/**
 * ====================================================================
 *  商务工作台 - 数据展示渲染层（app.js）
 *  ⚠️ 修改本文件前必读：列表/布局约定见 styles.css 顶部，这里只讲渲染规则。
 * --------------------------------------------------------------------
 *  商品表格共 9 列（与 index.html 的 <colgroup> 一一对应，勿改列数）：
 *    1 复选框 | 2 商品标题 | 3 状态 | 4 图片 | 5 产品规格
 *    6 视频   | 7 尺寸     | 8 时间 | 9 操作
 *
 *  渲染约定（改这些函数时必须遵守，否则整表错位）：
 *   · renderProducts() 每行恰好输出 9 个 <td>，空状态行用 colspan="9"。
 *   · 图片列：源图一行、AI图一行，各自最多 10 张，>10 折叠成 9++N。见 renderImageRows/renderImageRow。
 *   · 视频列：源视频一行、AI视频一行；无视频显示“无视频”，第二行恒显示“AI视频”。见 renderVideoStrip。
 *   · 尺寸列读 item.size_json 的 length/width/height/weight，单位 cm/g，无值显示灰色占位框。
 *   · 缩略图/占位框/角标的尺寸全部在 CSS 定死，这里不要写行内 style。
 * ====================================================================
 */
const DEFAULT_API_BASE = window.location.origin;
const STORAGE_KEYS = {
  apiBase: "ppe_api_base"
};

const savedApiBase = localStorage.getItem(STORAGE_KEYS.apiBase);
const initialApiBase = savedApiBase && !/:5000\/?$/i.test(savedApiBase) ? savedApiBase : DEFAULT_API_BASE;
if (savedApiBase !== initialApiBase) {
  localStorage.setItem(STORAGE_KEYS.apiBase, initialApiBase);
}

const state = {
  apiBase: initialApiBase,
  imports: [],
  view: "dashboard",
  search: "",
  status: "all",
  user: null,
  previewImages: [],
  previewIndex: 0
};
// AI创作中心运行时状态
state.composerAttachments = [];
state.studioRefs = [];
state.editTool = "background";
state.editImage = null;

const views = {
  dashboard: { title: "商品 AI 管线", eyebrow: "工作台" },
  products: { title: "TEMU采集箱", eyebrow: "商品采集箱" },
  box1688: { title: "1688采集箱", eyebrow: "商品采集箱" },
  boxOzon: { title: "OZON采集箱", eyebrow: "商品采集箱" },
  settings: { title: "设置", eyebrow: "配置" },
  agent: { title: "智能体", eyebrow: "AI创作中心" },
  aiImage: { title: "AI生图", eyebrow: "AI创作中心" },
  imageEdit: { title: "图片编辑", eyebrow: "AI创作中心" }
};

/**
 * 视图别名：所有采集箱（1688/OZON）复用 products 面板的同一张商品表格，
 * 保证“格式完全一样”。以后新增采集箱只要在 views 定义标题 + 这里加一条别名即可。
 */
const PANEL_ALIAS = {
  box1688: "products",
  boxOzon: "products",
};
/**
 * 每个采集箱对应的平台值（与后端 imports.platform 一致，统一小写）。
 * products=TEMU, box1688=1688, boxOzon=ozon。dashboard 等非采集箱视图不设平台。
 */
