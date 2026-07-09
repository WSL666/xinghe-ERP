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
 *   · renderProducts() 每行恰好输出 10 个 <td>，空状态行用 colspan="10"。
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
  previewItems: [],
  previewIndex: 0,
  previewImportId: null,
};
// 图片编辑: 预览弹窗操作(按钮); 拖拽开关(默认开, 存 localStorage)
state.dragEnabled = localStorage.getItem("dragEnabled") !== "0";
// AI创作中心运行时状态
state.composerAttachments = [];
state.studioRefs = [];
state.editTool = "background";
state.editImage = null;
// 勾选的商品 id 集合(独立于 DOM, 轮询刷新不丢失)
state.selectedIds = new Set();

const views = {
  dashboard: { title: "商品 AI 管线", eyebrow: "工作台" },
  products: { title: "TEMU采集箱", eyebrow: "商品采集箱" },
  box1688: { title: "1688采集箱", eyebrow: "商品采集箱" },
  boxOzon: { title: "OZON采集箱", eyebrow: "商品采集箱" },
  exportedAll: { title: "已导出", eyebrow: "已导出" },
  errorBox: { title: "错误汇总", eyebrow: "失败/错误" },
  insufficientBox: { title: "金豆不足", eyebrow: "待充值" },
  recharge: { title: "钱包", eyebrow: "账户" },
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
  exportedAll: "products",
  errorBox: "products",
  insufficientBox: "products",
};
/**
 * 每个采集箱对应的平台值（与后端 imports.platform 一致，统一小写）。
 * products=TEMU, box1688=1688, boxOzon=ozon。dashboard 等非采集箱视图不设平台。
 */
const PLATFORM_BY_VIEW = {
  products: "temu",
  box1688: "1688",
  boxOzon: "ozon",
  exportedTemu: "temu",
  exported1688: "1688",
  exportedOzon: "ozon",
};
// 已导出箱视图集合(复用 products 面板, 但请求带 exported=true)
const EXPORTED_VIEWS = { exportedAll: true };
const ERROR_VIEW = "errorBox";
const INSUFFICIENT_VIEW = "insufficientBox";
const PLATFORM_LABEL = { temu: "TEMU", "1688": "1688", ozon: "OZON" };

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

// Top-level safe clipboard copy. navigator.clipboard is undefined over plain
// HTTP LAN (https/localhost only); fall back to a hidden textarea + execCommand.
async function downloadPlugin() {
  const response = await fetch(apiUrl("/api/temu/plugin/download"), { credentials: "include" });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail = data.detail && data.detail.error ? data.detail.error : data.error;
    throw new Error(detail || `下载失败: ${response.status}`);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const filename = parseFilename(disposition, "temu-collector.zip");
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
  toast("采集插件已下载。");
}

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

const SHOP_FIELDS = [
  "origin", "shipping", "site", "shopName",
  "length", "width", "height", "weight",
  "declarePrice", "retailPrice", "stock", "deliveryDays",
  "skuClass", "skuClassQty", "skuClassUnit"
];
const SHOP_CONFIG_KEY = "xinghe_shop_config";

function getShopConfig() {
  try { return JSON.parse(localStorage.getItem(SHOP_CONFIG_KEY) || "{}") || {}; }
  catch { return {}; }
}

function saveShopConfig(cfg) {
  localStorage.setItem(SHOP_CONFIG_KEY, JSON.stringify(cfg));
}

function loadShopConfigForm() {
  const cfg = getShopConfig();
  document.querySelectorAll("#shopConfigForm input[data-shop]").forEach((inp) => {
    inp.value = cfg[inp.dataset.shop] || "";
  });
}

function collectShopConfigFromForm() {
  const cfg = {};
  document.querySelectorAll("#shopConfigForm input[data-shop]").forEach((inp) => {
    const v = (inp.value || "").trim();
    if (v) cfg[inp.dataset.shop] = v;
  });
  return cfg;
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
  $("#appView").classList.remove("is-hidden");
  syncApiText();
  loadAISettings();
  setView(state.view);
  refreshData({ silent: true });
  startLiveSync();
}



const _MASK = "••••••••••••";

function _applyMask(el) {
  if (!el) return;
  const real = el.dataset.real || "";
  el.dataset.visible = "0";
  el.value = real ? _MASK : "";
  el.type = "text";
}

function _revealReal(el) {
  if (!el) return;
  el.dataset.visible = "1";
  el.value = el.dataset.real || "";
}

function syncApiText() {
  const urlEl = $("#pluginUrl");
  if (urlEl) { urlEl.dataset.real = state.apiBase || ""; _applyMask(urlEl); }
  const apiKeyInput = $("#pluginApiKey");
  if (apiKeyInput) { apiKeyInput.dataset.real = state.user?.api_key || ""; _applyMask(apiKeyInput); }
}

function setApiStatus(status, text) {
  const node = $("#apiStatus");
  node.className = `status-pill ${status}`;
  node.textContent = text;
}

