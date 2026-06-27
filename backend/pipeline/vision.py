from __future__ import annotations

import json
import re
import time
from typing import Any
import httpx
from openai import APITimeoutError, OpenAI

from ._base import PipelineStepError, VISION_MAX_ATTEMPTS, VISION_TIMEOUT, log, parse_json_response, require_env


def build_vision_messages(prompt: str, image_b64_list: list[str]) -> list[dict[str, Any]]:
    """Build OpenAI SDK multimodal messages."""
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for b64_url in image_b64_list:
        content.append({
            "type": "image_url",
            "image_url": {"url": b64_url},
        })
    return [{"role": "user", "content": content}]


def analyze_product(env: dict[str, str], prompt: str,
                    image_b64_list: list[str]) -> dict[str, Any]:
    """Call the Vision model to analyze all images in one shot -> output JSON."""
    import traceback as _tb
    chat_api_key = require_env(env, "CHAT_API_KEY")
    chat_base_url = require_env(env, "OPENAI_CHAT_BASE_URL")
    chat_model = env.get("CHAT_MODEL", "gpt-5.5")

    if chat_base_url.endswith("/chat/completions"):
        sdk_base = chat_base_url[:-len("/chat/completions")]
    else:
        sdk_base = chat_base_url.rstrip("/")

    log(f"Vision: base={sdk_base}, model={chat_model}, key=...{chat_api_key[-8:]}, images={len(image_b64_list)}")

    client = OpenAI(
        base_url=sdk_base,
        api_key=chat_api_key,
        default_headers={"User-Agent": "python-httpx/0.28.1"},
    )
    try:
        stream = client.chat.completions.create(
            model=chat_model,
            messages=build_vision_messages(prompt, image_b64_list),
            stream=True,
            stream_options={"include_usage": True},
            timeout=httpx.Timeout(VISION_TIMEOUT, connect=30.0),
        )
        content_parts: list[str] = []
        usage = None
        for chunk in stream:
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue
            delta = getattr(choices[0], "delta", None)
            delta_content = getattr(delta, "content", None) if delta is not None else None
            if delta_content:
                content_parts.append(delta_content)
    except Exception as exc:
        log(f"Vision API call exception: {exc}")
        log(f"Traceback: {_tb.format_exc()}")
        raise RuntimeError(f"Vision API call failed: {exc}") from exc

    content = "".join(content_parts).strip()
    if not content:
        raise RuntimeError("Empty vision stream response")
    if usage:
        log(f"Vision usage: {usage}")
    log(f"Vision raw response (first 500 chars): {content[:500]}")
    return parse_json_response(content)


def validate_analysis_payload(payload: dict[str, Any],
                               image_count: int) -> tuple[list[int], list[tuple[int, str]]]:
    """Validate Vision result."""
    raw_indexes = payload.get("selected_reference_image_indexes")
    if not isinstance(raw_indexes, list):
        raise ValueError("selected_reference_image_indexes must be a list")

    indexes: list[int] = []
    for item in raw_indexes:
        if isinstance(item, bool):
            raise ValueError("selected_reference_image_indexes cannot contain booleans")
        try:
            index = int(item)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid reference image index: {item!r}") from exc
        indexes.append(index)

    if len(indexes) < 2 or len(indexes) > 3:
        raise ValueError("selected_reference_image_indexes must contain 2 to 3 indexes")
    if len(set(indexes)) != len(indexes):
        raise ValueError("selected_reference_image_indexes contains duplicate indexes")
    invalid = [index for index in indexes if index < 1 or index > image_count]
    if invalid:
        raise ValueError(f"Reference image indexes out of range 1..{image_count}: {invalid}")

    prompt_items: list[tuple[int, str]] = []
    for key, value in payload.items():
        match = re.fullmatch(r"image_(\d+)", key)
        if not match:
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty string prompt")
        prompt_items.append((int(match.group(1)), value.strip()))

    prompt_items.sort(key=lambda item: item[0])
    prompt_numbers = [number for number, _ in prompt_items]
    if len(prompt_items) < 6 or len(prompt_items) > 8:
        raise ValueError(f"Expected 6 to 8 image_N prompts, got {len(prompt_items)}")
    if prompt_numbers != list(range(1, len(prompt_items) + 1)):
        raise ValueError(f"image_N keys must be continuous from image_1: {prompt_numbers}")

    return indexes, prompt_items


def analyze_product_with_retry(
    env: dict[str, str],
    vision_prompt: str,
    valid_b64: list[str],
    image_count: int,
    max_attempts: int = VISION_MAX_ATTEMPTS,
) -> dict[str, Any]:
    """Vision analysis with retry; validates JSON schema each attempt."""
    attempts: list[dict[str, Any]] = []
    last_error = ""

    for attempt in range(1, max_attempts + 1):
        started = time.perf_counter()
        attempt_info: dict[str, Any] = {
            "attempt": attempt,
            "ok": False,
            "elapsed": 0,
            "error": "",
            "payload_preview": "",
        }
        try:
            payload = analyze_product(env, vision_prompt, valid_b64)
            attempt_info["payload_preview"] = json.dumps(payload, ensure_ascii=False)[:1000]
            selected_indexes, prompt_items = validate_analysis_payload(payload, image_count)
            attempt_info.update({
                "ok": True,
                "elapsed": round(time.perf_counter() - started, 3),
                "selected_indexes": selected_indexes,
                "prompt_count": len(prompt_items),
            })
            attempts.append(attempt_info)
            return {
                "payload": payload,
                "selected_indexes": selected_indexes,
                "prompt_items": prompt_items,
                "attempts": attempts,
            }
        except Exception as exc:
            last_error = str(exc)
            attempt_info.update({
                "elapsed": round(time.perf_counter() - started, 3),
                "error": last_error,
            })
            attempts.append(attempt_info)
            log(f"[WARN] Vision attempt {attempt}/{max_attempts} failed: {exc}")
            if attempt < max_attempts:
                time.sleep(min(2 * attempt, 6))

    raise PipelineStepError(f"Vision analysis failed after {max_attempts} retries: {last_error}", {
        "attempts": attempts,
        "last_error": last_error,
    })
