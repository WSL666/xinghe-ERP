// content.js — 通快采集悬浮按钮 (注入到 Temu 页面)
// 负责: 悬浮按钮 UI + 可拖动 + 点击采集 + 发送到管线 + 结果提示

const PIPELINE_URL = 'https://wangshilin888.com:8443';

// ========== 读取 API 密钥 (和 popup 共享 storage) ==========
async function getApiKey() {
  const result = await chrome.storage.local.get(['shopConfig']);
  return String((result.shopConfig || {}).apiKey || '').trim();
}

// ========== 注入 CSS ==========
function injectCSS() {
  if (document.getElementById('tk-fab-css')) return;
  const link = document.createElement('link');
  link.id = 'tk-fab-css';
  link.rel = 'stylesheet';
  link.href = chrome.runtime.getURL('content.css');
  (document.head || document.documentElement).appendChild(link);
}

// ========== 创建悬浮按钮 ==========
let fab, dot, toast;
function createFab() {
  if (document.getElementById('tk-fab')) return;

  fab = document.createElement('div');
  fab.id = 'tk-fab';
  fab.innerHTML = `<img src="${chrome.runtime.getURL('tongkuai_image.png')}"><span class="tk-dot gray"></span>`;
  (document.body || document.documentElement).appendChild(fab);
  dot = fab.querySelector('.tk-dot');

  toast = document.createElement('div');
  toast.id = 'tk-toast';
  (document.body || document.documentElement).appendChild(toast);

  // 拖动逻辑 (默认右下角, 可拖动, 区分点击/拖动)
  let isDragging = false;
  let hasMoved = false;
  let startX = 0, startY = 0;
  let offsetX = 0, offsetY = 0;

  fab.addEventListener('mousedown', (e) => {
    isDragging = true;
    hasMoved = false;
    startX = e.clientX;
    startY = e.clientY;
    const rect = fab.getBoundingClientRect();
    offsetX = e.clientX - rect.left;
    offsetY = e.clientY - rect.top;
    fab.classList.add('dragging');
    e.preventDefault();
  });

  document.addEventListener('mousemove', (e) => {
    if (!isDragging) return;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    if (Math.abs(dx) > 4 || Math.abs(dy) > 4) hasMoved = true;
    // 固定定位 → 切换为精确坐标
    fab.style.right = 'auto';
    fab.style.bottom = 'auto';
    fab.style.left = Math.max(0, Math.min(window.innerWidth - 52, e.clientX - offsetX)) + 'px';
    fab.style.top = Math.max(0, Math.min(window.innerHeight - 52, e.clientY - offsetY)) + 'px';
  });

  document.addEventListener('mouseup', () => {
    if (!isDragging) return;
    isDragging = false;
    fab.classList.remove('dragging');
  });

  fab.addEventListener('click', (e) => {
    if (hasMoved) return; // 拖动时不触发采集
    doCollectAndSend();
  });

  updateFabStatus();
}

// ========== 更新按钮状态点 ==========
async function updateFabStatus() {
  const apiKey = await getApiKey();
  if (!dot) return;
  if (!apiKey) {
    dot.className = 'tk-dot red';
    return;
  }
  dot.className = 'tk-dot gray';
  // 查余额 (1次轻量请求)
  try {
    const res = await fetch(PIPELINE_URL + '/api/billing/balance', {
      headers: { 'Authorization': 'Bearer ' + apiKey },
    });
    if (res.ok) {
      const data = await res.json();
      const avail = data.available;
      if (avail <= 0) dot.className = 'tk-dot red';
      else if (avail <= 11) dot.className = 'tk-dot yellow';
      else dot.className = 'tk-dot green';
    } else if (res.status === 401) {
      dot.className = 'tk-dot red';
    }
  } catch { dot.className = 'tk-dot gray'; }
}

// ========== 提示框 ==========
function showToast(type, html, autoCloseMs) {
  if (!toast) return;
  toast.className = 'tk-toast show ' + type;
  toast.innerHTML = html;
  clearTimeout(toast._timer);
  if (autoCloseMs) toast._timer = setTimeout(() => toast.classList.remove('show'), autoCloseMs);
}