function statusInfo(item) {
  const status = (item.status || "").trim();
  if (status === "done") return { cls: "ok", text: "已完成" };
  if (status === "generating") return { cls: "running", text: "生成中" };
  if (status === "queued") return { cls: "queued", text: "排队中" };
  if (status === "error") return { cls: "error", text: "错误" };
  if (status === "insufficient") return { cls: "error", text: "金豆不足" };
  if (status === "collected") return { cls: "collected", text: "已采集" };
  return { cls: "collected", text: "已采集" };
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

function renderImageRows(originals, generated, item) {
  // 源图一行、AI 图一行（各自独立），每行最多 10 张，两行垂直堆叠
  const importId = item && item.id;
  const originalList = normalizeImageItems(originals).map((it) => ({ ...it, kind: "orig", importId }));
  const generatedList = (generated || []).map((g) => ({
    src: imageDownloadUrl(g.generated_image || g),
    title: g.image_type || "",
    kind: "ai",
    imageType: g.image_type || "",
    deleted: !!g.deleted,
    promoted: g.source === "manual_original",
    importId,
  })).filter((it) => it.src);

  // AI 行只显示未删除的; 已删的折叠到"已删除(N)"区域
  const aiVisible = generatedList.filter((g) => !g.deleted);
  const aiDeleted = generatedList.filter((g) => g.deleted);

  // 生成中: 只显示已完成的图(逐张弹出), 不显示转圈占位
  const isGenerating = item && (item.status === "generating" || item.status === "queued");
  const aiRow = isGenerating && generatedList.length === 0
    ? `<div class="image-strip img-strip-row empty-strip"><span class="empty-thumb">生成中</span></div>`
    : renderImageRow(aiVisible, "ai", "AI", importId, true);

  // 折叠区: 有已删图时显示"已删除(N)", 点击展开查看+还原
  const deletedSection = aiDeleted.length
    ? `<div class="deleted-fold">
         <button class="deleted-toggle" data-action="toggle-deleted" data-import-id="${importId || ""}" type="button">已删除(${aiDeleted.length})</button>
         <div class="deleted-strip" hidden>${renderImageRow(aiDeleted, "ai", "已删", importId, false)}</div>
       </div>`
    : "";

  return `
    <div class="image-row-stack" data-import-id="${importId || ""}">
      ${renderImageRow(originalList, "orig", "源", importId, false)}
      ${aiRow}
      ${deletedSection}
    </div>
  `;
}

// 渲染单行图片：固定每行最大 MAX_IMAGES 张（含 +N 占位框）。永不换行、永不撑高
function renderImageRow(list, kind, label, importId, isDropTarget = false) {
  const MAX_IMAGES = 10;
  if (!list.length) {
    return `<div class="image-strip img-strip-row empty-strip"><span class="empty-thumb">${label}图</span></div>`;
  }
  // 编码完整 item 信息(含 kind/imageType/deleted/promoted), 供预览弹窗定位操作目标
  const encoded = encodeURIComponent(JSON.stringify(list));
  const exceed = list.length > MAX_IMAGES;
  const overflow = exceed ? list.length - (MAX_IMAGES - 1) : 0;
  const shown = list.slice(0, exceed ? MAX_IMAGES - 1 : MAX_IMAGES);
  const tiles = shown.map((item, index) => {
    const badge = item.promoted ? `<span class="tile-badge orig">源</span>` : `<span class="tile-badge ${kind}">${label}</span>`;
    const dragAttr = (kind === "orig" && state.dragEnabled) ? `draggable="true" data-drag="orig-img"` : "";
    return `
    <button class="image-tile" type="button"
      data-action="preview"
      data-src="${escapeHtml(item.src)}"
      data-index="${index}"
      data-items="${encoded}"
      data-import-id="${importId || ""}"
      title="${escapeHtml(item.title)}" ${dragAttr}>
      <img src="${escapeHtml(item.src)}" loading="lazy" alt="">
      ${badge}
    </button>`;
  }).join("");
  const more = exceed
    ? `<span class="image-tile tile-more" title="共 ${list.length} 张${label}图"><span>+${overflow}</span></span>`
    : "";
  const dropAttrs = isDropTarget ? ` data-drop-target="ai-row" data-import-id="${importId || ""}"` : "";
  return `<div class="image-strip img-strip-row"${dropAttrs}>${tiles}${more}</div>`;
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
  return state.imports;
}

function renderStats() {
  $("#statTotal").textContent = state.imports.length;
  $("#statDone").textContent = state.imports.filter((item) => (item.status || "") === "done").length;
  $("#statImages").textContent = state.imports.reduce((sum, item) => sum + generatedOk(item).length, 0);
  const statusActive = (s) => s === "queued" || s === "generating";
  $("#statRunning").textContent = state.imports.filter((item) => statusActive((item.status || "").trim())).length;
  $("#statFailed").textContent = state.imports.filter((item) => {
    const s = (item.status || "").trim();
    return s === "error" || s === "insufficient";
  }).length;
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
          <small>编号 ${escapeHtml(item.ref_code || item.id)} - ${escapeHtml(item.created_at || "")}</small>
        </div>
        <span class="badge ${status.cls}">${status.text}</span>
      </article>
    `;
  }).join("") : `<div class="empty-state">暂无商品。请通过浏览器插件采集并发送商品数据。</div>`;
}

function formatTimeRange(item) {
  const created = String(item.created_at || "");
  const started = String(item.started_at || "");
  const finished = String(item.finished_at || "");
  if (!created) return `<span class="muted-cell">-</span>`;

  function _dur(t0s, t1s) {
    const t0 = new Date(t0s.replace(" ", "T"));
    const t1 = new Date(t1s.replace(" ", "T"));
    if (isNaN(t0) || isNaN(t1)) return "";
    const diff = Math.max(0, Math.round((t1 - t0) / 60000));
    return diff >= 60 ? (Math.round(diff / 6) / 10) + "小时" : diff + "分钟";
  }

  // 三态:
  // 1) 还没开始(排队中) → 只显示采集时间
  // 2) 正在跑(有 started 没 finished) → 显示开始时间
  // 3) 完成(有 started 和 finished) → 开始时间至结束时间(耗时)
  const date = (started || created).slice(0, 10);
  if (!started) {
    // 排队中: 显示采集时间
    const hm = created.slice(11, 16);
    return `<div class="time-cell"><span>${escapeHtml(date)}</span><span>${escapeHtml(hm)}</span></div>`;
  }
  const startHM = started.slice(11, 16);
  if (!finished) {
    // 正在跑: 显示开始时间
    return `<div class="time-cell"><span>${escapeHtml(date)}</span><span>${escapeHtml(startHM)}</span></div>`;
  }
  // 完成: 开始至结束 + 耗时(纯执行时间, 不含排队)
  const finishHM = finished.slice(11, 16);
  const mins = _dur(started, finished);
  return `<div class="time-cell"><span>${escapeHtml(date)}</span><span>${escapeHtml(startHM)}至${escapeHtml(finishHM)}</span>${mins ? `<span>（耗时${escapeHtml(mins)}）</span>` : ""}</div>`;
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

// AI 状态徽标
function renderAIBadges(item) {
  const aiStatus = (item.status || "").trim();
  const features = item.ai_features || [];
  if (!features.length && !aiStatus) return "";
  const titleDone = item.step2_done ? "done" : (aiStatus && features.includes("title") ? aiStatus : "");
  const imagesDone = item.step3_done ? "done" : (aiStatus && features.includes("images") ? aiStatus : "");
  let html = "";
  if (features.includes("title") || titleDone) {
    const cls = titleDone === "done" ? "done" : (aiStatus || "idle");
    const icon = cls === "done" ? "✅" : cls === "generating" ? "⏳" : cls === "queued" ? "⏳" : cls === "error" ? "❌" : cls === "insufficient" ? "🔴" : "⬜";
    html += `<span class="ai-badge ${cls}" title="AI标题">🏷️${icon}</span>`;
  }
  if (features.includes("images") || imagesDone) {
    const cls = imagesDone === "done" ? "done" : (aiStatus || "idle");
    const icon = cls === "done" ? "✅" : cls === "generating" ? "⏳" : cls === "queued" ? "⏳" : cls === "error" ? "❌" : cls === "insufficient" ? "🔴" : "⬜";
    html += `<span class="ai-badge ${cls}" title="AI生图">🖼️${icon}</span>`;
  }
  if (!html && aiStatus === "insufficient") {
    html += `<span class="ai-badge insufficient">🔴金豆不足</span>`;
  }
  return html;
}

function renderProducts() {
  const rows = filteredImports();
  const prevChecked = state.selectedIds || new Set();
  // 清掉已不存在的 id(被删除/筛选掉的)
  const liveIds = new Set(rows.map((r) => Number(r.id)));
  const isExported = !!EXPORTED_VIEWS[state.view];
  const isError = state.view === ERROR_VIEW;
  const isInsufficient = state.view === INSUFFICIENT_VIEW;
  for (const id of [...prevChecked]) {
    if (!liveIds.has(id)) prevChecked.delete(id);
  }
  $("#productRows").innerHTML = rows.length ? rows.map((item) => {
    const status = statusInfo(item);
    const origTitle = escapeHtml(item.title || "未命名商品");
    const cnTitle = item.cn_title ? escapeHtml(item.cn_title) : `<span class="muted-cell">待优化</span>`;
    const enTitle = item.en_title ? escapeHtml(item.en_title) : `<span class="muted-cell">待翻译</span>`;
    const generated = generatedOk(item);
    return `
      <tr>
        <td class="cell-check"><input type="checkbox" class="row-check" value="${item.id}" ${prevChecked.has(Number(item.id)) ? "checked" : ""}></td>
        <td>${PLATFORM_LABEL[item.platform] || escapeHtml(item.platform || "")}</td>
        <td>
          <div class="product-title">
            <div class="title-line title-orig" title="${origTitle}"><span>源</span>${origTitle}</div>
            <div class="title-line title-ai" title="${escapeHtml(item.cn_title || "")}"><span>新</span>${cnTitle}</div>
            <div class="title-line title-en" title="${escapeHtml(item.en_title || "")}"><span>英</span>${enTitle}</div>
            <div class="ref-row"><small class="ref-badge">ID: ${escapeHtml(item.ref_code || item.id)}</small><button class="ref-copy" data-action="copy-ref" data-ref="${escapeHtml(item.ref_code || item.id)}" title="复制编号" type="button">复制</button></div>
          </div>
        </td>
        <td><span class="badge ${status.cls}" title="${escapeHtml(item.status_msg || "")}">${status.text}</span><div class="ai-badges">${renderAIBadges(item)}</div></td>
        <td>${renderImageRows(item.gallery_images || [], generated, item)}</td>
        <td>${renderSpecCell(item)}</td>
        <td>${renderVideoStrip(item)}</td>
        <td>${renderSizeCell(item)}</td>
        <td>${formatTimeRange(item)}</td>
        <td>
          <div class="row-actions">
            <button data-action="ai-edit" data-id="${item.id}">AI编辑</button>
            ${isError || isInsufficient ? `<button data-action="retry" data-id="${item.id}">重试</button><button data-action="restore" data-id="${item.id}">移回采集箱</button>` : isExported ? `<button data-action="reexport" data-id="${item.id}">重新导出</button><button data-action="unexport" data-id="${item.id}">移回采集箱</button>` : `<button data-action="export" data-id="${item.id}">导出</button>`}
            <button class="danger" data-action="delete" data-id="${item.id}">删除</button>
          </div>
        </td>
      </tr>
    `;
  }).join("") : `<tr><td colspan="10"><div class="empty-state">没有匹配的商品。</div></td></tr>`;
}

function renderAll() {
  renderStats();
  renderRecent();
  renderProducts();
  // 同步表头全选框: 当前页全部勾选时才打勾
  const liveCbs = document.querySelectorAll(".row-check");
  const allChecked = liveCbs.length > 0 && Array.from(liveCbs).every((cb) => state.selectedIds.has(Number(cb.value)));
  const selAll = $("#selectAll");
  if (selAll) selAll.checked = allChecked;
  if (typeof updateBatchState === "function") updateBatchState();
}

function setView(name) {
  const oldView = state.view;
  state.view = views[name] ? name : "dashboard";
  $$(".view-panel").forEach((panel) => panel.classList.remove("active"));
  const panelName = PANEL_ALIAS[state.view] || state.view;
  $(`#${panelName}Panel`).classList.add("active");
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === state.view));
  $("#pageTitle").textContent = views[state.view].title;
  $("#pageEyebrow").textContent = views[state.view].eyebrow;
  const prevPlatform = state.platform;
  const prevIsExported = !!EXPORTED_VIEWS[oldView];
  state.platform = PLATFORM_BY_VIEW[state.view] || null;
  const nowIsExported = !!EXPORTED_VIEWS[state.view];
  if (panelName === "products" && (prevPlatform !== state.platform || prevIsExported !== nowIsExported || oldView !== state.view)) {
    refreshData({ silent: true });
  }
  if (state.view === "recharge") updateRechargePanel();
  syncNavGroup();

  if (panelName === "products") loadAISettings();
  if (name === "settings") loadShopConfigForm();
}

