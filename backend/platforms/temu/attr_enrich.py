"""属性数据库补全:从 attr_db.json 匹配 pid/vid/templatePid。

以前这段逻辑在 Chrome 插件 popup.js 里,attr_db.json 也打包在插件中,
用户解包插件就能看到辛苦积累的属性数据库。现在挪到后端:
  - attr_db.json 放在 backend/platforms/temu/(不在插件包里)
  - 插件只采集原始 propName/propValue,提交给后端
  - 后端在 /api/temu/import 入库前用本模块补全属性

匹配逻辑与原 popup.js 完全一致(逐行对照移植):
  1. 过滤掉不需要的属性(商品编号、产地)
  2. 用 propsDB[propName] 匹配 → 取 pid / templatePid
  3. 用 valuesDB[pid|propValue] 或 "、" 分割子值匹配 → 取 vid
  4. 未命中的属性保持原样(不丢字段)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("attr_enrich")

_ATTR_DB_PATH = Path(__file__).resolve().parent / "attr_db.json"

# 与插件保持一致:这两个属性不补全
_EXCLUDE_PROPS = {"商品编号", "产地"}

# 进程级缓存:启动时加载一次,避免每次请求都读 233KB 文件
_props_db: dict[str, Any] = {}
_values_db: dict[str, Any] = {}
_loaded = False


def _load_db() -> None:
    """加载 attr_db.json 到内存(进程级,只读一次)。"""
    global _props_db, _values_db, _loaded
    if _loaded:
        return
    try:
        raw = json.loads(_ATTR_DB_PATH.read_text(encoding="utf-8"))
        _props_db = raw.get("props", {}) or {}
        _values_db = raw.get("values", {}) or {}
        _loaded = True
        logger.info("attr_db loaded: %d props, %d values", len(_props_db), len(_values_db))
    except Exception as exc:
        logger.warning("attr_db load failed: %s", exc)
        _props_db = {}
        _values_db = {}
        _loaded = True


def _get_vid(db_val: Any) -> str:
    """从 DB 值中提取 vid(兼容新旧格式)。

    新格式: {"vid": "123"}  旧格式: "123"(纯字符串)
    """
    if not db_val:
        return ""
    if isinstance(db_val, str):
        return db_val
    return str(db_val.get("vid", "") or "")


def enrich_product_props(product_data: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int]:
    """补全 product_data 里的 productProps 属性。

    返回 (enriched_props, hit_count, total_count):
      enriched_props: 补全后的属性列表
      hit_count:      命中数据库的数量
      total_count:    过滤后的属性总数
    """
    _load_db()

    raw_props = product_data.get("productProps", []) or []
    # 也兼容插件可能用的字段名
    if not raw_props:
        raw_props = product_data.get("goodsProperty", []) or []

    # 过滤不需要的属性
    filtered = [
        p for p in raw_props
        if str(p.get("propName", p.get("key", ""))).strip() not in _EXCLUDE_PROPS
    ]

    hit_count = 0
    enriched: list[dict[str, Any]] = []

    for p in filtered:
        pn = str(p.get("propName", "")).strip()
        pv = str(p.get("propValue", "")).strip()
        match = _props_db.get(pn)

        if not match:
            # 数据库中没有该属性,保持原样(保留所有已有字段)
            enriched.append(p)
            continue

        hit_count += 1
        pid = str(match.get("pid", "") or p.get("pid", "") or "")
        tpid = str(match.get("templatePid", "") or p.get("templatePid", "") or "")

        # 用数据库中的 vid 填入
        vid = str(p.get("vid", "") or "")
        if not vid and pv and pid:
            vkey = f"{pid}|{pv}"
            v_match = _values_db.get(vkey)
            if v_match:
                vid = _get_vid(v_match)
            else:
                # 尝试按 "、" 分割子值匹配
                for part in pv.split("、"):
                    pk = f"{pid}|{part.strip()}"
                    vm = _values_db.get(pk)
                    if vm:
                        vid = _get_vid(vm)
                        break

        enriched.append({
            "propName": p.get("propName", ""),
            "refPid": p.get("refPid", "") or "",
            "pid": pid,
            "templatePid": tpid,
            "numberInputValue": p.get("numberInputValue", "") or "",
            "valueUnit": p.get("valueUnit", "") or "",
            "vid": vid,
            "propValue": p.get("propValue", ""),
        })

    return enriched, hit_count, len(filtered)
