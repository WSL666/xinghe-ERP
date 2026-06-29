"""Temu 导出:生成符合 popTemu 模板的 59 列 xlsx(返回内存 bytes)。

各平台导出格式不同,故每平台一个 export.py。
Temu 模板包含产品标题、SKU、规格、生成图URL 等 59 列。
"""
from __future__ import annotations

import json
from typing import Any


def to_xlsx(raw_import: dict[str, Any], cn_title: str, en_title: str,
            generated: list[dict[str, Any]]) -> bytes:
    """生成 Temu 59 列 xlsx,返回 bytes。

    参数:
      raw_import  原始采集数据(含 shopConfig/skus/product 等)
      cn_title    流水线优化后的中文标题(优先用)
      en_title    流水线优化后的英文标题
      generated   step4 生成的图片列表(每项含 generated_image URL)
    """
    import openpyxl
    from openpyxl.styles import Alignment, Font
    from openpyxl.utils import get_column_letter

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "popTemu_product"

    headers = [
        '产品标题','英文标题','产品描述','产品货号','变种名称',
        '变种属性名称一','变种属性值一','变种属性名称二','变种属性值二','预览图',
        '申报价格','SKU货号','长','宽','高',
        '重量','识别码类型','识别码','站外产品链接','轮播图',
        '产品素材图','外包装形状','外包装类型','外包装图片','建议零售价(建议零售价币种)',
        '库存','发货时效','分类id','产品属性','SPU属性',
        'SKC属性','SKU属性','站点价格','来源url','产地',
        '敏感属性','备注','SKU分类','SKU分类数量','SKU分类单位',
        '独立包装','净含量数值','净含量单位','混合套装类型','SKU分类总数量',
        'SKU分类总数量单位','总净含量','总净含量单位','包装清单','生命周期',
        '视频Url','运费模板（模板id）','经营站点','所属店铺','SPUID',
        'SKCID','SKUID','创建时间','更新时间'
    ]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")

    shop = raw_import.get("shopConfig", {})
    goods_id = raw_import.get("goodsId", "")
    category_id = raw_import.get("categoryId", "")
    video_url = raw_import.get("videoUrl", "")
    created_at = raw_import.get("createdAt", "")
    skus = raw_import.get("skus", [])
    product_data = raw_import.get("product", {})

    gallery_images = product_data.get("galleryImages", [])
    first_img = gallery_images[0] if gallery_images else ""

    raw_props = product_data.get("productProps", [])
    clean_props = [{
        "propName": p.get("propName", ""), "refPid": p.get("refPid", ""),
        "pid": p.get("pid", ""), "templatePid": p.get("templatePid", ""),
        "numberInputValue": p.get("numberInputValue", ""), "valueUnit": p.get("valueUnit", ""),
        "vid": p.get("vid", ""), "propValue": p.get("propValue", ""),
    } for p in raw_props]
    props_json = json.dumps(clean_props, ensure_ascii=False)

    pack_list = product_data.get("packList", []) or []
    pack_list_json = json.dumps(pack_list, ensure_ascii=False) if pack_list else ""

    generated_urls = [g.get("generated_image", "") for g in generated if g.get("generated_image")]

    def fmt_decimal(v):
        if v is None or v == "":
            return ""
        try:
            return f"{float(v):.1f}"
        except (ValueError, TypeError):
            return ""

    def clean_price(p):
        if not p:
            return ""
        s = str(p).replace("$", "").replace("¥", "").replace(",", "").replace(" ", "")
        try:
            return str(float(s))
        except ValueError:
            return ""

    row_idx = 2
    for sku in skus:
        first_generated = generated_urls[0] if generated_urls else ""
        gen_str = "\n".join(generated_urls)
        r = [""] * 59
        r[0]  = cn_title or product_data.get("title", "")
        r[1]  = en_title
        r[4]  = sku.get("variantName", "")
        r[5]  = sku.get("specName1", "")
        r[6]  = sku.get("specValue1", "")
        r[7]  = sku.get("specName2", "")
        r[8]  = sku.get("specValue2", "")
        r[9]  = first_generated
        r[10] = clean_price(shop.get("declarePrice") or sku.get("price", ""))
        r[12] = fmt_decimal(shop.get("length", ""))
        r[13] = fmt_decimal(shop.get("width", ""))
        r[14] = fmt_decimal(shop.get("height", ""))
        r[15] = fmt_decimal(shop.get("weight", ""))
        r[19] = gen_str
        r[20] = first_generated
        r[24] = clean_price(shop.get("retailPrice") or sku.get("price", ""))
        r[25] = shop.get("stock") or sku.get("stock", 0)
        r[26] = "9"
        r[27] = category_id
        r[28] = props_json
        r[29] = "[]"
        r[30] = sku.get("skcProps", "[]")
        r[31] = sku.get("skuProps", "[]")
        r[34] = shop.get("origin", "")
        r[37] = shop.get("skuClass") or "按件包装"
        r[38] = shop.get("skuClassQty") or "7"
        r[39] = shop.get("skuClassUnit") or "件"
        r[40] = "否"
        r[41] = "0"
        r[44] = "0"
        r[46] = "0"
        r[48] = pack_list_json
        r[50] = video_url
        r[51] = shop.get("shippingTemplateId") or shop.get("shipping", "")
        r[52] = shop.get("site", "")
        r[53] = shop.get("shopName", "")
        r[54] = sku.get("spuId", goods_id)
        r[55] = sku.get("skcId", "")
        r[56] = sku.get("skuId", "")
        r[57] = created_at
        r[58] = created_at
        for col, val in enumerate(r, 1):
            ws.cell(row=row_idx, column=col, value=val)
        row_idx += 1

    col_widths = [
        40, 40, 30, 15, 20, 15, 20, 15, 20, 50,
        12, 18, 8, 8, 8, 8, 12, 15, 50, 50,
        50, 12, 12, 50, 15, 8, 10, 10, 60, 10,
        50, 50, 12, 50, 20, 12, 30, 12, 10, 10,
        10, 10, 10, 10, 10, 10, 10, 10, 40, 12,
        50, 20, 20, 15, 20, 20, 20, 20, 20
    ]
    for col, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(col)].width = w

    from io import BytesIO
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