async function updateRechargePanel() {
  const u = state.user || {};
  const uidEl = $("#rechargeUid");
  if (uidEl) uidEl.textContent = u.uid || "-";
  let beans = 0;
  try {
    const data = await apiFetch("/api/billing/balance");
    beans = data.beans != null ? data.beans : 0;
  } catch (e) {
    beans = u.beans != null ? u.beans : 0;
  }
  const beansEl = $("#beansBalance");
  if (beansEl) beansEl.textContent = beans;
  // 账单明细
  try {
    const txData = await apiFetch("/api/billing/transactions?limit=30");
    const rows = txData.transactions || [];
    const tbody = $("#billingRows");
    if (!tbody) return;
    if (!rows.length) {
      tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#94a3b8;padding:24px">暂无记录</td></tr>';
      return;
    }
    tbody.innerHTML = rows.map(tx => {
      const amt = tx.amount;
      const sign = amt > 0 ? "+" : "";
      const cls = amt > 0 ? "tx-in" : "tx-out";
      const time = (tx.created_at || "").replace("T", " ").slice(0, 16);
      const reason = escapeHtml(tx.reason || "");
      const ref = tx.ref_code || "";
      const refCell = ref
        ? `<div class="billing-ref-row"><span class="billing-ref">${escapeHtml(ref)}</span><button class="ref-copy" data-action="copy-ref" data-ref="${escapeHtml(ref)}" type="button">复制</button></div>`
        : "";
      return `<tr>
        <td>${time}</td>
        <td><span class="tx-tag ${cls}">${amt > 0 ? "充值" : "消费"}</span></td>
        <td class="${cls}">${sign}${amt}</td>
        <td>${tx.balance_after}</td>
        <td>${refCell}</td>
        <td>${reason}</td>
      </tr>`;
    }).join("");
  } catch (e) {}
}