// ========== 核心: 采集 + 发送 (一键到底) ==========
async function doCollectAndSend() {
  if (fab) fab.classList.add('loading');
  showToast('warn', '⏳ 正在采集并发送...');

  // 1. 校验密钥
  const apiKey = await getApiKey();
  if (!apiKey) {
    if (fab) fab.classList.remove('loading');
    showToast('error', '❌ 请先点击扩展图标 → 填写 API 密钥', 4000);
    return;
  }

  // 2. 注入 inject.js 到页面 MAIN world, 读取 rawData
  let injectResult;
  try {
    const [tab] = [0]; // placeholder, 不需要
    // content script 无法直接 executeScript 到自己页面, 用动态注入 script 标签
    injectResult = await new Promise((resolve) => {
      const timeout = setTimeout(() => resolve({ ok: false, error: '采集超时，请刷新页面重试' }), 8000);

      // 监听 inject.js 的 postMessage
      const handler = (event) => {
        if (event.source !== window) return;
        if (!event.data || event.data.source !== 'tk-collector') return;
        clearTimeout(timeout);
        window.removeEventListener('message', handler);
        resolve(event.data);
      };
      window.addEventListener('message', handler);

      // 注入 inject.js (MAIN world, 能访问 window.rawData)
      const script = document.createElement('script');
      script.src = chrome.runtime.getURL('inject.js');
      script.onload = function () { this.remove(); };
      (document.head || document.documentElement).appendChild(script);
    });
  } catch (e) {
    if (fab) fab.classList.remove('loading');
    showToast('error', '❌ 采集失败: ' + e.message, 5000);
    return;
  }

  if (!injectResult || !injectResult.ok) {
    if (fab) fab.classList.remove('loading');
    showToast('error', '❌ ' + (injectResult?.error || '采集失败'), 5000);
    return;
  }

  const collectedData = injectResult.data;

  // 3. 构建 payload (与 popup 发送逻辑完全一致)
  const payload = buildPayload(collectedData);

  // 4. 发送到管线
  try {
    const res = await fetch(PIPELINE_URL + '/api/temu/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + apiKey },
      body: JSON.stringify(payload),
    });
    const data = await res.json();

    if (fab) fab.classList.remove('loading');

    if (data.ok) {
      const balTip = (typeof data.available === 'number') ? `<br><span style="color:#888;font-size:11px;">💰 剩余 ${data.available} 金豆</span>` : '';
      showToast('success', `✅ <b>${(data.title || '').slice(0, 30)}</b><br>${data.sku_count} SKU · ${data.total_images} 图${balTip}`, 4000);
      updateFabStatus();
    } else if (res.status === 402) {
      showToast('error', `🔴 <b>金豆不足</b><br>${data.error || '余额不足'}<br><span style="color:#e74c3c;">请充值后继续</span>`, 5000);
      updateFabStatus();
    } else {
      showToast('error', '❌ 发送失败: ' + (data.error || '未知错误'), 5000);
    }
  } catch (e) {
    if (fab) fab.classList.remove('loading');
    showToast('error', '❌ 网络错误: ' + e.message, 5000);
  }
}

