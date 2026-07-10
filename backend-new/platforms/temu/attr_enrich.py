"""属性数据库补全:从 attr_db.json 匹配 pid/vid/templatePid。

以前这段逻辑在 Chrome 插件 popup.js 里,attr_db.json 也打包在插件中,
用户解包插件就能看到辛苦积累的属性数据库。现在挪到后端:
  - attr_db.json 放在 backend/platforms/temu/(不在插件包里)
  - 插件只采集原始 propName/propValue/refPid,提交给后端
  - 后端在 /api/temu/import 入库前用本模块补全属性

匹配逻辑(schema 2,优先级从高到低):
  1. 过滤掉不需要的属性(商品编号、产地)
  2. pid: 优先用 refPid 查(1:1 最准), refPid 缺失再用 propName 兜底
  3. vid: 用 valuesDB[pid|propValue] 或 "、" 分割子值匹配
  4. templatePid: 用 defaultTemplatePid[pid](该 pid 出现频次最高的 templatePid)
  5. 未命中的属性保持原样(不丢字段)

兼容 schema 1 旧库(props+values 两段): 检测不到 schema 2 字段时自动回退。
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

# 进程级缓存:启动时加载一次
_props_db: dict[str, Any] = {}
_values_db: dict[str, Any] = {}
_refpid_db: dict[str, str] = {}
_names_db: dict[str, str] = {}
_default_tpid_db: dict[str, str] = {}
_templates_db: dict[str, Any] = {}
_loaded = False


def _load_db() -> None:
    """加载 attr_db.json 到内存(进程级,只读一次)。"""
    global _props_db, _values_db, _refpid_db, _names_db, _default_tpid_db, _templates_db, _loaded
    if _loaded:
        return
    try:
        raw = json.loads(_ATTR_DB_PATH.read_text(encoding="utf-8"))
        _props_db = raw.get("props", {}) or {}
        _values_db = raw.get("values", {}) or {}
        # schema 2 新增字段(没有就空 dict, 自动兼容旧库)
        _refpid_db = raw.get("refPid", {}) or {}
        _names_db = raw.get("names", {}) or {}
        _default_tpid_db = raw.get("defaultTemplatePid", {}) or {}
        _templates_db = raw.get("templates", {}) or {}
        _loaded = True
        logger.info(
            "attr_db loaded(schema=%s): %d props, %d values, %d refPid, %d names, %d defaultTemplatePid, %d templates",
            raw.get("schema", 1),
            len(_props_db), len(_values_db),
            len(_refpid_db), len(_names_db), len(_default_tpid_db), len(_templates_db),
        )
    except Exception as exc:
        logger.warning("attr_db load failed: %s", exc)
        _props_db = {}
        _values_db = {}
        _refpid_db = {}
        _names_db = {}
        _default_tpid_db = {}
        _templates_db = {}
        _loaded = True


def _to_int_str(v: Any) -> str:
    """把 refPid/pid 等值规范成整数字符串(数据库 key 是 "123" 这种)。"""
    if v is None:
        return ""
    s = str(v).strip()
    return s


def _get_vid(db_val: Any) -> str:
    """从 DB 值中提取 vid(兼容新旧格式)。

    新格式: {"vid": "123"}  旧格式: "123"(纯字符串)
    """
    if not db_val:
        return ""
    if isinstance(db_val, str) or isinstance(db_val, int):
        return str(db_val)
    return str(db_val.get("vid", "") or "")


def _resolve_pid(p: dict[str, Any], pn: str) -> str:
    """解析 pid: 优先 refPid(1:1), 缺失用 names/props 兜底。"""
    # 1) refPid 优先(schema 2 最准)
    ref_pid = _to_int_str(p.get("refPid", ""))
    if ref_pid:
        hit = _refpid_db.get(ref_pid)
        if hit:
            return str(hit)
    # 2) names 兜底(propName -> pid, schema 2)
    if pn and _names_db:
        hit = _names_db.get(pn)
        if hit:
            return str(hit)
    # 3) props 兜底(兼容 schema 1)
    if pn:
        match = _props_db.get(pn)
        if match:
            return str(match.get("pid", "") or "")
    return ""


def _resolve_template_pid(pid: str, p: dict[str, Any], pn: str, category_id: str = "") -> str:
    """解析 templatePid: 优先类目专用 templates[cat][pid], 兜底全局频次 defaultTemplatePid[pid]。

    同一个 pid 在不同类目下的 templatePid 不同(例如材质 pid=1 在指甲钳类目和餐具
    类目的 templatePid 完全不一样)。店小秘按类目校验, 用全局频次最高的 templatePid
    会落到错误的类目导致配对失败。因此必须优先用产品所属类目的专用 templatePid。
    """
    # 1) 类目专用(schema 2): templates[categoryId][pid] 最准
    if pid and category_id and _templates_db:
        cat_map = _templates_db.get(str(category_id))
        if cat_map and isinstance(cat_map, dict):
            hit = cat_map.get(pid)
            if hit:
                return str(hit)
    # 2) 全局兜底(schema 2): defaultTemplatePid[pid] 该 pid 出现频次最高
    if pid and _default_tpid_db:
        hit = _default_tpid_db.get(pid)
        if hit:
            return str(hit)
    # 3) props 兜底(兼容 schema 1, 里面也带 templatePid)
    if pn:
        match = _props_db.get(pn)
        if match:
            tpid = str(match.get("templatePid", "") or "")
            if tpid:
                return tpid
    return ""


def enrich_product_props(product_data: dict[str, Any], category_id: str = "") -> tuple[list[dict[str, Any]], int, int]:
    """补全 product_data 里的 productProps 属性。

    返回 (enriched_props, hit_count, total_count):
      enriched_props: 补全后的属性列表
      hit_count:      pid 命中数据库的数量
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
        pn = str(p.get("propName", p.get("key", ""))).strip()
        pv = str(p.get("propValue", "")).strip()

        # 解析 pid
        pid = _resolve_pid(p, pn)
        if not pid:
            # pid 都没命中, 保持原样
            enriched.append(p)
            continue

        hit_count += 1

        # 解析 templatePid
        tpid = _resolve_template_pid(pid, p, pn, category_id)

        # 解析 vid
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
            "propName": p.get("propName", p.get("key", "")),
            "refPid": _to_int_str(p.get("refPid", "")),
            "pid": pid,
            "templatePid": tpid,
            "numberInputValue": p.get("numberInputValue", "") or "",
            "valueUnit": p.get("valueUnit", "") or "",
            "vid": vid,
            "propValue": p.get("propValue", ""),
        })

    return enriched, hit_count, len(filtered)