async function refreshData({ silent = false } = {}) {
  try {
    const isExported = !!EXPORTED_VIEWS[state.view];
    const isError = state.view === ERROR_VIEW;
    const isInsufficient = state.view === INSUFFICIENT_VIEW;
    const qs = [];
    if (state.platform) qs.push(`platform=${encodeURIComponent(state.platform)}`);
    if (isExported) qs.push("exported=true");
    if (isError) qs.push("error=true");
    if (isInsufficient) qs.push("insufficient=true");
    const url = qs.length ? `/api/temu/imports?${qs.join("&")}` : "/api/temu/imports";
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
    const s = (item.status || "").trim();
    return s === "queued" || s === "generating";
  }).length;
}

function _importsSignature() {
  return state.imports
    .map((i) => `${i.id}:${i.status || ""}:${i.step2_done}:${i.step3_done}:${i.step4_done}:${i.updated_at || ""}`)
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

async function loadCurrentUser() {
  const data = await apiFetch("/api/auth/me");
  setSession(data.user);
  return data.user;
}


async function runAIPipeline(id, features) {
  // 手动触发某条链接的 AI 处理 (标题/生图/全链路)
  try {
    await apiFetch(`/api/temu/imports/${id}/ai-run`, {
      method: "POST",
      body: JSON.stringify({ features }),
    });
    toast("AI任务已加入队列。");
    state.selectedIds && state.selectedIds.delete(Number(id));
    await refreshData({ silent: true });
  } catch (error) {
    toast(error.message || "AI任务启动失败。", "error");
  }
}

async function loadAISettings() {
  try {
    const data = await apiFetch("/api/temu/ai-settings");
    const master = $("#aiMasterToggle");
    if (master) master.checked = !!(data.ai_title_enabled || data.ai_images_enabled);
  } catch {}
}

async function saveAISettings() {
  const master = $("#aiMasterToggle");
  if (!master) return;
  const on = master.checked;
  if (on) {
    if (!confirm("开启后，新采集的商品将自动运行 AI全链路（标题+生图），每条扣 11 金豆。确定开启？")) {
      master.checked = false;
      return;
    }
  }
  try {
    await apiFetch("/api/temu/ai-settings", {
      method: "POST",
      body: JSON.stringify({
        ai_title_enabled: on,
        ai_images_enabled: on,
      }),
    });
    toast(on ? "已开启AI自动处理。" : "已关闭AI自动处理。");
    if (on) {
      // 开启后自动批量处理已采集的老数据
      setTimeout(() => batchAIProcess(), 500);
    }
  } catch (error) {
    toast(error.message || "AI设置保存失败。", "error");
  }
}

// 统一解析 Content-Disposition 中的文件名(优先 UTF-8 编码的 filename*)
function parseFilename(disposition, fallback) {
  const star = (disposition.match(/filename\*=UTF-8''([^;]+)/i) || [])[1];
  if (star) { try { return decodeURIComponent(star); } catch {} }
  const plain = (disposition.match(/filename="?([^";]+)"?/i) || [])[1];
  return plain || fallback;
}

async function exportItem(id) {
  const response = await fetch(apiUrl(`/api/temu/imports/${id}/export`), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ shopConfig: getShopConfig() }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `Export failed: ${response.status}`);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const filename = parseFilename(disposition, `final_result_${id}.xlsx`);
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
      await markExportedAfterSave(id);
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
  await markExportedAfterSave(id);
}

async function markExportedAfterSave(id) {
  // 文件保存确认后才归档(只标记 done 的)。失败不阻塞, 静默提示。
  try {
    const r = await apiFetch(`/api/temu/imports/${id}/mark-exported`, { method: "POST" });
    if (r && r.marked) {
      state.selectedIds && state.selectedIds.delete(Number(id));
      await refreshData({ silent: true });
    }
  } catch {}
}

