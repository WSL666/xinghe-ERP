"""API Key 池内网管理面板:左右结构。

布局:
  左侧  模型列表(竖排,可滚动),点一个切换右侧
  右侧  当前模型的两张表(纵向叠放)
        上:正常 key 表(可用+冷却)
        下:失效 key 表(401/403)

字段: Key(脱敏) / 状态 / 添加时间 / 失败次数 / 失败原因
操作: 增(添加) / 删(单删+批量删失效) / 改(改状态) / 查(列表+计数)

安全(双层):
  1. 仅本机访问(request.client.host 必须 127.0.0.1)
  2. 本地 token(.env 的 ADMIN_TOKEN)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .pool import PROVIDERS, all_snapshots, get_pool

router = APIRouter(prefix="/admin/keys", tags=["admin"])


def _is_local(request: Request) -> bool:
    client = request.client.host if request.client else ""
    return client in ("127.0.0.1", "::1", "localhost", "0000:0000:0000:0000:0000:0000:0000:0001")


def _check_token(request: Request) -> None:
    import config  # 延迟导入: 让 run.py 的 fake 注入生效, 生产环境无影响
    expected = getattr(config.get_settings(), "admin_token", "") or ""
    if not expected:
        return
    token = request.query_params.get("token", "")
    if not token:
        auth = request.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.lower().startswith("bearer") else ""
    if token != expected:
        raise HTTPException(status_code=403, detail={"ok": False, "error": "invalid admin token"})


def _guard(request: Request) -> None:
    if not _is_local(request):
        raise HTTPException(status_code=403, detail={"ok": False, "error": "admin panel is local-only"})
    _check_token(request)


def _ok(**payload: Any) -> dict[str, Any]:
    return {"ok": True, **payload}


def _require_provider(provider: str) -> str:
    if provider not in PROVIDERS:
        raise HTTPException(status_code=400, detail={"ok": False, "error": f"unknown provider: {provider}"})
    return provider


# ── 页面 ──
_PAGE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>API Key 池管理</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#0f172a;color:#e2e8f0;height:100vh;display:flex;flex-direction:column}
header{background:#1e293b;border-bottom:1px solid #334155;padding:14px 24px;display:flex;align-items:center;justify-content:space-between}
header h1{font-size:17px;font-weight:600}
header .meta{font-size:12px;color:#64748b}
.layout{display:flex;flex:1;overflow:hidden}
/* 左侧模型列表 */
.sidebar{width:220px;background:#1e293b;border-right:1px solid #334155;display:flex;flex-direction:column}
.sidebar-title{padding:14px 16px;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.5px}
.model-list{flex:1;overflow-y:auto;padding:4px}
.model-item{display:flex;align-items:center;justify-content:space-between;padding:12px 14px;border-radius:8px;cursor:pointer;margin-bottom:2px;transition:background .15s}
.model-item:hover{background:#334155}
.model-item.active{background:#3b82f6}
.model-item.active .m-count{background:rgba(255,255,255,.25)}
.model-name{font-size:14px;font-weight:500}
.m-count{background:#334155;color:#cbd5e1;font-size:11px;padding:2px 8px;border-radius:10px}
/* 右侧主区 */
.main{flex:1;overflow-y:auto;padding:20px 28px}
.model-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
.model-header h2{font-size:20px;font-weight:600}
.stat-pills{display:flex;gap:8px}
.pill{font-size:12px;padding:4px 12px;border-radius:12px;font-weight:500}
.pill.green{background:rgba(34,197,94,.15);color:#4ade80;border:1px solid rgba(34,197,94,.3)}
.pill.amber{background:rgba(245,158,11,.15);color:#fbbf24;border:1px solid rgba(245,158,11,.3)}
.pill.red{background:rgba(239,68,68,.15);color:#f87171;border:1px solid rgba(239,68,68,.3)}
/* 添加框 */
.add-box{background:#1e293b;border:1px solid #334155;border-radius:10px;padding:14px;margin-bottom:22px;display:flex;gap:10px;align-items:center}
.add-box input{flex:1;background:#0f172a;border:1px solid #334155;color:#e2e8f0;padding:9px 12px;border-radius:6px;font-size:13px;font-family:monospace}
.add-box input:focus{outline:none;border-color:#3b82f6}
.add-box button{background:#3b82f6;color:#fff;border:none;padding:9px 18px;border-radius:6px;cursor:pointer;font-size:13px;font-weight:500;white-space:nowrap}
.add-box button:hover{background:#2563eb}
/* 表格区块 */
.section{margin-bottom:24px}
.section-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:10px}
.section-title{font-size:14px;font-weight:600;display:flex;align-items:center;gap:8px}
.dot{width:8px;height:8px;border-radius:50%}
.dot.green{background:#22c55e}.dot.amber{background:#f59e0b}.dot.red{background:#ef4444}
table{width:100%;border-collapse:collapse;background:#1e293b;border:1px solid #334155;border-radius:8px;overflow:hidden}
thead th{background:#0f172a;padding:10px 14px;text-align:left;font-size:11px;color:#94a3b8;font-weight:600;text-transform:uppercase;letter-spacing:.3px;border-bottom:1px solid #334155}
tbody td{padding:11px 14px;border-bottom:1px solid #1e293b;font-size:13px;vertical-align:middle}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover{background:#1e293b}
td.mono{font-family:monospace;color:#cbd5e1}
.status-tag{font-size:11px;padding:3px 9px;border-radius:10px;font-weight:500;display:inline-block}
.status-tag.available{background:rgba(34,197,94,.15);color:#4ade80}
.status-tag.cooling{background:rgba(245,158,11,.15);color:#fbbf24}
.status-tag.failed{background:rgba(239,68,68,.15);color:#f87171}
td .reason{color:#94a3b8;font-size:12px}
.row-actions{display:flex;gap:6px}
.row-actions button{background:transparent;border:1px solid #475569;color:#cbd5e1;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:11px}
.row-actions button:hover{background:#334155}
.row-actions button.danger{color:#f87171;border-color:rgba(239,68,68,.4)}
.row-actions button.danger:hover{background:rgba(239,68,68,.15)}
.row-actions button.ok{color:#4ade80;border-color:rgba(34,197,94,.4)}
.row-actions button.ok:hover{background:rgba(34,197,94,.15)}
.empty{padding:24px;text-align:center;color:#64748b;font-size:13px}
.btn-danger{background:transparent;border:1px solid rgba(239,68,68,.4);color:#f87171;padding:5px 12px;border-radius:5px;cursor:pointer;font-size:12px}
.btn-danger:hover{background:rgba(239,68,68,.15)}
.toast{position:fixed;bottom:24px;right:24px;background:#1e293b;border:1px solid #3b82f6;padding:12px 18px;border-radius:8px;font-size:13px;display:none;box-shadow:0 8px 24px rgba(0,0,0,.4)}
.toast.show{display:block}
</style></head><body>
<header>
  <h1>🔑 API Key 池管理</h1>
  <span class="meta" id="ts"></span>
</header>
<div class="layout">
  <aside class="sidebar">
    <div class="sidebar-title">模型 (点击切换)</div>
    <div class="model-list" id="modelList"></div>
  </aside>
  <main class="main" id="main"></main>
</div>
<div class="toast" id="toast"></div>
<script>
const TOKEN = new URLSearchParams(location.search).get('token') || '';
let state = { pools: [], cur: '' };
function H(){return {'Content-Type':'application/json','Authorization':TOKEN?'Bearer '+TOKEN:''};}
function U(p){return p + (TOKEN?'?token='+TOKEN:'');}

async function load(){
  try{
    const r = await fetch(U('/admin/keys/api/state'), {headers:H()});
    const d = await r.json();
    state.pools = d.pools;
    if(!state.cur && d.pools.length) state.cur = d.pools[0].provider;
    render();
  }catch(e){ toast('加载失败: '+e); }
}

function curPool(){ return state.pools.find(p=>p.provider===state.cur) || state.pools[0] || {normal:[],failed:[],counts:{}}; }

function render(){
  // 左侧模型列表
  const ml = document.getElementById('modelList');
  ml.innerHTML = state.pools.map(p=>{
    const c = p.counts;
    return `<div class="model-item ${p.provider===state.cur?'active':''}" onclick="sel('${p.provider}')">
      <span class="model-name">${p.label}</span>
      <span class="m-count">${c.normal}/${c.failed}</span>
    </div>`;
  }).join('');
  // 右侧
  const p = curPool();
  const c = p.counts || {};
  document.getElementById('main').innerHTML = `
    <div class="model-header">
      <h2>${p.label}</h2>
      <div class="stat-pills">
        <span class="pill green">可用 ${c.available||0}</span>
        <span class="pill amber">冷却 ${c.cooling||0}</span>
        <span class="pill red">失效 ${c.failed||0}</span>
      </div>
    </div>
    <div class="add-box">
      <input type="text" id="newKey" placeholder="粘贴完整 API Key（如 sk-xxxxxxxx）添加到 ${p.label} 的可用池">
      <button onclick="addKey()">+ 添加 Key</button>
    </div>
    <div class="section">
      <div class="section-head">
        <div class="section-title"><span class="dot green"></span> 正常 Key（可用 + 冷却）</div>
        <span style="font-size:12px;color:#64748b">共 ${p.normal.length} 个</span>
      </div>
      ${tableHtml(p.normal, 'normal')}
    </div>
    <div class="section">
      <div class="section-head">
        <div class="section-title"><span class="dot red"></span> 失效 Key（401/403，可批量删）</div>
        ${p.failed.length?`<button class="btn-danger" onclick="bulkDel()">批量删除全部失效 (${p.failed.length})</button>`:''}
      </div>
      ${tableHtml(p.failed, 'failed')}
    </div>`;
  document.getElementById('ts').textContent = '更新于 ' + new Date().toLocaleTimeString();
}

function statusTag(s){
  const map = {available:['可用','available'],cooling:['冷却','cooling'],failed:['失效','failed']};
  const [t,cls] = map[s]||[s,''];
  return `<span class="status-tag ${cls}">${t}</span>`;
}

function tableHtml(rows, type){
  if(!rows.length) return `<table><tbody><tr><td><div class="empty">暂无</div></td></tr></tbody></table>`;
  const rowsHtml = rows.map(r=>{
    let actions = '';
    if(type==='failed'){
      actions = `<div class="row-actions">
        <button class="ok" onclick="update('${r.full_key}','available','恢复到可用')">恢复</button>
        <button class="danger" onclick="del('${r.full_key}')">删除</button>
      </div>`;
    }else{
      actions = `<div class="row-actions">
        ${r.status==='cooling'?`<button class="ok" onclick="update('${r.full_key}','available','恢复')">恢复</button>`:''}
        <button class="danger" onclick="update('${r.full_key}','failed','标记失效')">标记失效</button>
        <button class="danger" onclick="del('${r.full_key}')">删除</button>
      </div>`;
    }
    return `<tr>
      <td class="mono">${r.key}</td>
      <td>${statusTag(r.status)}</td>
      <td>${r.added_at||'-'}</td>
      <td>${r.fail_count||0}</td>
      <td>${r.fail_reason?`<span style="color:#fbbf24;font-weight:500">${r.fail_reason}</span>`:'<span class="reason">-</span>'}</td>
      <td>${actions}</td>
    </tr>`;
  }).join('');
  const isFail = type==='failed';
  return `<table><thead><tr>
    <th>Key</th><th>状态</th><th>${isFail?'失效时间':'添加时间'}</th><th>失败次数</th><th>失败原因</th><th>操作</th>
  </tr></thead><tbody>${rowsHtml}</tbody></table>`;
}

function sel(provider){ state.cur = provider; render(); }

async function addKey(){
  const inp = document.getElementById('newKey');
  const key = inp.value.trim();
  if(!key) return toast('请输入 key');
  const r = await fetch(U('/admin/keys/api/add'), {method:'POST',headers:H(),body:JSON.stringify({provider:state.cur,key})});
  const d = await r.json();
  toast(d.ok?('已添加到 '+ (curPool().label)): (d.error||d.detail?.error||'添加失败'));
  if(d.ok){ inp.value=''; load(); }
}

async function del(key){
  const r = await fetch(U('/admin/keys/api/remove'), {method:'POST',headers:H(),body:JSON.stringify({provider:state.cur,key})});
  const d = await r.json();
  toast(d.ok?'已删除':'删除失败'); load();
}

async function bulkDel(){
  if(!confirm('确认批量删除 '+curPool().label+' 的全部失效 Key？')) return;
  const r = await fetch(U('/admin/keys/api/bulk-remove'), {method:'POST',headers:H(),body:JSON.stringify({provider:state.cur,state:'failed'})});
  const d = await r.json();
  toast(d.ok?('已删除 '+d.removed+' 个'):'失败'); load();
}

async function update(key, status, label){
  const r = await fetch(U('/admin/keys/api/update'), {method:'POST',headers:H(),body:JSON.stringify({provider:state.cur,key,status})});
  const d = await r.json();
  toast(d.ok?(label+'成功'):'操作失败'); load();
}

let toastT;
function toast(msg){ const t=document.getElementById('toast'); t.textContent=msg; t.classList.add('show'); clearTimeout(toastT); toastT=setTimeout(()=>t.classList.remove('show'),2200); }

load(); setInterval(load, 5000);
</script>
</body></html>
"""


