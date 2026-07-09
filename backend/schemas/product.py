"""统一商品模型：所有平台采集的原始数据,经各平台 adapter 标准化为此结构。

下游所有 step(翻译/多模态/生图)只认这个模型,不关心数据来自 Temu 还是 1688。
新增平台只需写 adapter + pipeline + prompts,core/ 工具零修改。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Product:
    """标准化商品表示。

    平台原生结构(Temu 商品 JSON / 1688 详情 JSON)由各 adapter 映射到此形状。
    """

    row: int = 2
    chinese_title: str = ""
    english_title: str = ""
    carousel_images: list[str] = field(default_factory=list)
    gallery_images: list[str] = field(default_factory=list)
    old_image_urls: list[str] = field(default_factory=list)
    videos: list[dict[str, Any]] = field(default_factory=list)
    skus: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


def to_pipeline_input(product: Product) -> dict[str, Any]:
    """转成 core steps 期望的 dict 形状。"""
    return {
        "row": product.row,
        "chinese_title": product.chinese_title,
        "english_title": product.english_title,
        "carousel_images": list(product.carousel_images),
        "gallery_images": list(product.gallery_images),
        "old_image_urls": list(product.old_image_urls),
    }