async function reexportItem(id) {
  // 已导出箱里"重新导出": 走单条导出(不改变 exported 状态, 仍是已导出)
  const response = await fetch(apiUrl(`/api/temu/imports/${id}/export`), {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ shopConfig: getShopConfig() }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `Export failed: ${response.status}`);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const filename = parseFilename(disposition, `export_${id}.xlsx`);
  if (window.showSaveFilePicker) {
    try {
      const handle = await window.showSaveFilePicker({
        suggestedName: filename,
        types: [{ description: "Excel workbook", accept: { "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"] } }]
      });
      const writable = await handle.createWritable();
      await writable.write(blob);
      await writable.close();
      toast("导出已保存。");
      return;
    } catch (error) {
      if (error && error.name === "AbortError") { toast("导出已取消。"); return; }
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

async function retryItem(id) {
  // 错误箱「重试」: 重新触发全链路 AI
  try {
    await apiFetch(`/api/temu/imports/${id}/ai-run`, {
      method: "POST",
      body: JSON.stringify({ features: ["title", "images"] }),
    });
    toast("已重新加入队列。");
    state.selectedIds && state.selectedIds.delete(Number(id));
    await refreshData({ silent: true });
  } catch (error) {
    toast(error.message || "重试失败。", "error");
  }
}

async function restoreItem(id) {
  // 错误箱「移回采集箱」: 仅重置状态为 pending, 不自动跑
  try {
    await apiFetch(`/api/temu/imports/${id}/restore`, { method: "POST" });
    toast("已移回收采箱。");
    state.selectedIds && state.selectedIds.delete(Number(id));
    await refreshData({ silent: true });
  } catch (error) {
    toast(error.message || "操作失败。", "error");
  }
}

async function unexportItem(id) {
  // 移回收采箱: 取消已导出标记
  try {
    await apiFetch(`/api/temu/imports/${id}/unexport`, { method: "POST" });
    toast("已移回收采箱。");
    state.selectedIds && state.selectedIds.delete(Number(id));
    await refreshData({ silent: true });
  } catch (error) {
    toast(error.message || "操作失败。", "error");
  }
}

async function batchRetry() {
  const ids = selectedIds();
  if (!ids.length) { toast("请先勾选商品。", "warn"); return; }
  toggleBatchMenu(false);
  toast(`正在重试 ${ids.length} 个...`);
  let ok = 0;
  for (const id of ids) {
    try {
      await apiFetch(`/api/temu/imports/${id}/ai-run`, {
        method: "POST",
        body: JSON.stringify({ features: ["title", "images"] }),
      });
      state.selectedIds && state.selectedIds.delete(Number(id));
      ok++;
    } catch {}
  }
  await refreshData({ silent: true });
  toast(`已重新加入队列 ${ok} 个。`);
}

async function batchAIProcess() {
  // 全自动批量AI处理: 从下拉框读并发数, 扫描所有"已采集未跑AI"的链接, 全链路分批跑完
  const concurrency = parseInt($("#aiConcurrency")?.value || "1", 10);
  const features = ["title", "images"];
  toast("正在扫描已采集商品...");
  let pending = [];
  try {
    const data = await apiFetch("/api/temu/imports");
    pending = (data.imports || []).filter((item) => {
      const s = (item.status || "").trim();
      const hasTitle = !!item.step2_done;
      const hasImages = !!item.step4_done || (item.generated_json && item.generated_json.length > 0);
      // 跳过已有结果(标题/图片)和正在运行中的
      return !hasTitle && !hasImages && s !== "queued" && s !== "generating" && s !== "done";
    }).map((item) => item.id);
  } catch {
    toast("获取列表失败。", "error");
    return;
  }
  if (!pending.length) {
    toast("没有待处理的商品。");
    return;
  }
  toast(`开始批量AI处理: 共 ${pending.length} 条, 并发 ${concurrency}...`);
  let ok = 0, fail = 0;
  for (let i = 0; i < pending.length; i += concurrency) {
    const batch = pending.slice(i, i + concurrency);
    const results = await Promise.allSettled(
      batch.map((id) =>
        apiFetch(`/api/temu/imports/${id}/ai-run`, {
          method: "POST",
          body: JSON.stringify({ features }),
        })
      )
    );
    for (let j = 0; j < results.length; j++) {
      if (results[j].status === "fulfilled") ok++;
      else fail++;
    }
    await refreshData({ silent: true });
  }
  toast(`批量AI处理完成: 成功 ${ok}, 失败 ${fail}。`);
}

async function markBulkExported(ids) {
  // 文件保存确认后才归档。后端只标记 status=done 的, 返回实际归档数。
  try {
    const r = await apiFetch("/api/temu/imports/bulk/mark-exported", {
      method: "POST",
      body: JSON.stringify({ ids }),
    });
    const marked = (r && r.marked) || 0;
    const total = (r && r.total) || ids.length;
    const skipped = total - marked;
    for (const id of ids) state.selectedIds && state.selectedIds.delete(Number(id));
    await refreshData({ silent: true });
    if (skipped > 0) {
      toast(`已导出 ${marked} 个，${skipped} 个未完成已跳过。`, "warn");
    } else {
      toast(`已导出 ${marked} 个商品。`);
    }
  } catch {
    toast("文件已下载，但归档失败，可稍后重试。", "warn");
  }
}

async function deleteItem(id) {
  if (!confirm(`确认删除导入 #${id} 吗？`)) return;
  await apiFetch(`/api/temu/imports/${id}`, { method: "DELETE" });
  toast("已删除该导入。");
  await refreshData({ silent: true });
}

function selectedIds() {
  return Array.from(state.selectedIds || new Set());
}

function updateBatchState() {
  const has = selectedIds().length > 0;
  const isError = state.view === ERROR_VIEW;
  const isInsufficient = state.view === INSUFFICIENT_VIEW;
  const showRetry = isError || isInsufficient;
  $("#batchDeleteBtn").disabled = !has;
  if (showRetry) {
    $("#batchExportBtn").hidden = true;
    const retry = $("#batchRetryBtn");
    if (retry) { retry.hidden = false; retry.disabled = !has; }
  } else {
    $("#batchExportBtn").hidden = false;
    $("#batchExportBtn").disabled = !has;
    const retry = $("#batchRetryBtn");
    if (retry) retry.hidden = true;
  }
}

function toggleBatchMenu(open) {
  const menu = $("#batchMenu");
  const willOpen = open ?? menu.hidden;
  menu.hidden = !willOpen;
}

async function batchExport() {
  const ids = selectedIds();
  if (!ids.length) { toast("请先勾选商品。", "warn"); return; }
  toggleBatchMenu(false);
  toast(`正在导出 ${ids.length} 个商品...`);
  try {
    const response = await fetch(apiUrl("/api/temu/imports/bulk/export"), {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids, shopConfig: getShopConfig() }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || `导出失败: ${response.status}`);
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const filename = parseFilename(disposition, ids.length === 1 ? `final_result_${ids[0]}.xlsx` : "exports.zip");
    const doneIds = ids.slice();
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
        await markBulkExported(doneIds);
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
    await markBulkExported(doneIds);
  } catch (error) {
    toast(error.message || "批量导出失败。", "error");
  }
}

async function batchDelete() {
  const ids = selectedIds();
  if (!ids.length) { toast("请先勾选商品。", "warn"); return; }
  toggleBatchMenu(false);
  if (!confirm(`确认批量删除选中的 ${ids.length} 个商品吗？此操作不可恢复。`)) return;
  try {
    const data = await apiFetch("/api/temu/imports/bulk/delete", {
      method: "POST",
      body: JSON.stringify({ ids }),
    });
    const msg = `已删除 ${data.deleted} 个` + (data.missing?.length ? `，未找到 ${data.missing.length} 个` : "");
    toast(msg);
    $("#selectAll").checked = false;
    if (state.selectedIds) state.selectedIds.clear();
    await refreshData({ silent: true });
  } catch (error) {
    toast(error.message || "批量删除失败。", "error");
  }
}

function openAIEdit(id) {
  // AI编辑抽屉(合并详情): 展示商品 + 开关式手动触发 AI标题/AI生图
  const item = state.imports.find((entry) => String(entry.id) === String(id));
  if (!item) return;
  const drawer = $("#detailDrawer");
  const title = item.cn_title || item.title || "未命名商品";
  const aiStatus = (item.status || "").trim();
  const running = aiStatus === "queued" || aiStatus === "generating";
  const compactItem = {
    id: item.id,
    ref_code: item.ref_code,
    status: aiStatus,
    status_msg: item.status_msg,
    title: item.title,
    cn_title: item.cn_title,
    en_title: item.en_title,
    sku_count: item.sku_count,
    image_count: item.image_count,
    step2_done: item.step2_done,
    step3_done: item.step3_done,
    step4_done: item.step4_done,
    ai_features: item.ai_features,
    vision_json: item.vision_json,
    step_logs: item.step_logs,
    generated_json: item.generated_json
  };
  const titleDone = !!item.step2_done;
  const imagesDone = !!item.step4_done;
  const aiOpsHtml = running
    ? `<p class="hint">⏳ AI 正在处理中…</p>`
    : `<div class="ai-edit-section">
         <div class="ai-edit-block">
           <div class="ai-edit-block-head">
             <span>🏷️ AI标题</span>
             ${titleDone ? `<span class="ai-done-tag">✅ 已有AI标题</span>` : ""}
           </div>
           ${titleDone
             ? `<button class="ai-regen-btn" data-action="ai-run" data-id="${item.id}" data-features="title">🔄 重新生成</button>`
             : `<button class="ai-gen-btn" data-action="ai-run" data-id="${item.id}" data-features="title">AI生成</button>`}
         </div>
         <div class="ai-edit-block">
           <div class="ai-edit-block-head">
             <span>🖼️ AI生图</span>
             ${imagesDone ? `<span class="ai-done-tag">✅ 已有AI图片</span>` : ""}
           </div>
           ${imagesDone
             ? `<button class="ai-regen-btn" data-action="ai-run" data-id="${item.id}" data-features="images">🔄 重新生成</button>`
             : `<button class="ai-gen-btn" data-action="ai-run" data-id="${item.id}" data-features="images">AI生成</button>`}
         </div>
       </div>`;
  drawer.innerHTML = `
    <button class="ghost-btn small" data-action="close-drawer">关闭</button>
    <h3>${escapeHtml(title)}</h3>
    <p class="hint">编号 ${escapeHtml(item.ref_code || item.id)} · AI状态 ${escapeHtml(aiStatus || "未运行")}</p>
    <h4>AI 操作</h4>
    ${aiOpsHtml}
    <h4>AI 标题</h4>
    <div class="ai-title-result">
      <div class="title-line title-orig"><span>源</span>${escapeHtml(item.title || "无")}</div>
      <div class="title-line title-ai"><span>新</span>${item.cn_title ? escapeHtml(item.cn_title) : '<span class="muted-cell">待生成</span>'}</div>
      <div class="title-line title-en"><span>英</span>${item.en_title ? escapeHtml(item.en_title) : '<span class="muted-cell">待生成</span>'}</div>
    </div>
    <h4>图片</h4>
    ${renderImageRows(item.gallery_images || [], generatedOk(item), item)}
    <details class="debug-json">
      <summary>步骤日志</summary>
      ${renderStepLogs(item)}
    </details>
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

function openPreview(src, items = [], index = 0, importId = null) {
  // items: [{src, kind, imageType, deleted, promoted, importId}]
  if (!items.length) {
    items = [{ src, kind: "orig", deleted: false, promoted: false, importId }];
  }
  state.previewItems = items;
  state.previewImportId = importId;
  state.previewIndex = Math.max(0, Math.min(index, items.length - 1));
  renderPreview();
  $("#imageModal").classList.add("open");
  $("#imageModal").setAttribute("aria-hidden", "false");
}

function closePreview() {
  $("#imageModal").classList.remove("open");
  $("#imageModal").setAttribute("aria-hidden", "true");
  $("#modalImage").src = "";
  state.previewItems = [];
  state.previewIndex = 0;
  state.previewImportId = null;
}

function currentPreviewItem() {
  return state.previewItems[state.previewIndex] || null;
}

function renderPreview() {
  const item = currentPreviewItem();
  if (!item) return;
  $("#modalImage").src = item.src;
  // 类型标签
  const label = $("#modalTypeLabel");
  let txt = "";
  if (item.kind === "orig") txt = "源";
  else if (item.deleted) txt = "AI已删除";
  else if (item.promoted) txt = "源·转成品";
  else txt = "AI成品";
  if (label) label.textContent = txt;
  label.className = "modal-type-label" + (item.deleted ? " is-deleted" : "");

  // 工具栏: 按类型显示对应按钮
  const tb = $("#modalToolbar");
  if (!tb) return;
  const iid = item.importId || state.previewImportId;
  let btns = "";
  if (item.kind === "orig") {
    // 原图: 只能"用作成品"
    btns = `<button class="modal-tool-btn promote" data-action="ai-promote" data-import-id="${iid}" data-src="${escapeHtml(item.src)}">用作成品</button>`;
  } else if (item.deleted) {
    // AI已删: 只能"还原"
    btns = `<button class="modal-tool-btn restore" data-action="ai-restore" data-import-id="${iid}" data-image-type="${escapeHtml(item.imageType)}">还原</button>`;
  } else {
    // AI成品: 可以"删除"(含原图转成品的)
    btns = `<button class="modal-tool-btn delete" data-action="ai-delete" data-import-id="${iid}" data-image-type="${escapeHtml(item.imageType)}">删除</button>`;
  }
  tb.innerHTML = btns;
}

function shiftPreview(delta) {
  if (!state.previewItems.length) return;
  state.previewIndex = (state.previewIndex + delta + state.previewItems.length) % state.previewItems.length;
  renderPreview();
}

async function aiImageAction(action, importId, payload) {
  const ep = {
    promote: `/api/temu/imports/${importId}/ai-image/promote`,
    delete: `/api/temu/imports/${importId}/ai-image/delete`,
    restore: `/api/temu/imports/${importId}/ai-image/restore`,
  }[action];
  if (!ep) return;
  const data = await apiFetch(ep, { method: "POST", body: JSON.stringify(payload) });
  toast(action === "promote" ? "已用作成品图。" : action === "delete" ? "已删除，可在「已删除」中还原。" : "已还原。");
  // 局部更新内存中该商品的 generated_json, 避免整页刷新
  const imp = state.imports.find((it) => Number(it.id) === Number(importId));
  if (imp && data.generated) imp.generated_json = data.generated;
  await refreshData({ silent: true });
  // 操作后保持弹窗打开, 用最新数据重新构建预览列表(方便连续删除/还原)
  if (imp && state.previewItems.length) {
    const oldItem = currentPreviewItem();
    const fresh = rebuildPreviewItems(imp, oldItem);
    if (fresh) { state.previewItems = fresh.items; state.previewIndex = fresh.index; renderPreview(); }
  }
}

// 根据最新的 generated_json 重建弹窗预览列表, 尽量保持停留在用户当前看的图片
function rebuildPreviewItems(imp, oldItem) {
  const importId = imp.id;
  const orig = normalizeImageItems(imp.gallery_images || []).map((it) => ({ ...it, kind: "orig", importId }));
  const gen = (generatedOk(imp)).map((g) => ({
    src: imageDownloadUrl(g.generated_image || g),
    title: g.image_type || "",
    kind: "ai",
    imageType: g.image_type || "",
    deleted: !!g.deleted,
    promoted: g.source === "manual_original",
    importId,
  })).filter((it) => it.src);
  const all = orig.concat(gen);
  if (!all.length) return null;
  // 尝试停在原来的位置; 找不到就停第一张
  let idx = 0;
  if (oldItem) {
    const found = all.findIndex((it) => it.src === oldItem.src);
    if (found >= 0) idx = found;
  }
  return { items: all, index: idx };
}

async function copyText(value) {
  const ok = await copyTextSafe(value);
  toast(ok ? "已复制。" : "复制失败，请手动复制。");
}

function bindEvents() {

  const settingsForm = $("#settingsForm");
  if (settingsForm) {
    settingsForm.addEventListener("submit", (event) => event.preventDefault());
  }
  const shopConfigForm = $("#shopConfigForm");
  if (shopConfigForm) {
    shopConfigForm.addEventListener("submit", (event) => event.preventDefault());
    loadShopConfigForm();
  }



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
      if (action === "logout") {
        try {
          await apiFetch("/api/auth/logout", { method: "POST" });
        } catch {}
        clearSession();
        window.location.replace("/");
      }
      if (action === "play-video") openVideo(actionButton.dataset.src, actionButton.dataset.poster);
      if (action === "close-video") closeVideo();
      if (action === "preview") {
        let items = [];
        try {
          items = JSON.parse(decodeURIComponent(actionButton.dataset.items || "[]"));
        } catch {
          // 兼容旧调用(纯 url 数组)
          try {
            const urls = JSON.parse(decodeURIComponent(actionButton.dataset.images || "[]"));
            items = urls.map((u) => ({ src: u, kind: "orig", deleted: false, promoted: false }));
          } catch {}
        }
        const iid = actionButton.dataset.importId || null;
        if (!items.length) items = [{ src: actionButton.dataset.src, kind: "orig", deleted: false, promoted: false }];
        openPreview(actionButton.dataset.src, items, Number(actionButton.dataset.index || 0), iid);
      }
      if (action === "close-modal") closePreview();
      if (action === "prev-image") shiftPreview(-1);
      if (action === "next-image") shiftPreview(1);
      if (action === "toggle-deleted") {
        const strip = actionButton.nextElementSibling;
        if (strip) {
          const isHidden = strip.hasAttribute("hidden");
          if (isHidden) { strip.removeAttribute("hidden"); actionButton.classList.add("open"); }
          else { strip.setAttribute("hidden", ""); actionButton.classList.remove("open"); }
        }
      }
      if (action === "ai-promote") await aiImageAction("promote", actionButton.dataset.importId, { source_url: actionButton.dataset.src });
      if (action === "ai-delete") await aiImageAction("delete", actionButton.dataset.importId, { image_type: actionButton.dataset.imageType });
      if (action === "ai-restore") await aiImageAction("restore", actionButton.dataset.importId, { image_type: actionButton.dataset.imageType });
      if (action === "ai-edit") openAIEdit(id);
      if (action === "spec") openSpec(id);
      if (action === "close-drawer") closeDrawer();
      if (action === "ai-run") {
        const feats = (actionButton.dataset.features || "").split(",").filter(Boolean);
        const cost = feats.includes("images") ? "10" : "1";
        const label = feats.includes("images") ? "AI生图" : "AI标题";
        const isRegen = actionButton.classList.contains("ai-regen-btn");
        const prompt = isRegen
          ? `已有${label}，重新生成将覆盖原数据，本次扣 ${cost} 金豆，确定？`
          : `确认生成 ${label}？本次将扣 ${cost} 金豆。`;
        if (!confirm(prompt)) return;
        await runAIPipeline(id, feats);
        closeDrawer();
      }
      if (action === "export") await exportItem(id);
      if (action === "reexport") await reexportItem(id);
      if (action === "unexport") await unexportItem(id);
      if (action === "retry") await retryItem(id);
      if (action === "restore") await restoreItem(id);
      if (action === "delete") await deleteItem(id);
      if (action === "save-shop-config") { saveShopConfig(collectShopConfigFromForm()); toast("店铺配置已保存。"); }
      if (action === "reset-shop-config") {
        document.querySelectorAll("#shopConfigForm input[data-shop]").forEach((inp) => { inp.value = ""; });
        saveShopConfig({});
        toast("店铺配置已清空。");
      }
      if (action === "copy-plugin-url") { const _u = $("#pluginUrl"); const _ok = await copyTextSafe(_u ? (_u.dataset.real || "") : ""); toast(_ok ? "已复制。" : "复制失败，请手动复制。"); }
      if (action === "copy-ref") { const _ok = await copyTextSafe(actionButton.dataset.ref || ""); if (_ok) { const _t = actionButton.textContent; actionButton.textContent = "已复制"; setTimeout(() => { actionButton.textContent = _t; }, 1500); } }
      if (action === "copy-api-key") { const _inp = $("#pluginApiKey"); const _v = _inp ? (_inp.dataset.real || "") : ""; const _ok = await copyTextSafe(_v); if (_ok) { const _t = actionButton.textContent; actionButton.textContent = "已复制"; setTimeout(() => { actionButton.textContent = _t; }, 1500); } }
      if (action === "download-plugin") { const _btn = actionButton; const _t = _btn.textContent; _btn.textContent = "准备中…"; _btn.disabled = true; try { await downloadPlugin(); } catch (e) { toast(e.message || "下载失败", "error"); } finally { setTimeout(() => { _btn.textContent = _t; _btn.disabled = false; }, 1200); } }

      if (action === "toggle-eye") {
        const el = $("#" + actionButton.dataset.target);
        if (!el) return;
        if (el.dataset.visible === "1") {
          _applyMask(el);
          actionButton.textContent = "👁";
        } else {
          _revealReal(el);
          actionButton.textContent = "🙈";
        }
      }
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

  // --- 拖拽: 原图 → AI行 = 用作成品 (傻瓜式) ---
  document.addEventListener("dragstart", (event) => {
    const tile = event.target.closest("[data-drag=orig-img]");
    if (!tile) return;
    event.dataTransfer.setData("text/plain", JSON.stringify({
      src: tile.dataset.src,
      importId: tile.dataset.importId,
    }));
    event.dataTransfer.effectAllowed = "copy";
  });

  document.addEventListener("dragover", (event) => {
    const zone = event.target.closest("[data-drop-target=ai-row]");
    if (!zone) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    zone.classList.add("drag-over");
  });

  document.addEventListener("dragleave", (event) => {
    const zone = event.target.closest("[data-drop-target=ai-row]");
    if (zone && !zone.contains(event.relatedTarget)) zone.classList.remove("drag-over");
  });

  document.addEventListener("drop", async (event) => {
    const zone = event.target.closest("[data-drop-target=ai-row]");
    if (!zone) return;
    event.preventDefault();
    zone.classList.remove("drag-over");
    let payload = {};
    try { payload = JSON.parse(event.dataTransfer.getData("text/plain")); } catch {}
    const importId = payload.importId || zone.dataset.importId;
    if (!importId || !payload.src) return;
    try {
      await aiImageAction("promote", importId, { source_url: payload.src });
    } catch (error) {
      toast(error.message, "error");
    }
  });

  $("#selectAll").addEventListener("change", (event) => {
    const checked = event.target.checked;
    const sel = state.selectedIds || (state.selectedIds = new Set());
    document.querySelectorAll(".row-check").forEach((cb) => {
      const id = Number(cb.value);
      cb.checked = checked;
      if (checked) sel.add(id); else sel.delete(id);
    });
    updateBatchState();
  });

  $("#productRows").addEventListener("change", (event) => {
    if (event.target.classList.contains("row-check")) {
      const sel = state.selectedIds || (state.selectedIds = new Set());
      const id = Number(event.target.value);
      if (event.target.checked) sel.add(id); else sel.delete(id);
      // 同步表头
      const liveCbs = document.querySelectorAll(".row-check");
      const allChecked = liveCbs.length > 0 && Array.from(liveCbs).every((cb) => state.selectedIds.has(Number(cb.value)));
      const selAll = $("#selectAll");
      if (selAll) selAll.checked = allChecked;
      updateBatchState();
    }
  });

  $("#batchBtn").addEventListener("click", (event) => {
    event.stopPropagation();
    toggleBatchMenu();
  });

  // AI 开关: 切换即保存
  const aiMaster = $("#aiMasterToggle");
  if (aiMaster) aiMaster.addEventListener("change", saveAISettings);

  // 并发数选择: 切换后, 如果开关开着就自动触发批量AI处理
  const aiConc = $("#aiConcurrency");
  if (aiConc) aiConc.addEventListener("change", () => {
    if ($("#aiMasterToggle")?.checked) batchAIProcess();
  });


  $("#batchMenu").addEventListener("click", (event) => {
    const btn = event.target.closest("[data-batch]");
    if (!btn) return;
    const action = btn.dataset.batch;
    if (action === "select-all") {
      const checks = document.querySelectorAll(".row-check");
      const all = Array.from(checks).every((cb) => cb.checked);
      const sel = state.selectedIds || (state.selectedIds = new Set());
      $("#selectAll").checked = !all;
      checks.forEach((cb) => {
        const id = Number(cb.value);
        cb.checked = !all;
        if (!all) sel.add(id); else sel.delete(id);
      });
      updateBatchState();
    } else if (action === "export") {
      batchExport();
    } else if (action === "retry") {
      batchRetry();
    } else if (action === "delete") {
      batchDelete();
    }
  });

  document.addEventListener("click", (event) => {
    const wrap = $("#batchWrap");
    if (wrap && !wrap.contains(event.target)) toggleBatchMenu(false);
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
    window.location.replace("/");
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
