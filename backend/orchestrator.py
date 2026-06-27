from __future__ import annotations

import threading
import time
from typing import Any

from config import ENV_PATH
from pipeline import (
    load_env,
    step2_translate_titles,
    step3_analyze_vision,
    step4_generate_images,
    upload_source_image_urls_to_oss,
    upload_source_videos_to_oss,
)
import pipeline_queue
from store import (
    get_products_for_pipeline,
    get_raw_import,
    record_step,
    update_finished_at,
    update_raw_import,
    update_status,
    update_step2,
    update_step3_vision,
    update_step4,
    update_videos,
)


_env_cache: dict[str, str] = {}


def _load_env() -> dict[str, str]:
    if not _env_cache:
        _env_cache.update(load_env(ENV_PATH))
    return _env_cache


def _one_line_error(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.split("\nTraceback", 1)[0].strip()
    return text.splitlines()[0].strip() if "\n" in text else text


def _failure_payload(exc: Exception, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    detail = detail or {}
    payload = {"status": "failed", "error": _one_line_error(detail.get("last_error") or exc)}
    if detail:
        payload["detail"] = detail
    return payload


def _attach_old_image_urls(payload: dict[str, Any]) -> list[str]:
    product = payload.get("product", {}) or {}
    gallery = (product.get("galleryImages", []) or [])[:10]
    old_urls = upload_source_image_urls_to_oss(_load_env(), gallery) if gallery else []
    product["oldImageUrls"] = old_urls
    payload["product"] = product
    return old_urls


def attach_old_image_urls_for_import(user_id: int, import_id: int) -> list[str]:
    raw_import = get_raw_import(user_id, import_id)
    if not raw_import:
        return []
    product = raw_import.get("product", {}) or {}
    if product.get("oldImageUrls"):
        return product.get("oldImageUrls", [])[:10]
    old_urls = _attach_old_image_urls(raw_import)
    update_raw_import(user_id, import_id, raw_import)
    return old_urls


def run_auto_pipeline(user_id: int, import_id: int) -> None:
    """Enqueue an import for background processing.

    The actual work runs in the Redis-backed worker threads started at startup
    (see pipeline_queue). We never spawn a per-request thread here, so a
    process restart no longer strands a running job: the queued/generating
    imports are re-enqueued by startup recovery.
    """
    update_status(user_id, import_id, "queued", "waiting in queue")
    pipeline_queue.enqueue_pipeline(user_id, import_id)


def _execute_pipeline(user_id: int, import_id: int) -> None:
    """Run the full pipeline for one import. Called by the queue worker."""

    def _run_step2(env: dict[str, str], products: list[dict[str, Any]]) -> tuple[str | None, str | None]:
        started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        record_step(user_id, import_id, "step2_translate", "running", {
            "title": products[0].get("chinese_title", "") if products else "",
            "image_count": len(products[0].get("carousel_images", [])) if products else 0,
        }, started_at=started_at)
        try:
            results = step2_translate_titles(env, products)
            cn_new = results[0]["chinese_title"] if results else products[0].get("chinese_title", "")
            en_new = results[0]["english_title"] if results else ""
            update_step2(user_id, import_id, cn_new, en_new)
            record_step(user_id, import_id, "step2_translate", "success", {}, {
                "cn_title": cn_new,
                "en_title": en_new,
                "count": len(results),
            }, started_at=started_at)
            return cn_new, en_new
        except Exception as exc:
            update_step2(user_id, import_id, products[0].get("chinese_title", ""), "")
            record_step(user_id, import_id, "step2_translate", "failed", {}, {"status": "failed"}, error=_one_line_error(exc), started_at=started_at)
            return None, None

    def _run_step3(env: dict[str, str], products: list[dict[str, Any]]) -> dict[str, Any] | None:
        started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        record_step(user_id, import_id, "step3_vision", "running", {
            "title": products[0].get("chinese_title", "") if products else "",
            "image_count": len(products[0].get("carousel_images", [])) if products else 0,
        }, started_at=started_at)
        try:
            result = step3_analyze_vision(env, products)
            # _image_cache holds raw bytes for in-memory reuse by step4; it must
            # never reach update_step3_vision (bytes are not JSON serializable).
            image_cache = result.pop("_image_cache", None)
            update_step3_vision(user_id, import_id, result, done=True)
            record_step(user_id, import_id, "step3_vision", "success", {}, {
                "selected_indexes": result.get("selected_indexes", []),
                "prompt_count": len(result.get("prompt_items", [])),
                "attempt_count": len(result.get("attempts", [])),
                "elapsed": round(float(result.get("elapsed", 0)), 3),
                "meta_path": result.get("meta_path", ""),
            }, started_at=started_at)
            # re-attach only for the in-memory handoff to step4 in this same
            # pipeline run; it is never re-persisted.
            if image_cache is not None:
                result["_image_cache"] = image_cache
            return result
        except Exception as exc:
            failure = _failure_payload(exc, getattr(exc, "detail", {}))
            update_step3_vision(user_id, import_id, failure, done=False)
            record_step(user_id, import_id, "step3_vision", "failed", {}, failure, error=failure["error"], started_at=started_at)
            return None

    def _run_step4(env: dict[str, str], products: list[dict[str, Any]], vision_result: dict[str, Any]) -> list[dict[str, Any]] | None:
        started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        record_step(user_id, import_id, "step4_generation", "running", {
            "selected_indexes": vision_result.get("selected_indexes", []),
            "prompt_count": len(vision_result.get("prompt_items", [])),
        }, started_at=started_at)
        try:
            result = step4_generate_images(env, products, vision_result)
            generated = result.get("generated", [])
            update_step4(user_id, import_id, generated, done=True)
            record_step(user_id, import_id, "step4_generation", "success", {}, {
                "generation_stats": result.get("generation_stats", {}),
                "generated_count": sum(1 for item in generated if item.get("generated_image")),
                "failed_count": sum(1 for item in generated if item.get("error")),
                "meta_path": result.get("meta_path", ""),
            }, started_at=started_at)
            return generated
        except Exception as exc:
            failure = _failure_payload(exc, getattr(exc, "detail", {}))
            update_step4(user_id, import_id, [], done=False)
            record_step(user_id, import_id, "step4_generation", "failed", {}, failure, error=failure["error"], started_at=started_at)
            return None

    env = _load_env()
    try:
        update_status(user_id, import_id, "generating", "uploading source images")
        attach_old_image_urls_for_import(user_id, import_id)
    except Exception as exc:
        update_status(user_id, import_id, "error", f"source image upload failed: {_one_line_error(exc)}")
        return

    # Source videos are display-only and independent of AI image generation,
    # so upload failures must never break the pipeline. Best effort only.
    try:
        raw_import = get_raw_import(user_id, import_id) or {}
        source_videos = raw_import.get("videos", []) if isinstance(raw_import, dict) else []
        if source_videos:
            update_status(user_id, import_id, "generating", "uploading source videos")
            uploaded_videos = upload_source_videos_to_oss(env, source_videos)
            update_videos(user_id, import_id, uploaded_videos)
    except Exception as exc:
        update_status(user_id, import_id, "generating", f"video upload skipped: {_one_line_error(exc)}")

    products = get_products_for_pipeline(user_id, import_id)
    if not products:
        update_status(user_id, import_id, "error", "no product data")
        return

    raw_title = products[0].get("chinese_title", "")
    products_for_step3 = [{
        "row": products[0]["row"],
        "chinese_title": raw_title,
        "english_title": raw_title,
        "carousel_images": list(products[0].get("carousel_images", [])),
        "old_image_urls": list(products[0].get("old_image_urls", [])),
    }]
    products_for_step2 = [{
        "row": products[0]["row"],
        "chinese_title": raw_title,
        "carousel_images": list(products[0].get("carousel_images", [])),
        "old_image_urls": list(products[0].get("old_image_urls", [])),
    }]
    update_status(user_id, import_id, "generating", "translation and vision running")

    results: dict[str, Any] = {}

    def wrap_step2() -> None:
        cn, en = _run_step2(env, products_for_step2)
        results["step2_ok"] = cn is not None
        results["cn_title"] = cn
        results["en_title"] = en

    def wrap_step3() -> None:
        vision = _run_step3(env, products_for_step3)
        results["step3_ok"] = vision is not None
        results["vision"] = vision

    t2 = threading.Thread(target=wrap_step2, daemon=True)
    t3 = threading.Thread(target=wrap_step3, daemon=True)
    t2.start()
    t3.start()
    t2.join()
    t3.join()

    generated: list[dict[str, Any]] = []
    step4_ok = False
    if results.get("step3_ok"):
        update_status(user_id, import_id, "generating", "vision done, image generation running")
        step4_result = _run_step4(env, products_for_step3, results["vision"])
        step4_ok = step4_result is not None
        generated = step4_result or []

    ok_count = sum(1 for item in generated if item.get("generated_image"))
    fail_count = sum(1 for item in generated if item.get("error"))
    msg = f"success {ok_count}" + (f", failed {fail_count}" if fail_count else "")
    if not results.get("step2_ok"):
        msg = "translation failed; " + msg
    if not results.get("step3_ok"):
        msg = "vision failed; " + msg
    if results.get("step3_ok") and not step4_ok:
        msg = "image generation failed; " + msg
    update_status(user_id, import_id, "done" if results.get("step3_ok") and step4_ok else "error", msg)
    update_finished_at(user_id, import_id)