const PLATFORM_BY_VIEW = {
  products: "temu",
  box1688: "1688",
  boxOzon: "ozon",
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

// Top-level safe clipboard copy. navigator.clipboard is undefined over plain
// HTTP LAN (https/localhost only); fall back to a hidden textarea + execCommand.
async function copyTextSafe(value) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    try { await navigator.clipboard.writeText(value); return true; } catch {}
  }
  try {
    const ta = document.createElement("textarea");
    ta.value = value;
    ta.style.position = "fixed";
    ta.style.top = "-9999px";
    ta.setAttribute("readonly", "");
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch { return false; }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function toast(message, type = "ok") {
  const node = $("#toast");
  if (!node) return;
  node.textContent = message;
  node.className = `toast show ${type}`;
  clearTimeout(node._timer);
  node._timer = setTimeout(() => node.classList.remove("show"), 3600);
}

function normalizeApiBase(value) {
  return String(value || DEFAULT_API_BASE).trim().replace(/\/+$/, "");
}

function apiUrl(path) {
  return `${state.apiBase}${path}`;
}

async function apiFetch(path, options = {}) {
  const response = await fetch(apiUrl(path), {
    credentials: "include",
    ...options,
    headers: {
      ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(options.headers || {})
    }
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok || data.ok === false) {
    throw new Error(data.error || `Request failed: ${response.status}`);
  }
  return data;
}

function setSession(user) {
  state.user = user;
  const entNav = document.getElementById("enterpriseNav");
  if (entNav) {
    const role = (user && user.enterprise && user.enterprise.role) || "";
    entNav.classList.toggle("is-hidden", role !== "owner" && role !== "admin");
  }
}

function clearSession() {
  state.user = null;
}

function showApp() {
  $("#loginView").classList.add("is-hidden");
  $("#appView").classList.remove("is-hidden");
  syncApiText();
  setView(state.view);
  refreshData({ silent: true });
  startLiveSync();
}

function showLogin() {
  $("#loginView").classList.remove("is-hidden");
  $("#appView").classList.add("is-hidden");
  stopLiveSync();
  $("#loginApiBase").value = state.apiBase;
}

function syncApiText() {
  $("#sideApiBase").textContent = state.apiBase;
  $("#settingsApiBase").value = state.apiBase;
  $("#pluginUrl").value = state.apiBase;

  const apiKeyInput = $("#pluginApiKey");
  if (apiKeyInput) {
    apiKeyInput.value = state.user?.api_key || state.user?.api_key_preview || "";
  }

  $("#manifestExample").value = JSON.stringify([
    "*://*.temu.com/*",
    "http://localhost:6688/*",
    `${state.apiBase}/*`
  ], null, 2);
}

function setApiStatus(status, text) {
  const node = $("#apiStatus");
  node.className = `status-pill ${status}`;
  node.textContent = text;
}

function statusInfo(item) {
  const status = item.status || "pending";
  if (status === "done") return { cls: "ok", text: "已完成" };
  if (status === "generating") return { cls: "running", text: "生成中" };
  if (status === "translating") return { cls: "running", text: "翻译中" };
  if (status === "error") return { cls: "error", text: "错误" };
  return { cls: "pending", text: "待处理" };
}

function basename(path) {
  return String(path || "").split(/[\\/]/).pop();
}

function imageDownloadUrl(path) {
  if (/^https?:\/\//i.test(String(path || ""))) return path;
  const name = basename(path);
  return name ? apiUrl(`/api/download/${encodeURIComponent(name)}`) : "";
}

function generatedOk(item) {
  return (item.generated_json || []).filter((img) => img && img.generated_image && !img.error);
}

function normalizeImageItems(items, mode = "remote") {
  return (items || [])
    .filter(Boolean)
    .map((item) => {
      const raw = mode === "generated" ? (item.generated_image || item) : item;
      const src = imageDownloadUrl(raw);
      const title = mode === "generated" ? (item.image_type || basename(src)) : src;
      return { src, title };
    })
    .filter((item) => item.src);
}

function imageSetToken(list) {
  return encodeURIComponent(JSON.stringify(list.map((item) => item.src)));
}

function renderThumbs(items, mode = "remote") {
  const list = normalizeImageItems(items, mode);
  if (!list.length) return `<div class="empty-thumb">无图片</div>`;
  const encoded = imageSetToken(list);
  return `<div class="thumbs">${list.map((item, index) => `
    <img class="thumb clickable"
      src="${escapeHtml(item.src)}"
      title="${escapeHtml(item.title)}"
      data-action="preview"
      data-src="${escapeHtml(item.src)}"
      data-index="${index}"
      data-images="${encoded}"
      loading="lazy"
      alt="">
  `).join("")}</div>`;
}

function renderImageRows(originals, generated) {
  // 源图一行、AI 图一行（各自独立），每行最多 10 张，两行垂直堆叠
  const originalList = normalizeImageItems(originals).map((it) => ({ ...it, kind: "orig" }));
  const generatedList = normalizeImageItems(generated, "generated").map((it) => ({ ...it, kind: "ai" }));
  return `
    <div class="image-row-stack">
      ${renderImageRow(originalList, "orig", "源")}
      ${renderImageRow(generatedList, "ai", "AI")}
    </div>
  `;
}

// 渲染单行图片：固定每行最大 MAX_IMAGES 张（含 +N 占位框）。永不换行、永不撑高
function renderImageRow(list, kind, label) {
  const MAX_IMAGES = 10;
  if (!list.length) {
    return `<div class="image-strip img-strip-row empty-strip"><span class="empty-thumb">${label}图</span></div>`;
  }
  const encoded = imageSetToken(list);
  const exceed = list.length > MAX_IMAGES;
  const overflow = exceed ? list.length - (MAX_IMAGES - 1) : 0;
  const shown = list.slice(0, exceed ? MAX_IMAGES - 1 : MAX_IMAGES);
  const tiles = shown.map((item, index) => `
    <button class="image-tile" type="button"
      data-action="preview"
      data-src="${escapeHtml(item.src)}"
      data-index="${index}"
      data-images="${encoded}"
      title="${escapeHtml(item.title)}">
      <img src="${escapeHtml(item.src)}" loading="lazy" alt="">
      <span class="tile-badge ${kind}">${label}</span>
    </button>
  `).join("");
  const more = exceed
    ? `<span class="image-tile tile-more" title="共 ${list.length} 张${label}图"><span>+${overflow}</span></span>`
    : "";
  return `<div class="image-strip img-strip-row">${tiles}${more}</div>`;
}

function renderImageStrip(list, tail = "") {
  if (!list.length) return `<div class="empty-thumb">无图片</div>`;
  const encoded = imageSetToken(list);
  return `<div class="image-strip">${list.map((item, index) => `
    <button class="image-tile" type="button"
      data-action="preview"
      data-src="${escapeHtml(item.src)}"
      data-index="${index}"
      data-images="${encoded}"
      title="${escapeHtml(item.title)}">
      <img src="${escapeHtml(item.src)}" loading="lazy" alt="">
    </button>
  `).join("")}${tail}</div>`;
}

function stepLogs(item) {
  const logs = item.step_logs || {};
  return ["step2_translate", "step3_vision", "step4_generation"]
    .map((key) => logs[key] ? { key, ...logs[key] } : null)
    .filter(Boolean);
}

function renderJsonBlock(value) {
  return `<pre>${escapeHtml(JSON.stringify(value || {}, null, 2))}</pre>`;
}

function renderStepLogs(item) {
  const logs = stepLogs(item);
  if (!logs.length) return `<div class="empty-state compact">暂无步骤日志。</div>`;
  return `<div class="step-log-list">${logs.map((log) => `
    <article class="step-log-card ${escapeHtml(log.status || "unknown")}">
      <div class="step-log-head">
        <strong>${escapeHtml(log.label || log.key)}</strong>
        <span class="badge ${log.status === "success" ? "ok" : log.status === "failed" ? "error" : "running"}">${escapeHtml(log.status || "unknown")}</span>
      </div>
      <div class="step-log-meta">
        <span>开始：${escapeHtml(log.started_at || "-")}</span>
        <span>结束：${escapeHtml(log.finished_at || "-")}</span>
        <span>历史：${escapeHtml(log.history_count || 0)}</span>
      </div>
      ${log.error ? `<p class="step-log-error">${escapeHtml(log.error)}</p>` : ""}
      ${log.output ? renderJsonBlock(log.output) : ""}
    </article>
  `).join("")}</div>`;
}

function hasVision(item) {
  return Boolean(item.vision_json && item.vision_json.selected_indexes && !item.vision_json.error);
}

function filteredImports() {
  const term = state.search.trim().toLowerCase();
  return state.imports.filter((item) => {
    const statusMatch = state.status === "all" || (item.status || "pending") === state.status;
    const haystack = [
      item.id,
      item.goods_id,
      item.title,
      item.cn_title,
      item.en_title
    ].join(" ").toLowerCase();
    return statusMatch && (!term || haystack.includes(term));
  });
}

function renderStats() {
  $("#statTotal").textContent = state.imports.length;
  $("#statDone").textContent = state.imports.filter((item) => item.status === "done").length;
  $("#statImages").textContent = state.imports.reduce((sum, item) => sum + generatedOk(item).length, 0);
  $("#statRunning").textContent = state.imports.filter((item) => item.status !== "done").length;
}

function renderRecent() {
  const list = state.imports.slice(0, 6);
  $("#recentList").innerHTML = list.length ? list.map((item) => {
    const status = statusInfo(item);
    const title = item.cn_title || item.title || "未命名商品";
    return `
      <article class="recent-item">
        <div>
          <strong>${escapeHtml(title)}</strong>
          <small>#${escapeHtml(item.id)} - ${escapeHtml(item.created_at || "")}</small>
        </div>
        <span class="badge ${status.cls}">${status.text}</span>
      </article>
    `;
  }).join("") : `<div class="empty-state">暂无商品。请从插件发送或上传 XLSX。</div>`;
}

function formatTimeRange(item) {
  const created = String(item.created_at || "");
  const finished = String(item.finished_at || "");
  if (!created) return `<span class="muted-cell">-</span>`;
  const date = created.slice(0, 10);
  const startHM = created.slice(11, 16);
  const finishHM = finished ? finished.slice(11, 16) : "";
  let mins = "";
  if (finishHM) {
    const t0 = new Date(created.replace(" ", "T"));
    const t1 = new Date(finished.replace(" ", "T"));
    if (!isNaN(t0) && !isNaN(t1)) {
      const diff = Math.max(0, Math.round((t1 - t0) / 60000));
      mins = diff >= 60 ? (Math.round(diff / 6) / 10) + "小时" : diff + "分钟";
    }
  }
  return `<div class="time-cell"><span>${escapeHtml(date)}</span><span>${escapeHtml(startHM)}${finishHM ? "至" + escapeHtml(finishHM) : ""}</span>${mins ? `<span>（耗时${escapeHtml(mins)}）</span>` : ""}</div>`;
}

function specSummary(spec) {
  if (!spec) return null;
  const tree = spec.specTree || [];
  const levels = spec.specLevels || tree.map((l) => l.specKey);
  const skuCount = spec.skuCount || 0;
  if (!levels.length && !skuCount) return null;
  return {
    skuCount: skuCount,
    levelCount: levels.length,
    levelNames: levels.map((x) => (typeof x === "string" ? x : x.specKey)),
  };
}

// 尺寸列：长 宽 高 重 四项，单位 cm / g；无值显示灰色占位框
function renderSizeCell(item) {
  const size = item.size_json || {};
  const fields = [
    { key: "length", label: "长", unit: "cm" },
    { key: "width",  label: "宽", unit: "cm" },
    { key: "height", label: "高", unit: "cm" },
    { key: "weight", label: "重", unit: "g"  },
  ];
  const rows = fields.map((f) => {
    const raw = size[f.key];
    const val = (raw !== undefined && raw !== null && String(raw).trim() !== "")
      ? String(raw).trim()
      : "";
    return `<span class="size-row">${f.label}：${val
      ? `<b>${escapeHtml(val)}${f.unit}</b>`
      : `<span class="size-blank"></span>${f.unit}`}</span>`;
  }).join("");
  return `<div class="size-cell">${rows}</div>`;
}

function renderSpecCell(item) {
  const summary = specSummary(item.spec_json);
  if (!summary) return `<span class="muted-cell">-</span>`;
  const head = "查看";
  const detail = summary.levelNames.map((n, i) => `${i + 1}.${escapeHtml(n)}`).join("、");
  return `<button class="link-btn" data-action="spec" data-id="${item.id}" title="${escapeHtml(detail)}">${escapeHtml(head)}</button>`;
}

function renderVideoStrip(item) {
  const videos = (item.video_json || []).filter((v) => v && (v.oss_url || v.url));
  const srcRow = videos.length
    ? `<div class="image-strip img-strip-row">${videos.map((v) => {
        const src = v.oss_url || v.url;
        const poster = v.poster || "";
        const inner = poster
          ? `<img src="${escapeHtml(poster)}" loading="lazy" alt="">`
          : `<span class="image-tile-ico">&#9658;</span>`;
        return `<button type="button" class="image-tile video-ico-tile" data-action="play-video" data-src="${escapeHtml(src)}" data-poster="${escapeHtml(poster)}" title="${escapeHtml(v.width && v.height ? `${v.width}x${v.height}` : "视频")}">${inner}<span class="tile-badge orig">源</span></button>`;
     }).join("")}</div>`
    : `<div class="image-strip img-strip-row empty-strip"><span class="empty-thumb">无视频</span></div>`;
  const aiRow = `<div class="image-strip img-strip-row empty-strip"><span class="empty-thumb">AI视频</span></div>`;
  return `<div class="image-row-stack">${srcRow}${aiRow}</div>`;
}

function renderProducts() {
  const rows = filteredImports();
  $("#productRows").innerHTML = rows.length ? rows.map((item) => {
    const status = statusInfo(item);
    const origTitle = escapeHtml(item.title || "未命名商品");
    const cnTitle = item.cn_title ? escapeHtml(item.cn_title) : `<span class="muted-cell">待优化</span>`;
    const enTitle = item.en_title ? escapeHtml(item.en_title) : `<span class="muted-cell">待翻译</span>`;
    const generated = generatedOk(item);
    return `
      <tr>
        <td class="cell-check"><input type="checkbox" class="row-check" value="${item.id}"></td>
        <td>
          <div class="product-title">
            <div class="title-line title-orig" title="${origTitle}"><span>原</span>${origTitle}</div>
            <div class="title-line title-ai" title="${escapeHtml(item.cn_title || "")}"><span>新</span>${cnTitle}</div>
            <div class="title-line title-en" title="${escapeHtml(item.en_title || "")}"><span>英</span>${enTitle}</div>
            <small class="meta">ID ${escapeHtml(item.id)} / ${escapeHtml(item.goods_id || "无 goodsId")}</small>
          </div>
        </td>
        <td><span class="badge ${status.cls}" title="${escapeHtml(item.status_msg || "")}">${status.text}</span></td>
        <td>${renderImageRows(item.gallery_images || [], generated)}</td>
        <td>${renderSpecCell(item)}</td>
        <td>${renderVideoStrip(item)}</td>
        <td>${renderSizeCell(item)}</td>
        <td>${formatTimeRange(item)}</td>
        <td>
          <div class="row-actions">
            <button data-action="detail" data-id="${item.id}">详情</button>
            <button disabled>编辑</button>
            <button disabled>导入</button>
            <button data-action="export" data-id="${item.id}" ${item.status !== "done" ? "disabled" : ""}>导出</button>
            <button class="danger" data-action="delete" data-id="${item.id}">删除</button>
          </div>
        </td>
      </tr>
    `;
  }).join("") : `<tr><td colspan="9"><div class="empty-state">没有匹配的商品。</div></td></tr>`;
}

function renderAll() {
  renderStats();
  renderRecent();
  renderProducts();
}

function setView(name) {
  state.view = views[name] ? name : "dashboard";
  $$(".view-panel").forEach((panel) => panel.classList.remove("active"));
  const panelName = PANEL_ALIAS[state.view] || state.view;
  $(`#${panelName}Panel`).classList.add("active");
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === state.view));
  $("#pageTitle").textContent = views[state.view].title;
  $("#pageEyebrow").textContent = views[state.view].eyebrow;
  if (panelName === "products") {
    const t = $("#productPanelTitle");
    const e = $("#productPanelEyebrow");
    if (t) t.textContent = views[state.view].title;
    if (e) e.textContent = views[state.view].eyebrow;
  }
  const prevPlatform = state.platform;
  state.platform = PLATFORM_BY_VIEW[state.view] || null;
  if (panelName === "products" && prevPlatform !== state.platform) {
    refreshData({ silent: true });
  }
  syncNavGroup();
}

