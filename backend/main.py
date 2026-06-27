from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from contextlib import asynccontextmanager
from starlette.middleware.sessions import SessionMiddleware

class NoCacheStaticFiles(StaticFiles):
    """StaticFiles subclass that sends Cache-Control: no-cache on every file.

    Plain StaticFiles omits Cache-Control entirely, so browsers fall back to
    heuristic caching. Edge caches text/css aggressively this way and then
    serves a stale stylesheet after we edit it, while Chrome happens to
    revalidate. Forcing no-cache makes every browser re-check the ETag so the
    newest file is always served. It costs a cheap 304 round-trip, not a
    re-download, since ETag/Last-Modified still match.
    """

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if resp.status_code == 200:
            resp.headers["cache-control"] = "no-cache"
        return resp

from config import BACKEND_ROOT, FRONTEND_ROOT, get_settings
from security import create_session_token, load_session_token, validate_account, verify_password
from store import (
    create_user,
    delete_import,
    close_pool,
    open_pool,
    get_import,
    get_or_create_dev_user,
    get_user_by_api_key,
    list_resumable_imports,
    get_products_for_pipeline,
    get_raw_import,
    get_user_by_account,
    get_user_by_id,
    init_db,
    insert_import,
    list_imports,
    public_user,
    record_step,
    reset_user_api_key,
    update_status,
    update_step2,
    update_step3_vision,
    update_step4,
    create_enterprise_with_owner,
    get_enterprise_by_id,
    get_enterprise_context_for_user,
    join_enterprise_by_invite,
    list_enterprise_members,
    regenerate_invite_code,
    remove_enterprise_member,
    update_member_role,
)
from pipeline import (
    OUTPUT_DIR,
    export_to_xlsx,
    step1_read_xlsx,
    step2_translate_titles,
    step3_analyze_vision,
    step4_generate_images,
)
import pipeline_queue
from orchestrator import (
    _execute_pipeline,
    _failure_payload,
    _load_env,
    _one_line_error,
    run_auto_pipeline,
)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


settings = get_settings()
@asynccontextmanager
async def lifespan(_app: FastAPI):
    _startup()
    try:
        yield
    finally:
        close_pool()
        pipeline_queue.stop_workers()


app = FastAPI(title="Product Pipeline Digital App", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins) if settings.cors_origins else ["http://localhost:6688", "http://127.0.0.1:6688"],
    allow_origin_regex=r"chrome-extension://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

# Static asset mounts: serve the real files at fixed absolute URLs so the HTML
# can reference them directly, with no runtime string rewriting required.
app.mount("/frontend", NoCacheStaticFiles(directory=str(FRONTEND_ROOT)), name="frontend")
app.mount(
    "/dashboard/assets",
    NoCacheStaticFiles(directory=str(FRONTEND_ROOT / "dashboard" / "assets")),
    name="dashboard-assets",
)


class RegisterPayload(BaseModel):
    account: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)
    display_name: str = ""
    invite_code: str = ""


class LoginPayload(BaseModel):
    account: str
    password: str


class OnboardPayload(BaseModel):
    """Enterprise onboarding: creates the company + owner account in one go."""
    enterprise_name: str = Field(..., min_length=2)
    contact_name: str = ""
    contact_phone: str = ""
    account: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)
    display_name: str = ""


class MemberRolePayload(BaseModel):
    role: str


def api_ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


def api_error(message: str, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"ok": False, "error": message})


