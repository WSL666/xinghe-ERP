"""Temu 平台数据适配器:把 Temu 原始 raw_json 标准化为统一 Product 模型。

下游 core 工具只认 Product,不关心 Temu 的 JSON 结构。这样:
- Temu 数据结构变化时,只改这里
- 新增 1688 平台时,写自己的 adapter,core 不动
"""
from __future__ import annotations

from typing import Any

from schemas.product import Product


def parse_product(raw_import: dict[str, Any]) -> Product:
    """从 Temu raw_json 解析出标准 Product。

    Temu 原始结构:
      raw_import["product"]["galleryImages"]  轮播图URL列表
      raw_import["product"]["title"]          原始中文标题
      raw_import["product"]["oldImageUrls"]   已传OSS的旧图(可能为空)
      raw_import["videos"]                    源视频列表
      raw_import["skus"]                      SKU列表
    """
    product_data = raw_import.get("product", {}) or {}
    gallery = (product_data.get("galleryImages", []) or [])[:10]
    old_urls = (product_data.get("oldImageUrls", []) or [])[:10]

    return Product(
        row=2,
        chinese_title=product_data.get("title", "") or "",
        english_title="",
        carousel_images=list(gallery),
        gallery_images=list(gallery),
        old_image_urls=list(old_urls),
        videos=list(raw_import.get("videos", []) or []),
        skus=list(raw_import.get("skus", []) or []),
        raw=raw_import,
    )


def from_db_row(row: dict[str, Any], raw_import: dict[str, Any]) -> Product:
    """从 DB 已有字段(cn_title 等) + raw_import 重建 Product。

    用于手动触发单步或导出:此时部分字段(标题)可能已被流水线优化过,
    需用 DB 的 cn_title/en_title 覆盖,图片等仍取 raw_import。
    """
    product = parse_product(raw_import)
    if row.get("cn_title"):
        product.chinese_title = row["cn_title"]
    if row.get("en_title"):
        product.english_title = row["en_title"]
    return product
