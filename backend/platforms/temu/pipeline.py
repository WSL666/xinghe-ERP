"""Temu 平台流水线编排: 把采集到的商品跑完整 AI 生成流程。

流程: 下载源图 → (上传OSS ‖ 翻译 ‖ 多模态) 三路并行 → 生图 → 收尾
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from core.base import (
    MAX_PARALLEL,
    PIPELINE_TOTAL_TIMEOUT,
    PipelineStepError,
    log,
    set_trace_id,
)
from core.images import collect_product_images
from core.oss import (
    upload_source_image_bytes_to_oss,
    upload_source_videos_to_oss,
)
from tools.dispatch import run_translate, run_multimodal, run_image_gen
from tools.doubao_image import build_edit_image
from tools.tool_result import ToolResult

from schemas.product import Product, to_pipeline_input
from platforms.temu.adapter import parse_product
from platforms.temu.prompts import translate as translate_prompt
from platforms.temu.prompts import multimodal as multimodal_prompt


# ── 各步骤实现 (无 step 命名, 用业务语义) ──

def _translate(env: dict[str, str], product: Product) -> tuple[str, str]:
    """翻译: 调 dispatch → DeepSeek。返回 (cn_title, en_title)。"""
    log("=" * 50)
    log(">>> 翻译")
    r: ToolResult = run_translate(env, [product.chinese_title], translate_prompt.PROMPT)
    if not r.is_success:
        raise PipelineStepError(f"翻译失败: {r.error}", {"error_code": r.error_code})

    item = r.data["titles"][0]
    cn = item.get("cn_title") or item.get("chinese_title") or product.chinese_title
    en = item.get("en_title") or item.get("english_title") or ""
    log(f"翻译完成: cn={cn[:60]}... en={en[:60]}...")
    return cn, en


def _analyze(env: dict[str, str], product: Product,
             image_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """多模态解析: 调 dispatch → Qwen。返回 payload + selected_indexes + prompt_items。"""
    log("=" * 50)
    log(">>> 多模态解析")
    if image_context is None:
        image_context = collect_product_images([to_pipeline_input(product)])
    prompt = multimodal_prompt.build_prompt(product.chinese_title)

    log(f"调多模态模型: {len(image_context['valid_b64'])} 张图")
    r: ToolResult = run_multimodal(
        env, prompt, image_context["valid_b64"], image_context["valid_images"],
    )
    if not r.is_success:
        raise PipelineStepError(f"多模态失败: {r.error}", {"error_code": r.error_code})

    result = {
        "payload": r.data["payload"],
        "selected_indexes": r.data["selected_indexes"],
        "prompt_items": [{"number": n, "prompt": p} for n, p in r.data["prompt_items"]],
        "_image_cache": {
            "image_bytes_list": image_context["image_bytes_list"],
            "valid_indices": image_context["valid_indices"],
            "total_input_images": image_context.get("total_input_images"),
            "valid_images": image_context.get("valid_images"),
        },
    }
    log(f"多模态完成: selected={r.data['selected_indexes']}, prompts={len(r.data['prompt_items'])}")
    return result


def _generate(env: dict[str, str], product: Product, multimodal: dict[str, Any],
              user_id: int = 0, import_id: int = 0, store: Any = None) -> list[dict[str, Any]]:
    """生图: 调 dispatch → 豆包 Seedream, 并行生成。"""
    log("=" * 50)
    log(">>> 图片生成")

    size = env.get("IMAGE_SIZE", "1024x1024")
    selected_indexes = [int(i) for i in multimodal.get("selected_indexes", [])]
    raw_prompt_items = multimodal.get("prompt_items", [])

    cache = multimodal.get("_image_cache")
    if cache and cache.get("image_bytes_list") is not None:
        valid_indices = cache["valid_indices"]
        image_bytes_list = cache["image_bytes_list"]
        log(f"复用多模态下载的图: {len(valid_indices)}/{len(image_bytes_list)}")
    else:
        ctx = collect_product_images([to_pipeline_input(product)])
        valid_indices = ctx["valid_indices"]
        image_bytes_list = ctx["image_bytes_list"]

    prompt_items: list[tuple[int, str]] = []
    for item in raw_prompt_items:
        if isinstance(item, dict):
            prompt_items.append((int(item["number"]), str(item["prompt"])))
        else:
            prompt_items.append((int(item[0]), str(item[1])))

    if not selected_indexes:
        raise PipelineStepError("多模态未选出参考图", {"selected_indexes": selected_indexes})
    if not prompt_items:
        raise PipelineStepError("多模态未生成提示词", {"prompt_count": 0})

    try:
        selected_ref_bytes = [image_bytes_list[valid_indices[int(idx) - 1]] for idx in selected_indexes]
    except Exception as exc:
        raise PipelineStepError(f"参考图索引映射失败: {exc}", {"selected_indexes": selected_indexes}) from exc

    edit_image = build_edit_image(selected_ref_bytes)

    def _gen_one(task_idx: int, task_total: int, task_number: int, image_prompt: str) -> dict:
        task_name = f"image_{task_number}"
        log(f"[{task_idx}/{task_total}] {task_name} 开始")
        r: ToolResult = run_image_gen(env, task_name, image_prompt, edit_image, size)
        if r.is_success:
            log(f"[{task_idx}/{task_total}] {task_name} OK ({r.metadata.get('elapsed', 0):.1f}s)")
            return r.data
        else:
            log(f"[{task_idx}/{task_total}] {task_name} FAILED: {r.error}")
            return {
                "image_type": task_name,
                "generated_image": None,
                "prompt": image_prompt,
                "error": r.error,
            }

    generated: list[dict[str, Any]] = []
    worker_count = min(MAX_PARALLEL, len(prompt_items))
    with ThreadPoolExecutor(max_workers=worker_count) as ex:
        futures = [
            ex.submit(_gen_one, i, len(prompt_items), n, p)
            for i, (n, p) in enumerate(prompt_items, start=1)
        ]
        for f in as_completed(futures):
            result = f.result()
            generated.append(result)
            if result.get("generated_image") and store:
                try:
                    store.append_generated_image(user_id, import_id, {
                        "image_type": result["image_type"],
                        "generated_image": result["generated_image"],
                    })
                except Exception:
                    pass

    generated.sort(key=lambda r: int(r["image_type"].split("_")[1]) if "_" in r.get("image_type", "") else 0)
    ok = sum(1 for g in generated if g.get("generated_image"))
    fail = sum(1 for g in generated if g.get("error"))
    log(f">>> 生图完成: 成功 {ok}, 失败 {fail}")
    return generated


# ── 编排入口 ──

def execute(
    env: dict[str, str],
    user_id: int,
    import_id: int,
    store: Any,
) -> None:
    """执行 Temu 完整流水线。"""
    set_trace_id()
    log(f">>> pipeline 开始: user={user_id} import={import_id}")

    raw_import = store.get_raw_import(user_id, import_id)
    if not raw_import:
        log("raw_import 为空, 跳过")
        return

    _full = store.get_import(user_id, import_id)
    ai_features = (_full.get("ai_features") if _full else None) or raw_import.get("ai_features") or []
    run_title = "title" in ai_features
    run_images = "images" in ai_features
    if not run_title and not run_images:
        try:
            store.update_status(user_id, import_id, "done", "无AI模块")
        except Exception:
            pass
        return

    product = parse_product(raw_import)
    deadline = time.monotonic() + PIPELINE_TOTAL_TIMEOUT

    def _timed_out() -> bool:
        return time.monotonic() >= deadline

    # 统一下载源图
    image_context: dict[str, Any] = {}
    has_images = bool(product.carousel_images)
    store.update_status(user_id, import_id, "generating", "AI处理中")
    if has_images:
        try:
            if _timed_out():
                raise TimeoutError("超时")
            image_context = collect_product_images([to_pipeline_input(product)])
        except Exception as exc:
            store.update_status(user_id, import_id, "error", f"图片下载失败: {exc}")
            store.update_finished_at(user_id, import_id)
            return

    # 三路并行: 上传OSS ‖ 翻译 ‖ 多模态
    store.update_status(user_id, import_id, "generating", "翻译/多模态/上传并行中")
    results: dict[str, Any] = {}

    def _w_upload():
        try:
            if not product.old_image_urls and image_context.get("image_bytes_list"):
                old_urls = upload_source_image_bytes_to_oss(env, image_context["image_bytes_list"])
                product.old_image_urls = old_urls
                raw_import.setdefault("product", {})["oldImageUrls"] = old_urls
                store.update_raw_import(user_id, import_id, raw_import)
        except Exception as exc:
            results["upload_error"] = str(exc)

    def _w_upload_video():
        try:
            if product.videos:
                uploaded = upload_source_videos_to_oss(env, product.videos)
                store.update_videos(user_id, import_id, uploaded)
        except Exception as exc:
            results["upload_video_error"] = str(exc)

    def _w_translate():
        import datetime as _dt
        started = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            store.record_step(user_id, import_id, "translate", "running",
                              input_data={"title": product.chinese_title},
                              started_at=started, finished_at=started, label="标题翻译")
            cn, en = _translate(env, product)
            results["translate"] = {"ok": True, "cn": cn, "en": en}
            store.update_translate(user_id, import_id, cn, en)
            store.record_step(user_id, import_id, "translate", "success",
                              output_data={"cn_title": cn, "en_title": en},
                              started_at=started, label="标题翻译")
        except Exception as exc:
            results["translate"] = {"ok": False, "error": str(exc)}
            store.update_translate(user_id, import_id, product.chinese_title, "")
            store.record_step(user_id, import_id, "translate", "failed",
                              error=str(exc), started_at=started, label="标题翻译")

    def _w_analyze():
        import datetime as _dt
        started = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            store.record_step(user_id, import_id, "analyze", "running",
                              started_at=started, finished_at=started, label="多模态解析")
            analysis = _analyze(env, product, image_context=image_context) if has_images else None
            if analysis:
                results["analyze"] = {"ok": True, "analysis": analysis}
                analysis_for_db = {k: v for k, v in analysis.items() if k != "_image_cache"}
                store.update_analyze(user_id, import_id, analysis_for_db, done=True)
                store.record_step(user_id, import_id, "analyze", "success",
                                  output_data={"selected_indexes": analysis.get("selected_indexes", []),
                                               "prompt_count": len(analysis.get("prompt_items", []))},
                                  started_at=started, label="多模态解析")
            else:
                results["analyze"] = {"ok": False, "error": "no images for analyze"}
                store.update_analyze(user_id, import_id, {"error": "no images"}, done=False)
                store.record_step(user_id, import_id, "analyze", "failed",
                                  error="no images", started_at=started, label="多模态解析")
        except Exception as exc:
            results["analyze"] = {"ok": False, "error": str(exc)}
            store.update_analyze(user_id, import_id, {"error": str(exc)}, done=False)
            store.record_step(user_id, import_id, "analyze", "failed",
                              error=str(exc), started_at=started, label="多模态解析")

    # 运行并行任务
    workers: list = []
    workers.append(threading.Thread(target=_w_upload, daemon=True))
    workers.append(threading.Thread(target=_w_upload_video, daemon=True))
    if run_title:
        workers.append(threading.Thread(target=_w_translate, daemon=True))
    if run_images:
        workers.append(threading.Thread(target=_w_analyze, daemon=True))
    for w in workers:
        w.start()
    for w in workers:
        w.join()

    # 生图 (依赖多模态)
    import threading
    generated: list[dict[str, Any]] = []
    generate_ok = False
    s_analyze = results.get("analyze", {})
    if run_images and s_analyze.get("ok"):
        analysis = s_analyze["analysis"]
        store.update_status(user_id, import_id, "generating", "多模态完成, 生图中")
        store.update_generate(user_id, import_id, [], done=False)
        import datetime as _dt
        started = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        store.record_step(user_id, import_id, "generate", "running",
                          input_data={"image_count": len(analysis.get("prompt_items", []))},
                          started_at=started, finished_at=started, label="图片生成")
        try:
            if _timed_out():
                raise TimeoutError("超时")
            generated = _generate(env, product, analysis, user_id=user_id, import_id=import_id, store=store)
            generate_ok = True
            store.update_generate(user_id, import_id, generated, done=True)
            ok_count = sum(1 for g in generated if g.get("generated_image"))
            fail_count = sum(1 for g in generated if g.get("error"))
            store.record_step(user_id, import_id, "generate", "success",
                              output_data={"generated": ok_count, "failed": fail_count},
                              started_at=started, label="图片生成")
        except Exception as exc:
            store.update_status(user_id, import_id, "error", f"生图失败: {exc}")
            store.record_step(user_id, import_id, "generate", "failed",
                              error=str(exc), started_at=started, label="图片生成")
            store.update_finished_at(user_id, import_id)
            return

    # 收尾
    ok_count = sum(1 for g in generated if g.get("generated_image"))
    fail_count = sum(1 for g in generated if g.get("error"))
    s_translate = results.get("translate", {})
    title_ok = bool(s_translate.get("ok")) if run_title else True
    images_ok = bool(s_analyze.get("ok")) and generate_ok if run_images else True
    done = title_ok and images_ok
    msg = f"success {ok_count}" + (f", failed {fail_count}" if fail_count else "")
    if run_title and not s_translate.get("ok"):
        msg = "翻译失败; " + msg
    if run_images and not s_analyze.get("ok"):
        msg = "多模态失败; " + msg
    store.update_status(user_id, import_id, "done" if done else "error", msg)
    store.update_finished_at(user_id, import_id)

    # 计费结算
    try:
        from billing.store import settle_beans, release_beans, hold_amount_for
        hold_amount = hold_amount_for(ai_features)
        multimodal_ok = bool(s_analyze.get("ok")) if run_images else False
        success_images = sum(1 for g in generated if g.get("generated_image")) if run_images else 0
        title_settle_ok = bool(s_translate.get("ok")) if run_title else False
        if title_settle_ok or multimodal_ok or success_images > 0:
            result = settle_beans(user_id, import_id, hold_amount,
                                  multimodal_ok, success_images, title_ok=title_settle_ok)
            if result:
                log(f"结算: user={user_id} import={import_id} "
                    f"扣{result.get('charged', 0)} 余额={result['balance_after']}")
        else:
            result = release_beans(user_id, import_id, hold_amount)
            if result:
                log(f"释放冻结(全失败不扣): user={user_id} import={import_id} "
                    f"余额={result['balance_after']}")
    except Exception as exc:
        log(f"[WARN] 结算异常: user={user_id} import={import_id} {exc}")

    log(f">>> pipeline 完成: user={user_id} import={import_id} done={done}")