async function refreshData({ silent = false } = {}) {
  try {
    const url = state.platform ? `/api/imports?platform=${encodeURIComponent(state.platform)}` : "/api/imports";
    const data = await apiFetch(url);
    state.imports = Array.isArray(data.imports) ? data.imports : [];
    setApiStatus("ok", "服务在线");
    renderAll();
    if (!silent) toast("数据已刷新。");
  } catch (error) {
    setApiStatus("fail", "连接失败");
    if (!silent) toast(error.message, "error");
  }
}

// ===== Live auto-refresh: poll fast while jobs are running, slow when idle =====
// Plugin imports, translation progress, and image generation then show up on
// their own without a manual refresh.
const POLL_FAST_MS = 2500;
const POLL_IDLE_MS = 30000;
let _pollTimer = null;
let _polling = false;
let _lastSignature = "";

function _activeImportCount() {
  return state.imports.filter((item) => {
    const s = item.status || "pending";
    return s === "queued" || s === "generating" || s === "translating" || s === "pending" || s === "error";
  }).length;
}

function _importsSignature() {
  return state.imports
    .map((i) => `${i.id}:${i.status}:${i.step2_done}:${i.step3_done}:${i.step4_done}:${i.updated_at || ""}`)
    .join("|");
}

function _schedulePoll(interval) {
  if (_pollTimer) clearTimeout(_pollTimer);
  _pollTimer = setTimeout(_pollOnce, interval);
}

