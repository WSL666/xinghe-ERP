"""Temu 平台流水线编排:把采集到的商品跑完整 AI 生成流程。

这是 Temu 的"业务大脑",决定:
  先做什么 → 后做什么 → 调哪些 core 工具 → 用哪个 prompt

流程: 统一下载源图 → (step1上传OSS ‖ step2翻译 ‖ step3视觉)三路并行 → 生图 → 收尾
      (step1/2/3 三路并行,翻译结果不再被OSS上传阻塞,生图依赖视觉结果)

新增 1688 时,写自己的 pipeline.py:
  - 可复用 core 的工具(翻译/视觉/生图)
  - 步骤顺序、是否需要某步、用哪套 prompt 都可不同
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any

from core.base import PipelineStepError, log, require_env
from core.base import MAX_PARALLEL, PIPELINE_TOTAL_TIMEOUT
from core.images import collect_product_images
from core.oss import (
    upload_source_image_bytes_to_oss,
    upload_source_videos_to_oss,
)
from tools.translate import translate_titles
from tools.vision_analyze import analyze_product_with_retry
from tools.image_gen import generate_one_image, build_edit_image
from llm.base import ApiKeyError
from api_key_pool import get_pool
from concurrent.futures import ThreadPoolExecutor, as_completed

from schemas.product import Product, to_pipeline_input
from platforms.temu.adapter import parse_product
from platforms.temu.prompts import translate as translate_prompt
from platforms.temu.prompts import vision as vision_prompt


# ─────────────────────────────────────────────────────────
# 各步骤实现(从旧 orchestrator/steps 迁移,改用 Product + Temu prompt)
# ─────────────────────────────────────────────────────────

def _step2_translate(env: dict[str, str], product: Product) -> tuple[str, str]:
    """DeepSeek 标题翻译。返回 (cn_title, en_title)。"""
    log("=" * 50)
    log(">>> TEMU STEP2: 标题翻译")
    translated = translate_titles(env, [product.chinese_title], translate_prompt.PROMPT)
    item = translated[0]
    cn = item.get("cn_title") or item.get("chinese_title") or product.chinese_title
    en = item.get("en_title") or item.get("english_title") or ""
    log(f"翻译完成: cn={cn[:60]}... en={en[:60]}...")
    return cn, en


def _step3_vision(env: dict[str, str], product: Product,
                    image_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """视觉解析:下载图片 → 调视觉模型 → 选参考图 + 生成提示词。

    image_context 不为空时直接复用(已下载的字节),省掉重复下载。
    """
    log("=" * 50)
    log(">>> TEMU STEP3: 视觉解析")
    if image_context is None:
        image_context = collect_product_images([to_pipeline_input(product)])
    product_text = product.chinese_title
    prompt = vision_prompt.build_prompt(product_text)

    log(f"调视觉模型: {len(image_context['valid_b64'])} 张图")
    analysis = analyze_product_with_retry(
        env,
        prompt,
        image_context["valid_b64"],
        image_context["valid_images"],
    )
    result = {
        "step": "step3_vision",
        "payload": analysis["payload"],
        "selected_indexes": analysis["selected_indexes"],
        "prompt_items": [{"number": n, "prompt": p} for n, p in analysis["prompt_items"]],
        "image_context": {**image_context, "old_image_urls": product.old_image_urls}
                          if False else None,  # 不持久化大字段
        "attempts": analysis["attempts"],
        "_image_cache": {
            "image_bytes_list": image_context["image_bytes_list"],
            "valid_indices": image_context["valid_indices"],
            "total_input_images": image_context.get("total_input_images"),
            "valid_images": image_context.get("valid_images"),
        },
    }
    # 汇总用的小字段(不带字节)
    log(f"视觉完成: selected={analysis['selected_indexes']}, prompts={len(analysis['prompt_items'])}")
    return result


def _step4_generate(env: dict[str, str], product: Product, vision: dict[str, Any],
                      user_id: int = 0, import_id: int = 0, store: Any = None) -> list[dict[str, Any]]:
    """图生图:按视觉给的 prompt 调 VibeLearning,并行生成。"""
    import traceback as _tb

    log("=" * 50)
    log(">>> TEMU STEP4: 图片生成")
    vibe_api_key = require_env(env, "VIBE_API_KEY")
    vibe_base_url = require_env(env, "VIBE_BASE_URL")
    image_model = env.get("IMAGE_MODEL", "gpt-image-2")
    size = env.get("IMAGE_SIZE", "1024x1024")

    selected_indexes = [int(i) for i in vision.get("selected_indexes", [])]
    raw_prompt_items = vision.get("prompt_items", [])

    # 复用 step3 下载的图,无则重新下载
    cache = vision.get("_image_cache")
    if cache and cache.get("image_bytes_list") is not None:
        valid_indices = cache["valid_indices"]
        image_bytes_list = cache["image_bytes_list"]
        log(f"复用 step3 图片: {len(valid_indices)}/{len(image_bytes_list)}")
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
        raise PipelineStepError("视觉未选出参考图", {"selected_indexes": selected_indexes})
    if not prompt_items:
        raise PipelineStepError("视觉未生成提示词", {"prompt_count": 0})

    try:
        selected_ref_bytes = [image_bytes_list[valid_indices[int(idx) - 1]] for idx in selected_indexes]
    except Exception as exc:
        raise PipelineStepError(f"参考图索引映射失败: {exc}", {"selected_indexes": selected_indexes}) from exc

    edit_image = build_edit_image(selected_ref_bytes)

    # ── key 池轮换: 每个任务取 1 个 key, 所有图共用 ──
    # 单个 key 支持 10 并发, 6~8 张图用一个 key 足够。
    # key 坏了(401/403) → mark_failed → 换新 key 重试整个批次(最多 3 轮)。
    # 每张图超时重试 2 次(同一 key), 2 次都超时也换 key。
    pool = get_pool("vibe")
    fallback_key = vibe_api_key
    MAX_KEY_ROUNDS = 3

    for key_round in range(1, MAX_KEY_ROUNDS + 1):
        cur_key = pool.acquire() or fallback_key
        log(f"并行生成 {len(prompt_items)} 张图(最大 {MAX_PARALLEL} 并发, key_round={key_round}, key=...{cur_key[-6:]})")

        def _gen_one(task_idx: int, task_total: int, task_number: int, image_prompt: str) -> dict:
            task_name = f"image_{task_number}"
            log(f"[{task_idx}/{task_total}] {task_name} 开始")
            try:
                image_url, oss_result, elapsed, attempts = generate_one_image(
                    env=env, task_name=task_name, prompt=image_prompt,
                    api_key=cur_key, base_url=vibe_base_url,
                    edit_image=edit_image, size=size, model=image_model,
                )
                log(f"[{task_idx}/{task_total}] {task_name} OK ({elapsed:.1f}s)")
                return {
                    "image_type": task_name,
                    "generated_image": image_url,
                    "oss_object_key": oss_result.get("object_key", ""),
                    "prompt": image_prompt,
                    "error": None,
                    "elapsed": elapsed,
                }
            except Exception as exc:
                log(f"[{task_idx}/{task_total}] {task_name} FAILED: {exc}")
                return {
                    "image_type": task_name,
                    "generated_image": None,
                    "prompt": image_prompt,
                    "error": str(exc),
                    "elapsed": 0,
                }

        generated = []
        worker_count = min(MAX_PARALLEL, len(prompt_items))
        with ThreadPoolExecutor(max_workers=worker_count) as ex:
            futures = [ex.submit(_gen_one, i, len(prompt_items), n, p)
                       for i, (n, p) in enumerate(prompt_items, start=1)]
            for f in as_completed(futures):
                result = f.result()
                generated.append(result)
                # 每张图完成立即写库 → 前端轮询能实时看到图片逐张出现
                if result.get("generated_image"):
                    try:
                        store.append_generated_image(user_id, import_id, {
                            "image_type": result["image_type"],
                            "generated_image": result["generated_image"],
                        })
                    except Exception:
                        pass

        # 检查结果: 只分两种 — 有成功 / 全部失败
        has_key_error = any("key 失效" in (g.get("error") or "") or "401" in (g.get("error") or "") or "403" in (g.get("error") or "") for g in generated)
        has_timeout_error = any("exceeded" in (g.get("error") or "") or "timeout" in (g.get("error") or "").lower() for g in generated)
        all_failed = all(g.get("error") for g in generated)
        success_count = sum(1 for g in generated if g.get("generated_image"))

        # 有成功的图 → key 没问题, 直接返回(部分失败的图保留 error)
        if success_count > 0:
            if cur_key != fallback_key:
                pool.mark_success(cur_key)
            generated.sort(key=lambda r: int(r["image_type"].split("_")[1]) if "_" in r.get("image_type", "") else 0)
            log(f">>> STEP4 完成: 成功 {success_count}, "
                f"失败 {sum(1 for g in generated if g.get('error'))}")
            return generated

        # ── 全部失败(不管什么原因)→ 反馈 key + 换 key 重试, 最多 MAX_KEY_ROUNDS 轮 ──
        # 不再区分错误类型: 401/403=永久失效, 超时/400/其他=进冷却, 都换下一个 key 重试。
        if all_failed:
            if has_key_error:
                reason, code = "all images failed with 401/403", 401
            elif has_timeout_error:
                reason, code = "all images timeout", None
            else:
                reason, code = "all images failed (model error 400/5xx etc)", None
            log(f"[WARN] STEP4 全部失败(key=...{cur_key[-6:]}), reason={reason}, "
                f"换 key 重试({key_round}/{MAX_KEY_ROUNDS})")
            if cur_key != fallback_key:
                pool.mark_failed(cur_key, code, error=reason)
            if key_round < MAX_KEY_ROUNDS:
                continue
            # 已是最后一轮 → 彻底失败, 抛异常让上层标 error(不再误标 done)
            generated.sort(key=lambda r: int(r["image_type"].split("_")[1]) if "_" in r.get("image_type", "") else 0)
            raise PipelineStepError(
                f"image generation failed after {MAX_KEY_ROUNDS} key rounds: {reason}",
                {"key_rounds": key_round, "last_reason": reason})

    # 不应走到这里, 但防万一
    generated.sort(key=lambda r: int(r["image_type"].split("_")[1]) if "_" in r.get("image_type", "") else 0)
    return generated


# ─────────────────────────────────────────────────────────
# 编排入口:被 worker 调用
# ─────────────────────────────────────────────────────────

def execute(
    env: dict[str, str],
    user_id: int,
    import_id: int,
    store: Any,
) -> None:
    """执行 Temu 完整流水线。store 是数据访问层(避免循环依赖,运行时注入)。

    流程:
      1. 从 DB 读 raw_import → adapter 转成 Product
      2. 统一下载源图(只下一次,供 step1/step3 复用)
      3. (step1上传OSS ‖ step2翻译 ‖ step3视觉)三路并行
      4. 视觉完成后生图
      5. 写回 DB,更新状态
    """
    raw_import = store.get_raw_import(user_id, import_id)
    if not raw_import:
        return

    # AI 模块化: ai_features 是独立 DB 列, 不在 raw_json 里 → 用 get_import 读全行
    _full = store.get_import(user_id, import_id)
    ai_features = (_full.get("ai_features") if _full else None) or raw_import.get("ai_features") or []
    run_title = "title" in ai_features
    run_images = "images" in ai_features
    # 如果没选任何模块(不应该入队), 直接标 done 不跑
    if not run_title and not run_images:
        try:
            store.update_status(user_id, import_id, "done", "无AI模块")
        except Exception:
            pass
        return

    product = parse_product(raw_import)

    deadline = time.monotonic() + PIPELINE_TOTAL_TIMEOUT

    def _time_left() -> float:
        return max(0.0, deadline - time.monotonic())

    def _timed_out() -> bool:
        return time.monotonic() >= deadline

    # ── 统一下载一次: 采集到的 Temu 原图(后续 OSS 上传/视觉解析共用) ──
    # 旧版 step1 和 step3 各下载一次,白费一次网络往返。现在只下一次。
    # 但如果只跑 title(翻译)且没有图片, 跳过下载直接跑翻译。
    image_context: dict[str, Any] = {}
    has_images = bool(product.carousel_images)
    store.update_status(user_id, import_id, "generating", "AI处理中")
    if has_images:
        try:
            if _timed_out():
                raise TimeoutError(f"pipeline exceeded {PIPELINE_TOTAL_TIMEOUT:.0f}s before download")
            image_context = collect_product_images([to_pipeline_input(product)])
        except Exception as exc:
            store.update_status(user_id, import_id, "error", f"图片下载失败: {exc}")
            store.update_finished_at(user_id, import_id)
            return

    # ── 三路并行: step1(源图上传OSS) ‖ step2(翻译) ‖ step3(视觉) ──
    # 关键优化: 翻译和视觉不再被 step1(上传OSS ~100秒)挡着,三路同时启动。
    # 翻译本身只需 ~1 秒,改完前端能比旧版提前约 100 秒看到翻译结果。
    # 视觉复用上面已下载的字节(不重复下载),省 ~5-6 秒。
    store.update_status(user_id, import_id, "generating", "translation, vision and source upload running")
    results: dict[str, Any] = {}

    def _w1():
        """step1: 把已下载的图片字节传 OSS(存档用)。失败不阻断主流程。"""
        try:
            if not product.old_image_urls and image_context.get("image_bytes_list"):
                old_urls = upload_source_image_bytes_to_oss(env, image_context["image_bytes_list"])
                product.old_image_urls = old_urls
                raw_import.setdefault("product", {})["oldImageUrls"] = old_urls
                store.update_raw_import(user_id, import_id, raw_import)
        except Exception as exc:
            results["step1_error"] = str(exc)

    def _w1v():
        """源视频上传(失败不阻断)。"""
        try:
            if product.videos:
                uploaded = upload_source_videos_to_oss(env, product.videos)
                store.update_videos(user_id, import_id, uploaded)
        except Exception as exc:
            results["step1v_error"] = str(exc)

    def _w2():
        import datetime as _dt
        step_key, label = "step2_translate", "标题翻译"
        started = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            store.record_step(user_id, import_id, step_key, "running",
                              input_data={"title": product.chinese_title},
                              started_at=started, finished_at=started, label=label)
            cn, en = _step2_translate(env, product)
            results["step2"] = {"ok": True, "cn": cn, "en": en}
            store.update_step2(user_id, import_id, cn, en)
            store.record_step(user_id, import_id, step_key, "success",
                              output_data={"cn_title": cn, "en_title": en},
                              started_at=started, label=label)
        except Exception as exc:
            results["step2"] = {"ok": False, "error": str(exc)}
            store.update_step2(user_id, import_id, product.chinese_title, "")
            store.record_step(user_id, import_id, step_key, "failed",
                              error=str(exc), started_at=started, label=label)

    def _w3():
        import datetime as _dt
        step_key, label = "step3_vision", "视觉解析"
        started = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not image_context or not image_context.get("valid_b64"):
            results["step3"] = {"ok": False, "error": "no images for vision"}
            store.update_step3_vision(user_id, import_id, {"error": "no images"}, done=False)
            store.record_step(user_id, import_id, step_key, "failed", error="no images", started_at=started, label=label)
            return
        try:
            store.record_step(user_id, import_id, step_key, "running",
                              input_data={"carousel_count": len(product.carousel_images)},
                              started_at=started, finished_at=started, label=label)
            # 复用已下载的 image_context,不再重复下载
            vision = _step3_vision(env, product, image_context=image_context)
            results["step3"] = {"ok": True, "vision": vision}
            vision_for_db = {k: v for k, v in vision.items() if k != "_image_cache"}
            store.update_step3_vision(user_id, import_id, vision_for_db, done=True)
            # 视觉解析不在此单独扣费: 与图片生成合并, 在任务收尾统一扣一条流水(幂等)。
            store.record_step(user_id, import_id, step_key, "success",
                              output_data={"selected_indexes": vision.get("selected_indexes", []),
                                           "prompt_count": len(vision.get("prompt_items", [])),
                                           "attempts": len(vision.get("attempts", []))},
                              started_at=started, label=label)
        except Exception as exc:
            results["step3"] = {"ok": False, "error": str(exc)}
            store.update_step3_vision(user_id, import_id, {"error": str(exc)}, done=False)
            store.record_step(user_id, import_id, step_key, "failed",
                              error=str(exc), started_at=started, label=label)

    t1 = threading.Thread(target=_w1, daemon=True)
    t1v = threading.Thread(target=_w1v, daemon=True)
    threads_to_join = [t1, t1v]
    if run_title:
        t2 = threading.Thread(target=_w2, daemon=True)
        t2.start()
        threads_to_join.append(t2)
    if run_images:
        t3 = threading.Thread(target=_w3, daemon=True)
        t3.start()
        threads_to_join.append(t3)
    t1.start(); t1v.start()
    for t in threads_to_join:
        t.join(timeout=_time_left())
    # 检查超时: 只有选中的模块才算
    timed_out_step = False
    if run_title and t2.is_alive():
        timed_out_step = True
    if run_images and t3.is_alive():
        timed_out_step = True
    if timed_out_step:
        store.update_status(user_id, import_id, "error", f"AI exceeded {PIPELINE_TOTAL_TIMEOUT:.0f}s deadline")
        try:
            store.update_status(user_id, import_id, "error", "AI处理超时")
        except Exception:
            pass
        store.update_finished_at(user_id, import_id)
        return

    s2 = results.get("step2", {})
    s3 = results.get("step3", {})

    # 3. 生图(依赖视觉) — 仅 AI生图模块
    generated: list[dict[str, Any]] = []
    step4_ok = False
    if run_images and s3.get("ok"):
        vision = s3["vision"]
        store.update_status(user_id, import_id, "generating", "vision done, image generation running")
        # 清空旧图片(防止崩溃重跑时残留重复 append)
        store.update_step4(user_id, import_id, [], done=False)
        import datetime as _dt
        step_key, label = "step4_generation", "图片生成"
        started = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        store.record_step(user_id, import_id, step_key, "running",
                          input_data={"image_count": len(vision.get("prompt_items", []))},
                          started_at=started, finished_at=started, label=label)
        try:
            if _timed_out():
                raise TimeoutError(f"pipeline exceeded {PIPELINE_TOTAL_TIMEOUT:.0f}s deadline before image generation")
            generated = _step4_generate(env, product, vision, user_id=user_id, import_id=import_id, store=store)
            step4_ok = True
            store.update_step4(user_id, import_id, generated, done=True)
            ok_count = sum(1 for g in generated if g.get("generated_image"))
            fail_count = sum(1 for g in generated if g.get("error"))
            store.record_step(user_id, import_id, step_key, "success",
                              output_data={"generated": ok_count, "failed": fail_count,
                                           "images": [{"image_type": g.get("image_type"),
                                                       "ok": bool(g.get("generated_image"))}
                                                      for g in generated]},
                              started_at=started, label=label)
        except Exception as exc:
            # 保留 append 已写入的成功图(不清空 generated_json)。
            # 只标 step4_done=False + error, 用户已看到的图不会消失。
            store.update_status(user_id, import_id, "error", f"image generation failed: {exc}")
            store.record_step(user_id, import_id, step_key, "failed",
                              error=str(exc), started_at=started, label=label)
            store.update_finished_at(user_id, import_id)
            return

    # 4. 收尾
    ok_count = sum(1 for g in generated if g.get("generated_image"))
    fail_count = sum(1 for g in generated if g.get("error"))
    # done 判断: 只看选中的模块是否都成功
    title_ok = bool(s2.get("ok")) if run_title else True
    images_ok = bool(s3.get("ok")) and step4_ok if run_images else True
    done = title_ok and images_ok
    msg = f"success {ok_count}" + (f", failed {fail_count}" if fail_count else "")
    if run_title and not s2.get("ok"):
        msg = "translation failed; " + msg
    if run_images and not s3.get("ok"):
        msg = "vision failed; " + msg
    store.update_status(user_id, import_id, "done" if done else "error", msg)
    store.update_finished_at(user_id, import_id)
    # ── 结算计费(hold→settle/release, 一条链接一条流水) ──
    # hold 在入队时已冻结悲观上限; 这里按实际成功数结算, 多冻的退还。
    # 实际成本 = 视觉成功1 + 成功图数; 全失败则 release 不扣。
    try:
        from billing.store import settle_beans, release_beans, hold_amount_for
        hold_amount = hold_amount_for(ai_features)
        vision_ok = bool(s3.get("ok")) if run_images else False
        success_images = sum(1 for g in generated if g.get("generated_image")) if run_images else 0
        title_settle_ok = bool(s2.get("ok")) if run_title else False
        if title_settle_ok or vision_ok or success_images > 0:
            # 有任一项成功 → 结算(按实际成本扣, 多冻的退还)
            result = settle_beans(user_id, import_id, hold_amount,
                                  vision_ok, success_images, title_ok=title_settle_ok)
            if result:
                log(f"结算: user={user_id} import={import_id} "
                    f"视觉={'1' if vision_ok else '0'} 图{success_images} "
                    f"扣{result.get('charged', 0)} 余额={result['balance_after']}"
                    f"{' [dedup]' if result.get('dedup') else ''}")
            else:
                log(f"[WARN] 结算失败(无hold记录?): user={user_id} import={import_id}")
        else:
            # 视觉失败且无成功图 → 释放冻结(不扣)
            result = release_beans(user_id, import_id, hold_amount)
            if result:
                log(f"释放冻结(全失败不扣): user={user_id} import={import_id} "
                    f"余额={result['balance_after']}"
                    f"{' [dedup]' if result.get('dedup') else ''}")
    except Exception as exc:
        log(f"[WARN] 结算/释放异常: user={user_id} import={import_id} {exc}")
