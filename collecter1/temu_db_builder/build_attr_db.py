#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_attr_db.py
================
从 Temu 卖家后台 template/query 接口的返回文件，【全量重建】属性匹配数据库。

每次运行都重新扫描输入文件夹下所有 *.txt / *.json，重新统计并生成数据库，
所以「把新的 output 文件夹丢进来重跑」即可更新，结果完全可复现（幂等）。

用法
----
    python build_attr_db.py                                   # 用默认输入/输出
    python build_attr_db.py --input "X:\\path\\to\\output"    # 只换输入
    python build_attr_db.py --input "..." --output db.json    # 两个都换

默认
----
    输入  : E:\\workplace\\Temu_Collection_Directory\\output
    输出  : E:\\workplace\\temu-collector1\\attr_db.json   (同目录生成 build_report.txt)

数据库结构 (schema 2)
---------------------
    refPid            : {"12":"1", ...}        refPid  -> pid            (主键，1:1 最干净)
    names             : {"材质":"1", ...}       propName-> pid            (兜底键)
    values            : {"1|涤纶":"12345",...}  pid|propValue -> vid      (pid 命中后直接查 vid)
    templates         : {"70444":{"1467":"1140253",...}, ...}   templateId -> {pid: templatePid} (精确，按类目)
    defaultTemplatePid: {"1467":"1140253", ...} pid -> 出现最多的 templatePid (未知类目时的兜底)
    props             : 旧版兼容层 name -> {pid, templatePid, source}      (老插件不改代码也能用)

设计要点
--------
    - pid 优先用 refPid 查（实测 1:1 无冲突），refPid 缺失再用 propName 兜底。
    - templatePid 随类目变化（同一 pid 可有上百个 templatePid），所以用
      (templateId, pid) 作精确键；未知类目时用该 pid 出现频次最高的 templatePid。
    - 同一 pid 被多个 propName 共享（如 材质/主体材质 都是 pid=1）是正常的，保留所有别名。
    - 同一 (pid|value) 出现多个 vid 时，取出现频次最高者（并列取数值更小）。