async function _pollOnce() {
  _pollTimer = null;
  if (_polling) { _schedulePoll(POLL_FAST_MS); return; }
  if (document.hidden) { _schedulePoll(POLL_IDLE_MS); return; }
  _polling = true;
  try {
    await refreshData({ silent: true });
    _lastSignature = _importsSignature();
  } catch {
  } finally {
    _polling = false;
  }
  _schedulePoll(_activeImportCount() > 0 ? POLL_FAST_MS : POLL_IDLE_MS);
}

function startLiveSync() {
  if (!_pollTimer) _schedulePoll(POLL_FAST_MS);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) _pollOnce();
  });
}

function stopLiveSync() {
  if (_pollTimer) { clearTimeout(_pollTimer); _pollTimer = null; }
}

async function loginUser(account, password) {
  const data = await apiFetch("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ account, password })
  });
  setSession(data.user);
  return data.user;
}

async function registerUser(account, password) {
  const data = await apiFetch("/api/auth/register", {
    method: "POST",
    body: JSON.stringify({ account, password, display_name: account })
  });
  setSession(data.user);
  return data.user;
}

async function loadCurrentUser() {
  const data = await apiFetch("/api/auth/me");
  setSession(data.user);
  return data.user;
}

async function resetApiKey() {
  const data = await apiFetch("/api/auth/api-key/reset", { method: "POST" });
  setSession(data.user);
  syncApiText();
  await copyTextSafe(data.api_key);
  const _keyInput = $("#pluginApiKey");
  if (_keyInput) _keyInput.value = data.api_key || "";
  toast("API 密钥已重置并复制。");
}