@router.get("", response_class=HTMLResponse)
def panel_page(request: Request) -> str:
    _guard(request)
    return _PAGE_HTML


# ── JSON API ──
@router.get("/api/state")
def api_state(request: Request) -> dict[str, Any]:
    _guard(request)
    return _ok(pools=all_snapshots())


@router.post("/api/add")
def api_add(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _guard(request)
    provider = _require_provider(str(payload.get("provider", "")))
    key = str(payload.get("key", "")).strip()
    if not key:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "key 不能为空"})
    ok = get_pool(provider).add(key)
    if not ok:
        raise HTTPException(status_code=409, detail={"ok": False, "error": "该 key 已存在"})
    return _ok(added=True)


@router.post("/api/remove")
def api_remove(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _guard(request)
    provider = _require_provider(str(payload.get("provider", "")))
    key = str(payload.get("key", "")).strip()
    if not key:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "key 不能为空"})
    ok = get_pool(provider).remove(key)
    if not ok:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "key 不存在"})
    return _ok(removed=True)


@router.post("/api/bulk-remove")
def api_bulk_remove(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _guard(request)
    provider = _require_provider(str(payload.get("provider", "")))
    pool = get_pool(provider)
    keys = payload.get("keys")
    if isinstance(keys, list):
        n = pool.bulk_remove([str(k) for k in keys if k])
        return _ok(removed=n)
    state = str(payload.get("state", "")).strip()
    if state == "failed":
        n = pool.bulk_remove([r["full_key"] for r in pool.list_failed()])
        return _ok(removed=n)
    raise HTTPException(status_code=400, detail={"ok": False, "error": "需要 keys[] 或 state=failed"})


@router.post("/api/update")
def api_update(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    """改状态: {provider, key, status}。status: available/failed/cooling。"""
    _guard(request)
    provider = _require_provider(str(payload.get("provider", "")))
    key = str(payload.get("key", "")).strip()
    status = str(payload.get("status", "")).strip()
    if not key or status not in ("available", "failed", "cooling"):
        raise HTTPException(status_code=400, detail={"ok": False, "error": "key 和 status(available/failed/cooling) 必填"})
    ok = get_pool(provider).update(key, status)
    if not ok:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "key 不存在"})
    return _ok(updated=True)


@router.post("/api/clear")
def api_clear(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    _guard(request)
    provider = _require_provider(str(payload.get("provider", "")))
    n = get_pool(provider).clear_all()
    return _ok(cleared=n)