// ========== 构建 payload (与 popup.js 一致) ==========
function buildPayload(collectedData) {
  function cleanPrice(p) {
    if (!p) return '';
    const s = String(p).replace(/[^\d.]/g, '');
    const num = parseFloat(s);
    return isNaN(num) ? '' : String(num);
  }

  const now = new Date();
  const pad2 = n => String(n).padStart(2, '0');
  const localNow = now.getFullYear() + '-' + pad2(now.getMonth() + 1) + '-' + pad2(now.getDate()) + ' ' + pad2(now.getHours()) + ':' + pad2(now.getMinutes()) + ':' + pad2(now.getSeconds());

  const rawSummary = collectedData.rawSummary || {};
  const skuList = rawSummary.skuList || [];
  const specLevelNames = (collectedData.specTree || []).map(l => l.specKey);

  const skus = skuList.map(sku => {
    const specObj = sku.specObj || {};
    const specKeys = Object.keys(specObj);
    const rawSpecs = sku.rawSpecs || [];
    const skcAttr = JSON.stringify([{ parentSpecId: 0, parentSpecName: '', specId: 0, specName: '', previewImgUrls: sku.skcPreviewImg || sku.thumbUrl || '', extCode: '', productSkcId: sku.skcId || '' }]);
    const skuAttr = JSON.stringify(specKeys.map(k => {
      const rs = rawSpecs.find(s => s.specKey === k);
      return { specId: rs ? rs.specValueId : 0, parentSpecName: k, specName: specObj[k], parentSpecId: rs ? rs.specKeyId : 0 };
    }));
    return {
      variantName: sku.specs || '',
      specName1: specLevelNames[0] || '',
      specValue1: specKeys[0] ? specObj[specKeys[0]] : '',
      specName2: specLevelNames[1] || '',
      specValue2: specKeys[1] ? specObj[specKeys[1]] : '',
      previewImage: sku.thumbUrl || sku.specShowImageUrl || '',
      price: cleanPrice(sku.price) || cleanPrice(collectedData.priceRange),
      stock: sku.stock || 0,
      skcProps: skcAttr,
      skuProps: skuAttr,
      spuId: sku.spuId || collectedData.goodsId || '',
      skcId: sku.skcId || '',
      skuId: sku.skuId || '',
    };
  });

  if (skus.length === 0) {
    skus.push({
      variantName: '默认', specName1: '', specValue1: '', specName2: '', specValue2: '',
      previewImage: (collectedData.galleryImgs || [])[0] || '',
      price: cleanPrice(collectedData.priceRange), stock: 0,
      skcProps: '[]', skuProps: '[]',
      spuId: collectedData.goodsId || '', skcId: '', skuId: '',
    });
  }

  return {
    goodsId: collectedData.goodsId || '',
    platform: 'temu',
    categoryId: collectedData.categoryId || '',
    videoUrl: (collectedData.videos || []).map(v => v.url).join('\n'),
    videos: (collectedData.videos || []).map(v => ({ url: v.url || '', poster: v.poster || '', width: v.width || 0, height: v.height || 0 })),
    spec: {
      skuCount: skus.length,
      specLevels: specLevelNames,
      specTree: (collectedData.specTree || []).map(level => ({ specKey: level.specKey || '', values: (level.values || []).map(item => ({ specValue: item.specValue || '', imgUrl: item.imgUrl || '' })) })),
      productProps: (collectedData.goodsProperty || []).filter(p => !['商品编号', '产地'].includes((p.propName || p.key || '').trim())).map(p => ({ propName: p.propName || '', propValue: p.propValue || '' })),
    },
    createdAt: localNow,
    product: {
      title: collectedData.title || '',
      galleryImages: (collectedData.galleryImgs || []).slice(0, 10),
      firstImage: (collectedData.galleryImgs || [])[0] || '',
      productProps: (collectedData.goodsProperty || []).filter(p => !['商品编号', '产地'].includes((p.propName || p.key || '').trim())).map(p => ({
        propName: p.propName || '', refPid: p.refPid || '', pid: p.pid || '', templatePid: p.templatePid || '', numberInputValue: p.numberInputValue || '', valueUnit: p.valueUnit || '', vid: p.vid || '', propValue: p.propValue || ''
      })),
    },
    skus: skus,
  };
}

// ========== 初始化 ==========
function init() {
  injectCSS();
  createFab();
}

// 等 DOM ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}

// SPA 页面切换时重新检测 (Temu 是 SPA)
let lastUrl = location.href;
new MutationObserver(() => {
  if (location.href !== lastUrl) {
    lastUrl = location.href;
    setTimeout(init, 500);
  }
}).observe(document, { subtree: true, childList: true });