async function uploadXlsx(file) {
  if (!file) return;
  const formData = new FormData();
  formData.append("file", file);
  await apiFetch("/api/step1/upload", { method: "POST", body: formData });
  toast("XLSX 已上传，后台任务已排队。");
  await refreshData({ silent: true });
}

async function runStep(id, step) {
  const labels = {
    step2: "翻译",
    step3: "视觉",
    step4: "生成",
    generate: "整体生成"
  };
  await apiFetch(`/api/imports/${id}/${step}`, { method: "POST" });
  toast(`${labels[step] || step} 已开始。`);
  await refreshData({ silent: true });
}

async function exportItem(id) {
  const response = await fetch(apiUrl(`/api/imports/${id}/export`), {
    method: "POST",
    credentials: "include"
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `Export failed: ${response.status}`);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="?([^";]+)"?/i);
  const filename = match ? match[1] : `final_result_${id}.xlsx`;
  if (window.showSaveFilePicker) {
    try {
      const handle = await window.showSaveFilePicker({
        suggestedName: filename,
        types: [{
          description: "Excel workbook",
          accept: {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"]
          }
        }]
      });
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      toast("导出已保存。");
      return;
    } catch (error) {
      if (error && error.name === "AbortError") {
        toast("导出已取消。");
        return;
      }
    }
  }
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  toast("导出已下载。");
}

async function deleteItem(id) {
  if (!confirm(`确认删除导入 #${id} 吗？`)) return;
  await apiFetch(`/api/imports/${id}`, { method: "DELETE" });
  toast("已删除该导入。");
  await refreshData({ silent: true });
}

function openDetail(id) {
  const item = state.imports.find((entry) => String(entry.id) === String(id));
  if (!item) return;
  const drawer = $("#detailDrawer");
  const title = item.cn_title || item.title || "未命名商品";
  const compactItem = {
    id: item.id,
    goods_id: item.goods_id,
    status: item.status,
    status_msg: item.status_msg,
    title: item.title,
    cn_title: item.cn_title,
    en_title: item.en_title,
    sku_count: item.sku_count,
    image_count: item.image_count,
    step2_done: item.step2_done,
    step3_done: item.step3_done,
    step4_done: item.step4_done,
    vision_json: item.vision_json,
    step_logs: item.step_logs,
    generated_json: item.generated_json
  };
  drawer.innerHTML = `
    <button class="ghost-btn small" data-action="close-drawer">关闭</button>
    <h3>${escapeHtml(title)}</h3>
    <p class="hint">导入 ID ${escapeHtml(item.id)} / 状态 ${escapeHtml(item.status || "pending")}</p>
    <h4>图片</h4>
    ${renderImageRows(item.gallery_images || [], generatedOk(item))}
    <h4>步骤日志</h4>
    ${renderStepLogs(item)}
    <details class="debug-json">
      <summary>调试 JSON</summary>
      ${renderJsonBlock(compactItem)}
    </details>
  `;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
}

