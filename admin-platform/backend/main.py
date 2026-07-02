"""超级管理员系统入口。

独立 FastAPI 进程，端口 6689，独立鉴权体系。
与主应用共享同一个 PostgreSQL，但不共享登录态。
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from config import FRONTEND_ROOT, get_settings
from store import close_pool, ensure_default_admin, init_db, open_pool

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("admin-platform")
settings = get_settings()


class NoCacheStaticFiles(StaticFiles):
    """静态文件强制 no-cache，避免浏览器缓存旧版 CSS/JS。"""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if resp.status_code == 200:
            resp.headers["cache-control"] = "no-cache"
        return resp


def _startup() -> None:
    open_pool()
    init_db()
    ensure_default_admin()
    logger.info("admin-platform started on port %s", settings.admin_port)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _startup()
    try:
        yield
    finally:
        close_pool()


app = FastAPI(title="Platform Admin", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins) if settings.cors_origins
                   else ["https://localhost:8443"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionMiddleware, secret_key=settings.admin_secret_key)

# ── 挂载路由 ──
from routers.auth import router as auth_router  # noqa: E402
from routers.dashboard import router as dashboard_router  # noqa: E402
from routers.users import router as users_router  # noqa: E402
from routers.enterprises import router as enterprises_router  # noqa: E402
from routers.tasks import router as tasks_router  # noqa: E402
from routers.billing import router as billing_router  # noqa: E402
from routers.monitoring import router as monitoring_router  # noqa: E402
from routers.audit import router as audit_router  # noqa: E402

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(users_router)
app.include_router(enterprises_router)
app.include_router(tasks_router)
app.include_router(billing_router)
app.include_router(monitoring_router)
app.include_router(audit_router)

# ── 静态资源 ──
app.mount("/assets", NoCacheStaticFiles(directory=str(FRONTEND_ROOT / "assets")), name="assets")


@app.get("/", response_class=HTMLResponse)
def index_page() -> str:
    """登录页（未登录）自动跳 dashboard（已登录）。"""
    return (FRONTEND_ROOT / "index.html").read_text(encoding="utf-8")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page() -> str:
    return (FRONTEND_ROOT / "dashboard.html").read_text(encoding="utf-8")


@app.get("/api/health")
def health() -> dict[str, object]:
    return {"ok": True, "status": "healthy", "service": "admin-platform"}


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": str(exc.detail)})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=settings.admin_port,
        reload=settings.app_env != "production",
    )