"""
import json
import glob
import os
import argparse
from collections import defaultdict, Counter
from datetime import datetime

DEC = json.JSONDecoder()

DEFAULT_INPUT  = r"E:\workplace\Temu_Collection_Directory\output"
DEFAULT_OUTPUT = r"E:\workplace\temu-collector1\attr_db.json"


def _norm(v):
    """把 JSON 值规范成字符串；None/空统一成 ''。"""
    if v is None:
        return ""
    s = str(v)
    return "" if s == "None" else s


def _int(s):
    """字符串 -> int；空/非数字返回 0（写库用裸数字，店小秘要求数字类型）。"""
    try:
        return int(s)
    except (TypeError, ValueError):
        return 0


def parse_first(s):
    """容错解析：只取第一个 JSON 对象，忽略尾部多余数据（拼接文件）。"""
    obj, _ = DEC.raw_decode(s.lstrip())
    return obj


def best(counter):
    """从 Counter 选出『频次最高；并列取数值更小；再并列取字典序更小』的 key。"""
    def keyf(k):
        try:
            n = (int(k), k)
        except (TypeError, ValueError):
            n = (10 ** 18, k)
        return (-counter[k], n[0], n[1])
    return min(counter, key=keyf) if counter else None


def collect_evidence(input_dir):
    ev = {
        "refPid":  defaultdict(Counter),   # refPid        -> Counter(pid)
        "name":    defaultdict(Counter),   # propName      -> Counter(pid)
        "value":   defaultdict(Counter),   # "pid|val"     -> Counter(vid)
        "cell":    defaultdict(Counter),   # (tid,pid)     -> Counter(templatePid)
        "pidtpid": defaultdict(Counter),   # pid           -> Counter(templatePid)
        "tids":    set(),
        "pid_count": set(),
    }
    files = sorted(set(
        glob.glob(os.path.join(input_dir, "**", "*.txt"),  recursive=True) +
        glob.glob(os.path.join(input_dir, "**", "*.json"), recursive=True)
    ))
    parsed, skipped = 0, []
    for fp in files:
        try:
            j = parse_first(open(fp, encoding="utf-8").read())
        except Exception as e:
            skipped.append((os.path.basename(fp), "parse_error: " + str(e)[:50]))
            continue
        res = (j or {}).get("result") or {}
        if not res.get("properties"):
            skipped.append((os.path.basename(fp), "no result.properties"))
            continue
        parsed += 1
        tid = _norm(res.get("templateId"))
        if tid:
            ev["tids"].add(tid)
        for p in res["properties"]:
            pid = _norm(p.get("pid"))
            if not pid:
                continue
            ev["pid_count"].add(pid)
            name = (p.get("name") or "").strip()
            ref  = _norm(p.get("refPid"))
            tpid = _norm(p.get("templatePid"))
            if ref:
                ev["refPid"][ref][pid] += 1
            if name:
                ev["name"][name][pid] += 1
            if tpid:
                ev["pidtpid"][pid][tpid] += 1
                if tid:
                    ev["cell"][(tid, pid)][tpid] += 1
            for v in (p.get("values") or []):
                val = (v.get("value") or "").strip()
                vid = _norm(v.get("vid"))
                if val and vid:
                    ev["value"][pid + "|" + val][vid] += 1
    return ev, parsed, skipped, files


def build_db(ev):
    # 内部 best() 返回字符串；写库出口统一转 int（店小秘要裸数字，字符串会识别失败）
    refPid = {k: best(c) for k, c in ev["refPid"].items()}
    names  = {k: best(c) for k, c in ev["name"].items()}
    values = {k: best(c) for k, c in ev["value"].items()}
    defaultTemplatePid = {pid: best(c) for pid, c in ev["pidtpid"].items()}
    templates = {}
    for (tid, pid), c in ev["cell"].items():
        templates.setdefault(tid, {})[pid] = best(c)

    # ---- 旧版兼容层 props：name -> {pid, templatePid(=默认), source}（数字类型）----
    props = {}
    for name, pid in names.items():
        props[name] = {
            "pid": _int(pid),
            "templatePid": _int(defaultTemplatePid.get(pid, 0)),
            "source": "api",
        }

    # 排序，保证重跑结果逐字节一致（幂等）；排序后把值转 int
    def sk_int(d):
        return {k: _int(d[k]) for k in sorted(d)}
    refPid = sk_int(refPid)
    names  = sk_int(names)
    values = sk_int(values)
    defaultTemplatePid = sk_int(defaultTemplatePid)
    props = {k: props[k] for k in sorted(props)}
    templates = {tid: {pid: _int(templates[tid][pid]) for pid in sorted(templates[tid])}
                 for tid in sorted(templates)}
    return refPid, names, values, templates, defaultTemplatePid, props


def diagnostics(ev):
    """统计各表的冲突情况，用于评估稳定性。"""
    out = {}
    out["refPid_conflict"] = [(r, dict(c)) for r, c in ev["refPid"].items() if len(c) > 1]
    out["name_conflict"]   = [(n, dict(c)) for n, c in ev["name"].items()   if len(c) > 1]
    out["value_conflict"]  = [(k, dict(c)) for k, c in ev["value"].items()  if len(c) > 1]
    out["cell_conflict"]   = [((t, p), dict(c)) for (t, p), c in ev["cell"].items() if len(c) > 1]
    return out


def write_report(path, ev, db, parsed, total_files, skipped, diags, input_dir, output):
    refPid, names, values, templates, defTpid, props = db
    lines = []
    lines.append("=" * 64)
    lines.append("Temu 属性数据库 构建报告")
    lines.append("=" * 64)
    lines.append("生成时间  : " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("输入目录  : " + input_dir)
    lines.append("输出文件  : " + output)
    lines.append("")
    lines.append("【扫描】")
    lines.append("  扫描文件总数 : %d" % total_files)
    lines.append("  成功解析     : %d" % parsed)
    lines.append("  跳过/异常    : %d" % len(skipped))
    for fn, why in skipped[:20]:
        lines.append("    - %s : %s" % (fn, why))
    lines.append("")
    lines.append("【产出规模】")
    lines.append("  类目模板数 templateId      : %d" % len(templates))
    lines.append("  属性 pid 数               : %d" % len(ev["pid_count"]))
    lines.append("  refPid 映射数             : %d" % len(refPid))
    lines.append("  propName 映射数           : %d" % len(names))
    lines.append("  属性值 value 映射数       : %d" % len(values))
    lines.append("  (templateId,pid) 精确单元 : %d" % sum(len(v) for v in templates.values()))
    lines.append("")
    lines.append("【稳定性体检】")
    lines.append("  refPid->pid 冲突 (期望 0)         : %d" % len(diags["refPid_conflict"]))
    for r, c in diags["refPid_conflict"][:30]:
        lines.append("      %s -> %s" % (r, c))
    lines.append("  (templateId,pid)->templatePid 冲突 : %d" % len(diags["cell_conflict"]))
    lines.append("  propName->pid 真冲突(同义异pid)    : %d" % len(diags["name_conflict"]))
    for n, c in diags["name_conflict"][:30]:
        lines.append("      '%s' -> %s" % (n, c))
    lines.append("  pid|value->vid 多值 (已取频次最高)  : %d" % len(diags["value_conflict"]))
    for k, c in diags["value_conflict"][:15]:
        lines.append("      '%s' -> %s" % (k, c))
    lines.append("")
    lines.append("【说明】")
    lines.append("  * pid 优先用 refPid 查；refPid 缺失再用 propName。")
    lines.append("  * templatePid：知道目标类目 templateId 时查 templates[templateId][pid]；")
    lines.append("    否则用 defaultTemplatePid[pid]（该 pid 出现频次最高的 templatePid）。")
    lines.append("  * props 为旧版兼容层，老插件不改代码即可使用；")
    lines.append("    想发挥 refPid/templates 精确匹配需升级 popup.js（见交付说明）。")
    lines.append("")
    open(path, "w", encoding="utf-8").write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description="构建 Temu 属性匹配数据库")
    ap.add_argument("--input",  default=DEFAULT_INPUT,  help="输入文件夹（含 template/query 返回）")
    ap.add_argument("--output", default=DEFAULT_OUTPUT, help="输出 attr_db.json 路径")
    args = ap.parse_args()

    input_dir = args.input
    output    = os.path.abspath(args.output)

    print("[1/4] 扫描输入:", input_dir)
    ev, parsed, skipped, files = collect_evidence(input_dir)
    print("      文件 %d 个，解析成功 %d，跳过 %d" % (len(files), parsed, len(skipped)))

    print("[2/4] 生成各表 ...")
    db = build_db(ev)
    refPid, names, values, templates, defTpid, props = db

    database = {
        "schema": 2,
        "meta": {
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "source": "template/query API",
            "input_dir": input_dir,
            "files_parsed": parsed,
            "template_count": len(templates),
            "pid_count": len(ev["pid_count"]),
            "refpid_count": len(refPid),
            "name_count": len(names),
            "value_count": len(values),
        },
        "refPid":             refPid,
        "names":              names,
        "values":             values,
        "templates":          templates,
        "defaultTemplatePid": defTpid,
        "props":              props,   # 旧版兼容层
    }

    print("[3/4] 写入数据库:", output)
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        json.dump(database, f, ensure_ascii=False, indent=2)

    print("[4/4] 写入报告 ...")
    report_path = os.path.join(os.path.dirname(output), "build_report.txt")
    diags = diagnostics(ev)
    write_report(report_path, ev, db, parsed, len(files), skipped, diags, input_dir, output)

    print("")
    print("完成。")
    print("  数据库:", output)
    print("  报告  :", report_path)
    print("  规模  : 模板 %d | pid %d | refPid %d | 名 %d | 值 %d"
          % (len(templates), len(ev["pid_count"]), len(refPid), len(names), len(values)))
    print("  体检  : refPid冲突=%d  名冲突=%d  值多值=%d"
          % (len(diags["refPid_conflict"]), len(diags["name_conflict"]), len(diags["value_conflict"])))


if __name__ == "__main__":
    main()