function decodeText(value) {
  if (value === null || value === undefined) return "";
  let s = String(value);
  const entities = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&#039;": "'", "&nbsp;": " " };
  s = s.replace(/&#x([0-9a-fA-F]+);/g, (_, h) => String.fromCodePoint(parseInt(h, 16)));
  s = s.replace(/&#(\d+);/g, (_, d) => String.fromCodePoint(parseInt(d, 10)));
  for (const [k, v] of Object.entries(entities)) s = s.split(k).join(v);
  s = s.replace(/\u002F/g, "/").replace(/\s+/g, " ").trim();
  return s;
}

function openSpec(id) {
  const item = state.imports.find((entry) => String(entry.id) === String(id));
  if (!item) return;
  const spec = item.spec_json || {};
  const tree = spec.specTree || [];
  const title = decodeText(item.cn_title || item.title || "未命名商品");
  const drawer = $("#detailDrawer");
  const levels = tree.length ? tree : [];
  const allSpecImages = [];
  levels.forEach((level) => {
    (level.values || []).forEach((v) => {
      const src = imageDownloadUrl(v.imgUrl || "");
      if (src) allSpecImages.push(src);
    });
  });
  const specToken = imageSetToken(allSpecImages.map((src) => ({ src })));
  let cursor = 0;
  const levelHtml = levels.map((level, idx) => {
    const values = (level.values || []).map((v) => ({
      specValue: decodeText(v.specValue),
      imgUrl: v.imgUrl || "",
    })).filter((v) => v.imgUrl || v.specValue);
    const tiles = values.map((v) => {
      const hasImg = Boolean(v.imgUrl);
      const at = hasImg ? cursor++ : -1;
      const img = hasImg
        ? `<button type="button" class="spec-thumb-btn" data-action="preview" data-src="${escapeHtml(imageDownloadUrl(v.imgUrl))}" data-index="${at}" data-images="${specToken}" title="${escapeHtml(v.specValue)}"><img src="${escapeHtml(imageDownloadUrl(v.imgUrl))}" loading="lazy" alt="${escapeHtml(v.specValue)}"></button>`
        : `<span class="spec-no-img">无图</span>`;
      return `<div class="spec-value-tile">${img}<span title="${escapeHtml(v.specValue)}">${escapeHtml(v.specValue)}</span></div>`;
    }).join("");
    return `<div class="spec-level"><div class="spec-level-head">${idx + 1}. ${escapeHtml(decodeText(level.specKey))}（${values.length}种）</div><div class="spec-tiles">${tiles}</div></div>`;
  }).join("");
  const propsHtml = (spec.productProps || []).map((p) => {
    const name = decodeText(p.propName);
    const val = decodeText(p.propValue);
    if (!name && !val) return "";
    return `<div class="spec-prop"><span>${escapeHtml(name)}</span><b>${escapeHtml(val)}</b></div>`;
  }).filter(Boolean).join("");
  drawer.innerHTML = `
    <button class="ghost-btn small" data-action="close-drawer">关闭</button>
    <h3>${escapeHtml(title)}</h3>
    <p class="hint">产品规格 · 共 ${escapeHtml(String(spec.skuCount || 0))}个SKU、${escapeHtml(String(tree.length))}级规格</p>
    ${levelHtml ? `<div class="spec-tree">${levelHtml}</div>` : `<div class="empty-state compact">暂无规格数据。</div>`}
    ${propsHtml ? `<h4>产品详细信息</h4><div class="spec-props">${propsHtml}</div>` : ""}
  `;
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
}

function closeDrawer() {
  $("#detailDrawer").classList.remove("open");
  $("#detailDrawer").setAttribute("aria-hidden", "true");
}

function openVideo(src, poster = "") {
  const modal = $("#videoModal");
  const video = $("#modalVideo");
  if (!modal || !video) return;
  video.src = src;
  if (poster) video.poster = poster;
  modal.classList.add("open");
  modal.setAttribute("aria-hidden", "false");
  const pr = video.play();
  if (pr && typeof pr.catch === "function") pr.catch(() => {});
}

function closeVideo() {
  const modal = $("#videoModal");
  const video = $("#modalVideo");
  if (!modal || !video) return;
  video.pause();
  video.removeAttribute("src");
  video.load();
  modal.classList.remove("open");
  modal.setAttribute("aria-hidden", "true");
}

function openPreview(src, images = [], index = 0) {
  state.previewImages = images.length ? images : [src];
  state.previewIndex = Math.max(0, Math.min(index, state.previewImages.length - 1));
  $("#modalImage").src = state.previewImages[state.previewIndex];
  $("#imageModal").classList.add("open");
  $("#imageModal").setAttribute("aria-hidden", "false");
}

function closePreview() {
  $("#imageModal").classList.remove("open");
  $("#imageModal").setAttribute("aria-hidden", "true");
  $("#modalImage").src = "";
  state.previewImages = [];
  state.previewIndex = 0;
}

function shiftPreview(delta) {
  if (!state.previewImages.length) return;
  state.previewIndex = (state.previewIndex + delta + state.previewImages.length) % state.previewImages.length;
  $("#modalImage").src = state.previewImages[state.previewIndex];
}

async function copyText(value) {
  const ok = await copyTextSafe(value);
  toast(ok ? "已复制。" : "复制失败，请手动复制。");
}

function bindEvents() {
  $("#loginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = $("#loginUser").value.trim();
    const password = $("#loginPassword").value;
    state.apiBase = normalizeApiBase($("#loginApiBase").value);
    localStorage.setItem(STORAGE_KEYS.apiBase, state.apiBase);
    try {
      await loginUser(username, password);
      showApp();
    } catch (error) {
      const shouldRegister = confirm("登录失败。是否立即注册该账号？");
      if (!shouldRegister) {
        toast(error.message, "error");
        return;
      }
      try {
        await registerUser(username, password);
        showApp();
      } catch (registerError) {
        toast(registerError.message, "error");
      }
    }
  });

  $("#settingsForm").addEventListener("submit", (event) => {
    event.preventDefault();
    state.apiBase = normalizeApiBase($("#settingsApiBase").value);
    localStorage.setItem(STORAGE_KEYS.apiBase, state.apiBase);
    syncApiText();
    toast("设置已保存。");
    refreshData({ silent: true });
  });

  $("#searchInput").addEventListener("input", (event) => {
    state.search = event.target.value;
    renderProducts();
  });

  $("#statusFilter").addEventListener("change", (event) => {
    state.status = event.target.value;
    renderProducts();
  });

  $("#xlsxInput").addEventListener("change", async (event) => {
    try {
      await uploadXlsx(event.target.files[0]);
    } catch (error) {
      toast(error.message, "error");
    } finally {
      event.target.value = "";
    }
  });

  document.body.addEventListener("click", async (event) => {
    const viewButton = event.target.closest("[data-view]");
    if (viewButton) {
      setView(viewButton.dataset.view);
      return;
    }

    const actionButton = event.target.closest("[data-action]");
    if (!actionButton) return;

    const action = actionButton.dataset.action;
    const id = actionButton.dataset.id;

    try {
      if (action === "refresh") await refreshData();
      if (action === "test-api") await refreshData();
      if (action === "logout") {
        try {
          await apiFetch("/api/auth/logout", { method: "POST" });
        } catch {}
        clearSession();
        showLogin();
      }
      if (action === "open-upload") $("#xlsxInput").click();
      if (action === "play-video") openVideo(actionButton.dataset.src, actionButton.dataset.poster);
      if (action === "close-video") closeVideo();
      if (action === "preview") {
        let images = [];
        try {
          images = JSON.parse(decodeURIComponent(actionButton.dataset.images || "[]"));
        } catch {}
        openPreview(actionButton.dataset.src, images, Number(actionButton.dataset.index || 0));
      }
      if (action === "close-modal") closePreview();
      if (action === "prev-image") shiftPreview(-1);
      if (action === "next-image") shiftPreview(1);
      if (action === "detail") openDetail(id);
      if (action === "spec") openSpec(id);
      if (action === "close-drawer") closeDrawer();
      if (action === "step2") await runStep(id, "step2");
      if (action === "step3") await runStep(id, "step3");
      if (action === "step4") await runStep(id, "step4");
      if (action === "generate") await runStep(id, "generate");
      if (action === "export") await exportItem(id);
      if (action === "delete") await deleteItem(id);
      if (action === "copy-plugin-url") { const _ok = await copyTextSafe($("#pluginUrl").value); toast(_ok ? "已复制。" : "复制失败，请手动复制。"); }
      if (action === "reset-api-key") await resetApiKey();
      if (action === "toggle-nav") {
        const group = actionButton.closest(".nav-group");
        if (group) group.classList.toggle("open");
      }
      if (action === "new-chat") newAgentChat();
      if (action === "attach-image") $("#composerImageInput").click();
      if (action === "attach-file") $("#composerFileInput").click();
      if (action === "remove-att") {
        state.composerAttachments.splice(Number(actionButton.dataset.index || 0), 1);
        renderComposerAttachments();
      }
      if (action === "agent-send") await agentSend();
      if (action === "agent-prompt") {
        const composer = $("#composerInput");
        if (composer) {
          composer.value = actionButton.dataset.prompt || "";
          composer.focus();
          composerAutoGrow();
        }
      }
      if (action === "studio-add-ref") $("#studioRefInput").click();
      if (action === "remove-ref") {
        state.studioRefs.splice(Number(actionButton.dataset.index || 0), 1);
        renderStudioRefs();
      }
      if (action === "studio-preset") actionButton.classList.toggle("selected");
      if (action === "studio-generate") studioGenerate();
      if (action === "edit-tool") setEditTool(actionButton.dataset.editTool);
      if (action === "edit-upload") $("#editFileInput").click();
      if (action === "edit-apply") editApply();
      if (action === "edit-reset") { state.editImage = null; renderEditCanvas(); }
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("#videoModal").addEventListener("click", (event) => {
    if (event.target.id === "videoModal") closeVideo();
  });

  $("#imageModal").addEventListener("click", (event) => {
    if (event.target.id === "imageModal") closePreview();
  });

  $("#selectAll").addEventListener("change", (event) => {
    const checked = event.target.checked;
    document.querySelectorAll(".row-check").forEach((cb) => (cb.checked = checked));
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closePreview();
      closeVideo();
      closeDrawer();
    }
    if ($("#imageModal").classList.contains("open") && event.key === "ArrowLeft") shiftPreview(-1);
    if ($("#imageModal").classList.contains("open") && event.key === "ArrowRight") shiftPreview(1);
  });
}

async function init() {
  bindEvents();
  bindStudioEvents();
  syncApiText();
  try {
    await loadCurrentUser();
    showApp();
  } catch {
    showLogin();
  }
}

init();

// ===== AI创作中心 =====

function syncNavGroup() {
  const activeSub = document.querySelector(".nav-sub-item.active");
  document.querySelectorAll(".nav-group").forEach((group) => {
    const contains = group.contains(activeSub);
    group.classList.toggle("open", contains);
    group.classList.toggle("active-group", contains);
  });
}

function agentWelcomeHtml() {
  return `
    <div class="chat-welcome">
      <div class="chat-welcome-mark">AI</div>
      <h3>智能体创作助手</h3>
      <p>上传商品图或文件，告诉我你想做什么：生成套图、优化标题、写详情文案、分析竞品。智能体会按需调用生图与文档能力。</p>
    </div>
  `;
}

function composerAutoGrow() {
  const ta = $("#composerInput");
  if (!ta) return;
  ta.style.height = "auto";
  ta.style.height = Math.min(ta.scrollHeight, 160) + "px";
}

function renderComposerAttachments() {
  const box = $("#composerAttachments");
  if (!box) return;
  box.innerHTML = state.composerAttachments.map((a, i) => {
    if (a.kind === "image") {
      return `<div class="att att-img"><img src="${escapeHtml(a.url)}" alt=""><button class="att-remove" data-action="remove-att" data-index="${i}" type="button">×</button></div>`;
    }
    return `<div class="att att-file"><span>📄</span><span class="att-name">${escapeHtml(a.name)}</span><button class="att-remove" data-action="remove-att" data-index="${i}" type="button">×</button></div>`;
  }).join("");
  box.classList.toggle("has-items", state.composerAttachments.length > 0);
}

function addComposerFiles(fileList) {
  Array.from(fileList || []).forEach((file) => {
    const isImg = file.type.startsWith("image/");
    const entry = { kind: isImg ? "image" : "file", name: file.name };
    if (isImg) entry.url = URL.createObjectURL(file);
    state.composerAttachments.push(entry);
  });
  renderComposerAttachments();
}

async function agentSend() {
  const ta = $("#composerInput");
  const text = ((ta && ta.value) || "").trim();
  if (!text && !state.composerAttachments.length) return;
  const msgs = $("#agentMessages");
  if (!msgs) return;
  const welcome = msgs.querySelector(".chat-welcome");
  if (welcome) welcome.remove();
  const images = state.composerAttachments.filter((a) => a.kind === "image");
  const files = state.composerAttachments.filter((a) => a.kind === "file");
  const bubble = [
    images.map((a) => `<img class="bubble-img" src="${escapeHtml(a.url)}" alt="">`).join(""),
    files.length
      ? `<div class="att att-file" style="margin-bottom:8px">${files.map((f) => `<span>📄</span><span class="att-name">${escapeHtml(f.name)}</span>`).join("")}</div>`
      : "",
    escapeHtml(text)
  ].filter(Boolean).join("");
  msgs.insertAdjacentHTML("beforeend", `<div class="chat-bubble user">${bubble}</div>`);
  msgs.scrollTop = msgs.scrollHeight;
  if (ta) ta.value = "";
  composerAutoGrow();
  state.composerAttachments = [];
  renderComposerAttachments();
  msgs.insertAdjacentHTML("beforeend", `<div class="chat-bubble agent thinking"><span class="dot"></span><span class="dot"></span><span class="dot"></span></div>`);
  msgs.scrollTop = msgs.scrollHeight;
}

function newAgentChat() {
  const msgs = $("#agentMessages");
  if (msgs) msgs.innerHTML = agentWelcomeHtml();
  state.composerAttachments = [];
  renderComposerAttachments();
}

function bindStudioEvents() {
  const dropzones = document.querySelectorAll("[data-dropzone]");
  dropzones.forEach((zone) => {
    zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("drag"); });
    zone.addEventListener("dragleave", () => zone.classList.remove("drag"));
    zone.addEventListener("drop", (e) => {
      e.preventDefault();
      zone.classList.remove("drag");
      const files = e.dataTransfer?.files;
      if (!files || !files.length) return;
      if (zone.dataset.dropzone === "composer") addComposerFiles(files);
      if (zone.dataset.dropzone === "studio-ref") addStudioRefs(files);
      if (zone.dataset.dropzone === "edit") handleEditUpload(files[0]);
    });
  });
  const composerInput = $("#composerImageInput");
  if (composerInput) composerInput.addEventListener("change", (e) => addComposerFiles(e.target.files));
  const composerFile = $("#composerFileInput");
  if (composerFile) composerFile.addEventListener("change", (e) => addComposerFiles(e.target.files));
  const studioRef = $("#studioRefInput");
  if (studioRef) studioRef.addEventListener("change", (e) => addStudioRefs(e.target.files));
  const editInput = $("#editFileInput");
  if (editInput) editInput.addEventListener("change", (e) => handleEditUpload(e.target.files?.[0]));
  const composer = $("#composerInput");
  if (composer) composer.addEventListener("input", composerAutoGrow);
}

function addStudioRefs(fileList) {
  Array.from(fileList || []).forEach((file) => {
    if (!file.type.startsWith("image/")) return;
    state.studioRefs.push({ name: file.name, url: URL.createObjectURL(file) });
  });
  renderStudioRefs();
}

function renderStudioRefs() {
  const box = $("#studioRefList");
  if (!box) return;
  box.innerHTML = state.studioRefs.map((ref, i) => `
    <div class="studio-ref">
      <img src="${escapeHtml(ref.url)}" alt="">
      <button class="ref-remove" data-action="remove-ref" data-index="${i}" type="button">×</button>
    </div>
  `).join("");
  box.classList.toggle("has-items", state.studioRefs.length > 0);
}

async function studioGenerate() {
  const prompt = ($("#studioPrompt")?.value || "").trim();
  if (!state.studioRefs.length) { toast("请先添加参考图。", "error"); return; }
  if (!prompt) { toast("请输入生图描述。", "error"); return; }
  const btn = $("#studioGenerateBtn");
  if (btn) { btn.disabled = true; btn.textContent = "生成中..."; }
  try {
    const form = new FormData();
    form.append("prompt", prompt);
    state.studioRefs.forEach((ref) => form.append("refs", ref.name));
    form.append("tool", state.editTool);
    toast("已提交生图任务。");
  } catch (e) {
    toast(e.message, "error");
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "生成"; }
  }
}