def attach_session(response: Response, user_id: int) -> None:
    response.set_cookie(
        settings.session_cookie_name,
        create_session_token(user_id),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_max_age_seconds,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(settings.session_cookie_name, path="/")


async def current_user(request: Request) -> dict[str, Any]:
    token = request.cookies.get(settings.session_cookie_name)
    session_data = load_session_token(token)
    if not session_data:
        raise api_error("not authenticated", 401)
    user = get_user_by_id(int(session_data["uid"]))
    if not user or not user.get("is_active"):
        raise api_error("not authenticated", 401)
    return user


async def plugin_user(request: Request) -> dict[str, Any]:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise api_error("missing plugin api key", 401)
    user = get_user_by_api_key(token.strip())
    if not user:
        raise api_error("plugin api key is invalid", 401)
    if not user.get("is_active"):
        raise api_error("account disabled", 403)
    return user


@app.on_event("startup")
def _startup() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (BACKEND_ROOT / "uploads").mkdir(parents=True, exist_ok=True)
    open_pool()
    init_db()
    if settings.app_env != "production":
        get_or_create_dev_user()
    # Crash recovery: re-enqueue imports that were queued or mid-generation
    # when the previous process died, so they never strand in a bad status.
    resumed = list_resumable_imports()
    for row in resumed:
        pipeline_queue.enqueue_pipeline(int(row["user_id"]), int(row["id"]))
    # Consumer threads live in-process; they pull from the Redis queue.
    pipeline_queue.start_workers(_execute_pipeline)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": str(exc.detail)})


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (FRONTEND_ROOT / "index.html").read_text(encoding="utf-8")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> str:
    return (FRONTEND_ROOT / "dashboard" / "index.html").read_text(encoding="utf-8")


@app.post("/api/auth/register")
def register(payload: RegisterPayload, response: Response) -> dict[str, Any]:
    try:
        account = validate_account(payload.account)
    except ValueError as exc:
        raise api_error(str(exc), 400)
    if get_user_by_account(account):
        raise api_error("account already exists", 409)
    try:
        user = create_user(account=account, password=payload.password, display_name=payload.display_name)
    except ValueError as exc:
        raise api_error(str(exc), 400)
    # attach_session / public_user consume the create_user result, which carries
    # the plaintext api_key (the DB only stores its hash). When an invite code
    # is provided, join it after creation and copy the freshly-assigned
    # role/enterprise_id back onto that same dict so the response is accurate
    # without losing the api_key.
    enterprise = None
    if payload.invite_code:
        enterprise = join_enterprise_by_invite(payload.invite_code, int(user["id"]))
        if enterprise is None:
            raise api_error("invalid invite code", 400)
        fresh = get_user_by_id(int(user["id"])) or {}
        user["role"] = fresh.get("role", "member")
        user["enterprise_id"] = fresh.get("enterprise_id")
    attach_session(response, int(user["id"]))
    return api_ok(user=public_user(user), enterprise=enterprise)


@app.post("/api/auth/login")
def login(payload: LoginPayload, response: Response) -> dict[str, Any]:
    account = payload.account.strip().lower()
    user = get_user_by_account(account)
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise api_error("account or password is incorrect", 401)
    if not user.get("is_active"):
        raise api_error("account disabled", 403)
    attach_session(response, int(user["id"]))
    return api_ok(user=public_user(user))


@app.post("/api/auth/logout")
def logout(response: Response) -> dict[str, Any]:
    clear_session(response)
    return api_ok()


@app.get("/api/auth/me")
def auth_me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    # Fold the enterprise context (id/name/role/invite_code) into the user
    # payload so the frontend can decide which views and nav entries to show.
    enterprise = get_enterprise_context_for_user(int(user["id"]))
    data = public_user(user)
    if enterprise:
        data["enterprise"] = enterprise
    return api_ok(user=data)


@app.post("/api/auth/api-key/reset")
def reset_api_key(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    result = reset_user_api_key(int(user["id"]))
    return api_ok(user=public_user(result["user"]), api_key=result["api_key"])


async def require_enterprise_admin(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Resolve the caller's enterprise and enforce owner/admin rights."""
    ctx = get_enterprise_context_for_user(int(user["id"]))
    if not ctx or ctx.get("role") not in {"owner", "admin"}:
        raise api_error("enterprise admin only", 403)
    user["enterprise"] = ctx
    return user


@app.post("/api/enterprise/onboard")
def api_enterprise_onboard(payload: OnboardPayload, response: Response) -> dict[str, Any]:
    try:
        enterprise, user = create_enterprise_with_owner(
            name=payload.enterprise_name,
            contact_name=payload.contact_name,
            contact_phone=payload.contact_phone,
            account=payload.account,
            password=payload.password,
            display_name=payload.display_name,
        )
    except ValueError as exc:
        raise api_error(str(exc), 400)
    attach_session(response, int(user["id"]))
    return api_ok(enterprise=enterprise, user=public_user(user))


@app.get("/api/enterprise/me")
def api_enterprise_me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    ctx = get_enterprise_context_for_user(int(user["id"]))
    if not ctx:
        raise api_error("no enterprise", 404)
    return api_ok(enterprise=ctx)


@app.get("/api/enterprise/members")
def api_enterprise_members(user: dict[str, Any] = Depends(require_enterprise_admin)) -> dict[str, Any]:
    enterprise_id = int(user["enterprise"]["id"])
    return api_ok(
        enterprise=get_enterprise_by_id(enterprise_id),
        members=list_enterprise_members(enterprise_id),
    )


@app.post("/api/enterprise/invite/regenerate")
def api_enterprise_regenerate_invite(user: dict[str, Any] = Depends(require_enterprise_admin)) -> dict[str, Any]:
    if user["enterprise"]["role"] != "owner":
        raise api_error("only owner can regenerate invite code", 403)
    code = regenerate_invite_code(int(user["enterprise"]["id"]))
    return api_ok(invite_code=code)


@app.post("/api/enterprise/members/{member_id}/role")
def api_enterprise_member_role(
    member_id: int,
    payload: MemberRolePayload,
    user: dict[str, Any] = Depends(require_enterprise_admin),
) -> dict[str, Any]:
    try:
        updated = update_member_role(int(user["enterprise"]["id"]), member_id, payload.role)
    except ValueError as exc:
        raise api_error(str(exc), 400)
    if not updated:
        raise api_error("member not found", 404)
    return api_ok()


@app.delete("/api/enterprise/members/{member_id}")
def api_enterprise_remove_member(
    member_id: int,
    user: dict[str, Any] = Depends(require_enterprise_admin),
) -> dict[str, Any]:
    if not remove_enterprise_member(int(user["enterprise"]["id"]), member_id):
        raise api_error("member not found", 404)
    return api_ok()


@app.get("/enterprise", response_class=HTMLResponse)
def enterprise_page() -> str:
    return (FRONTEND_ROOT / "enterprise.html").read_text(encoding="utf-8")


@app.get("/onboard", response_class=HTMLResponse)
def onboard_page() -> str:
    return (FRONTEND_ROOT / "onboard.html").read_text(encoding="utf-8")


def create_import(payload: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    product_data = payload.get("product")
    skus = payload.get("skus")
    if not product_data or not skus:
        raise api_error("missing product or skus", 400)
    try:
        import_id = insert_import(int(user["id"]), payload)
    except Exception as exc:
        raise api_error(str(exc), 500)
    run_auto_pipeline(int(user["id"]), import_id)
    return api_ok(
        import_id=import_id,
        title=product_data.get("title", ""),
        sku_count=len(skus),
        total_images=len((product_data.get("galleryImages", []) or [])[:10]),
        old_image_urls=[],
        status="queued",
    )


@app.post("/api/import")
def api_import(payload: dict[str, Any], user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return create_import(payload, user)


@app.post("/api/plugin/import")
def api_plugin_import(payload: dict[str, Any], user: dict[str, Any] = Depends(plugin_user)) -> dict[str, Any]:
    return create_import(payload, user)


@app.post("/api/step1/upload")
async def api_step1_upload(file: UploadFile = File(...), user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise api_error("only .xlsx files are supported", 400)
    upload_dir = BACKEND_ROOT / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{int(time.time())}_{Path(file.filename).name}"
    upload_path = upload_dir / safe_name
    upload_path.write_bytes(await file.read())

    try:
        products = step1_read_xlsx(str(upload_path))
    except Exception as exc:
        raise api_error(f"read xlsx failed: {exc}", 500)

    first_prod = products[0] if products else {}
    gallery = first_prod.get("carousel_images", [])[:10]
    raw_import = {
        "shopConfig": {},
        "goodsId": "",
        "categoryId": "",
        "videoUrl": "",
        "createdAt": "",
        "product": {
            "title": first_prod.get("chinese_title", ""),
            "galleryImages": gallery,
            "firstImage": gallery[0] if gallery else "",
            "productProps": [],
        },
        "skus": [{
            "variantName": "default",
            "specName1": "",
            "specValue1": "",
            "specName2": "",
            "specValue2": "",
            "previewImage": gallery[0] if gallery else "",
            "price": "",
            "stock": 0,
            "skcProps": "[]",
            "skuProps": "[]",
            "spuId": "",
            "skcId": "",
            "skuId": "",
        }],
    }

    try:
        import_id = insert_import(int(user["id"]), raw_import)
    except Exception as exc:
        raise api_error(str(exc), 500)
    run_auto_pipeline(int(user["id"]), import_id)
    return api_ok(import_id=import_id, products=products, count=len(products), total_images=len(gallery), old_image_urls=[], status="queued")


@app.get("/api/imports")
def api_list_imports(platform: str | None = None, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    # ?platform=1688 只返回该平台；不传则返回全部
    pf = platform.strip().lower() if platform else None
    return api_ok(imports=list_imports(int(user["id"]), pf))


@app.get("/api/imports/{import_id}")
def api_get_import(import_id: int, request: Request, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    compact = request.query_params.get("full") not in {"1", "true", "yes"}
    row = get_import(int(user["id"]), import_id, compact=compact)
    if not row:
        raise api_error(f"import {import_id} not found", 404)
    return {"ok": True, "import": row}


@app.delete("/api/imports/{import_id}")
def api_delete_import(import_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not delete_import(int(user["id"]), import_id):
        raise api_error(f"import {import_id} not found", 404)
    return api_ok()


@app.post("/api/imports/{import_id}/step2")
def api_step2_translate(import_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    products = get_products_for_pipeline(int(user["id"]), import_id)
    if not products:
        raise api_error(f"import {import_id} not found", 404)
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    record_step(int(user["id"]), import_id, "step2_translate", "running", {"manual": True}, started_at=started_at)
    try:
        results = step2_translate_titles(_load_env(), products)
        cn_title = results[0]["chinese_title"] if results else ""
        en_title = results[0]["english_title"] if results else ""
        update_step2(int(user["id"]), import_id, cn_title, en_title)
        record_step(int(user["id"]), import_id, "step2_translate", "success", {"manual": True}, {
            "cn_title": cn_title,
            "en_title": en_title,
            "count": len(results),
        }, started_at=started_at)
    except Exception as exc:
        record_step(int(user["id"]), import_id, "step2_translate", "failed", {"manual": True}, error=_one_line_error(exc), started_at=started_at)
        raise api_error(f"translation failed: {exc}", 500)
    return api_ok(titles=[{"chinese_title": item["chinese_title"], "english_title": item["english_title"]} for item in results])


@app.post("/api/imports/{import_id}/step3")
def api_step3_vision(import_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    products = get_products_for_pipeline(int(user["id"]), import_id)
    row = get_import(int(user["id"]), import_id)
    if not products or not row:
        raise api_error(f"import {import_id} not found", 404)
    if row.get("cn_title"):
        products[0]["chinese_title"] = row["cn_title"]
        products[0]["english_title"] = row.get("en_title", "")
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    record_step(int(user["id"]), import_id, "step3_vision", "running", {"manual": True}, started_at=started_at)
    try:
        result = step3_analyze_vision(_load_env(), products)
        # _image_cache bytes are not JSON serializable; the manual step4 path
        # reads vision back from the DB and re-downloads via the fallback.
        result.pop("_image_cache", None)
        update_step3_vision(int(user["id"]), import_id, result, done=True)
        record_step(int(user["id"]), import_id, "step3_vision", "success", {"manual": True}, {
            "selected_indexes": result.get("selected_indexes", []),
            "prompt_count": len(result.get("prompt_items", [])),
            "attempt_count": len(result.get("attempts", [])),
            "elapsed": round(float(result.get("elapsed", 0)), 3),
            "meta_path": result.get("meta_path", ""),
        }, started_at=started_at)
        update_status(int(user["id"]), import_id, "generating", "vision done")
    except Exception as exc:
        failure = _failure_payload(exc, getattr(exc, "detail", {}))
        update_step3_vision(int(user["id"]), import_id, failure, done=False)
        record_step(int(user["id"]), import_id, "step3_vision", "failed", {"manual": True}, failure, error=failure["error"], started_at=started_at)
        raise api_error(f"vision failed: {exc}", 500)
    return api_ok(vision=result)


@app.post("/api/imports/{import_id}/step4")
def api_step4_generate(import_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    products = get_products_for_pipeline(int(user["id"]), import_id)
    row = get_import(int(user["id"]), import_id)
    if not products or not row:
        raise api_error(f"import {import_id} not found", 404)
    if row.get("cn_title"):
        products[0]["chinese_title"] = row["cn_title"]
        products[0]["english_title"] = row.get("en_title", "")
    vision_result = row.get("vision_json", {})
    if not vision_result or vision_result.get("error"):
        raise api_error("run vision first", 400)
    started_at = time.strftime("%Y-%m-%d %H:%M:%S")
    record_step(int(user["id"]), import_id, "step4_generation", "running", {"manual": True}, started_at=started_at)
    try:
        result = step4_generate_images(_load_env(), products, vision_result)
        generated = result.get("generated", [])
        update_step4(int(user["id"]), import_id, generated, done=True)
        ok_count = sum(1 for item in generated if item.get("generated_image"))
        fail_count = sum(1 for item in generated if item.get("error"))
        record_step(int(user["id"]), import_id, "step4_generation", "success", {"manual": True}, {
            "generation_stats": result.get("generation_stats", {}),
            "generated_count": ok_count,
            "failed_count": fail_count,
            "meta_path": result.get("meta_path", ""),
        }, started_at=started_at)
        update_status(int(user["id"]), import_id, "done", f"success {ok_count}" + (f", failed {fail_count}" if fail_count else ""))
    except Exception as exc:
        failure = _failure_payload(exc, getattr(exc, "detail", {}))
        update_step4(int(user["id"]), import_id, [], done=False)
        record_step(int(user["id"]), import_id, "step4_generation", "failed", {"manual": True}, failure, error=failure["error"], started_at=started_at)
        update_status(int(user["id"]), import_id, "error", f"image generation failed: {exc}")
        raise api_error(f"image generation failed: {exc}", 500)
    return api_ok(generated=generated, generation_stats=result.get("generation_stats", {}))


@app.post("/api/imports/{import_id}/generate")
def api_generate_full(import_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    if not get_import(int(user["id"]), import_id):
        raise api_error(f"import {import_id} not found", 404)
    run_auto_pipeline(int(user["id"]), import_id)
    return api_ok(import_id=import_id, status="queued")


@app.post("/api/imports/{import_id}/export")
def api_export(import_id: int, user: dict[str, Any] = Depends(current_user)) -> FileResponse:
    raw_import = get_raw_import(int(user["id"]), import_id)
    if not raw_import:
        raise api_error(f"import {import_id} not found", 404)
    row = get_import(int(user["id"]), import_id)
    products = [{
        "row": 2,
        "chinese_title": row.get("cn_title", "") or raw_import.get("product", {}).get("title", ""),
        "english_title": row.get("en_title", ""),
        "carousel_images": raw_import.get("product", {}).get("galleryImages", []),
        "generated": row.get("generated_json", []) if isinstance(row.get("generated_json"), list) else [],
    }]
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUTPUT_DIR / f"final_result_{int(user['id'])}_{import_id}.xlsx"
        export_to_xlsx(raw_import, products, str(out_path))
    except Exception as exc:
        raise api_error(f"export failed: {exc}", 500)
    return FileResponse(
        str(out_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=out_path.name,
    )


@app.get("/api/download/{filename:path}")
def api_download(filename: str) -> FileResponse:
    filepath = OUTPUT_DIR / "generated" / filename
    if not filepath.exists():
        raise api_error("file not found", 404)
    return FileResponse(str(filepath), filename=Path(filename).name)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return api_ok(status="healthy")
