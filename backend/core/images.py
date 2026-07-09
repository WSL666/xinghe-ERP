from __future__ import annotations

import base64
import hashlib
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from core.base import IMAGE_DOWNLOAD_CONCURRENCY, PipelineStepError, log


def download_bytes(url: str) -> tuple[bytes, str]:
    """Download an image, return (bytes, mime_type)."""
    req = urllib.request.Request(url, method="GET", headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        img_bytes = resp.read()
    mime = resp.headers.get("Content-Type", "image/jpeg")
    if ";" in mime:
        mime = mime.split(";")[0]
    return img_bytes, mime


def bytes_to_data_uri(img_bytes: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(img_bytes).decode('ascii')}"


def guess_mime_bytes(img_bytes: bytes) -> str:
    """Sniff MIME type from file header."""
    if img_bytes[:4] == b'\x89PNG':
        return "image/png"
    if img_bytes[:2] == b'\xff\xd8':
        return "image/jpeg"
    if img_bytes[:4] == b'RIFF' and img_bytes[8:12] == b'WEBP':
        return "image/webp"
    return "image/jpeg"


def encode_image_data_url(img_bytes: bytes) -> str:
    mime = guess_mime_bytes(img_bytes)
    encoded = base64.standard_b64encode(img_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def image_suffix_for_mime(mime: str) -> str:
    mime = (mime or "").split(";", 1)[0].lower().strip()
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(mime, ".jpg")


def image_id(source: str) -> str:
    return hashlib.md5(source.encode()).hexdigest()[:12]


def natural_key(path: Path) -> list[object]:
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", path.name)]


def ensure_jpeg_bytes(img_bytes: bytes) -> bytes:
    """Return JPEG bytes for generated images before OSS upload."""
    try:
        from PIL import Image
        from io import BytesIO

        source = BytesIO(img_bytes)
        with Image.open(source) as image:
            if image.mode in {"RGBA", "LA"}:
                background = Image.new("RGB", image.size, (255, 255, 255))
                alpha = image.getchannel("A") if "A" in image.getbands() else None
                background.paste(image.convert("RGBA"), mask=alpha)
                output_image = background
            else:
                output_image = image.convert("RGB")

            output = BytesIO()
            output_image.save(output, format="JPEG", quality=95, optimize=True)
            return output.getvalue()
    except ImportError as exc:
        raise RuntimeError("Pillow is required to convert generated images to JPG") from exc


def _download_one_image(index: int, url: str, total: int) -> tuple[int, dict[str, Any]]:
    """Download a single carousel image. Returns (index, item) so results stay
    index-aligned regardless of completion order."""
    item = {"index": index + 1, "url": url, "ok": False, "bytes": 0, "mime": "", "error": ""}
    try:
        log(f"  download [{index + 1}/{total}]: {url[:80]}...")
        raw_bytes, mime = download_bytes(url)
        item.update({"ok": True, "bytes": len(raw_bytes), "mime": mime, "_raw": raw_bytes})
        log(f"    ok [{index + 1}]: {len(raw_bytes)} bytes, mime={mime}")
    except Exception as exc:
        item["error"] = str(exc)
        log(f"    fail [{index + 1}]: {exc}")
    return index, item


def _download_images_parallel(all_urls: list[str]) -> tuple[list, list, list, list[int]]:
    """Download all carousel images in parallel, preserving input order.

    Replaces the serial download loop that was the dominant wall-clock cost in
    step3 (up to 10 sequential round-trips). A bounded thread pool keeps memory
    in check; per-image failures stay isolated exactly as before (None entries
    keep the lists index-aligned with all_urls).
    """
    total = len(all_urls)
    workers = min(IMAGE_DOWNLOAD_CONCURRENCY, total)
    fetched: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_download_one_image, i, url, total) for i, url in enumerate(all_urls)]
        for fut in as_completed(futures):
            idx, item = fut.result()
            fetched[idx] = item
    image_bytes_list: list[bytes | None] = [None] * total
    image_b64_list: list[str | None] = [None] * total
    downloads: list[dict[str, Any]] = []
    for i in range(total):
        item = fetched[i]
        raw = item.pop("_raw", None)
        if item["ok"] and raw is not None:
            image_bytes_list[i] = raw
            image_b64_list[i] = encode_image_data_url(raw)
        downloads.append(item)
    valid_indices = [i for i, b in enumerate(image_bytes_list) if b is not None]
    valid_b64 = [b for b in image_b64_list if b is not None]
    return image_bytes_list, image_b64_list, downloads, valid_indices


def collect_product_images(products: list[dict]) -> dict[str, Any]:
    """Download and encode product carousel images for Multimodal and I2I reuse."""
    all_urls = []
    for product in products:
        for img_url in product.get("carousel_images", [])[:10]:
            all_urls.append(img_url)

    if not all_urls:
        raise PipelineStepError("no carousel images to process", {
            "total_input_images": 0,
            "valid_images": 0,
            "downloads": [],
        })

    log(f"total {len(all_urls)} carousel image URLs to download")
    image_bytes_list, image_b64_list, downloads, valid_indices = _download_images_parallel(all_urls)
    valid_b64 = [b for b in image_b64_list if b is not None]
    log(f"download complete: {len(valid_b64)}/{len(all_urls)} usable")

    result = {
        "all_urls": all_urls,
        "image_bytes_list": image_bytes_list,
        "image_b64_list": image_b64_list,
        "valid_b64": valid_b64,
        "valid_indices": valid_indices,
        "downloads": downloads,
        "total_input_images": len(all_urls),
        "valid_images": len(valid_b64),
    }

    if not valid_b64:
        raise PipelineStepError("all images download failed", result)

    return result


def summarize_image_inputs(image_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "total_input_images": image_context.get("total_input_images", 0),
        "valid_images": image_context.get("valid_images", 0),
        "downloads": image_context.get("downloads", []),
    }