function setEditTool(tool) {
  state.editTool = tool || "background";
  document.querySelectorAll("[data-edit-tool]").forEach((b) => b.classList.toggle("selected", b.dataset.editTool === state.editTool));
}

function handleEditUpload(file) {
  if (!file || !file.type.startsWith("image/")) return;
  const reader = new FileReader();
  reader.onload = (e) => {
    state.editImage = e.target.result;
    renderEditCanvas();
  };
  reader.readAsDataURL(file);
}

function renderEditCanvas() {
  const canvas = $("#editCanvas");
  if (!canvas) return;
  if (!state.editImage) {
    canvas.innerHTML = `<div class="edit-empty">拖入或选择图片开始编辑</div>`;
    return;
  }
  canvas.innerHTML = `<img src="${escapeHtml(state.editImage)}" alt="">`;
}

function editApply() {
  if (!state.editImage) { toast("请先上传图片。", "error"); return; }
  toast("编辑能力对接中。");
}
function initDropzone(selector, onFiles) {
  const el = document.querySelector(selector);
  if (!el) return;
  el.addEventListener("dragover", (e) => { e.preventDefault(); el.classList.add("drag"); });
  el.addEventListener("dragleave", () => el.classList.remove("drag"));
  el.addEventListener("drop", (e) => {
    e.preventDefault();
    el.classList.remove("drag");
    const files = e.dataTransfer?.files;
    if (files && files.length) onFiles(files);
  });
}
initDropzone("#composerWrap", addComposerFiles);
initDropzone("#studioRefWrap", addStudioRefs);
initDropzone("#editWrap", (files) => handleEditUpload(files[0]));
