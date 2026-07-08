// content.js — 通快采集悬浮卡片 (注入到 Temu 页面)
// 负责: 悬浮卡片 UI + 拖动 + 点击采集 + 发送到管线 + 按钮状态反馈

// 请求全部通过 background service worker 转发(绕过 CORS)
function bgFetch(path, opts) {
  return chrome.runtime.sendMessage({ type: 'tk-fetch', path, method: opts.method, headers: opts.headers, body: opts.body });
}

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

// ========== 创建悬浮卡片 ==========
let fab, dot, collectBtn, beansEl, btnTimer;
function createFab() {
  if (document.getElementById('tk-fab')) return;

  fab = document.createElement('div');
  fab.id = 'tk-fab';
  fab.innerHTML = `
    <div class="tk-header">
      <img src="${chrome.runtime.getURL('tongkuai_image.png')}">
      <span class="tk-name">通快采集</span>
    </div>
    <div class="tk-body">
      <div class="tk-status-row"><span class="tk-dot gray"></span><span class="tk-status-text">检查中...</span></div>
      <div class="tk-beans"></div>
    </div>
    <button class="tk-collect-btn">📦 一键采集</button>
  `;
  (document.body || document.documentElement).appendChild(fab);
  dot = fab.querySelector('.tk-dot');
  collectBtn = fab.querySelector('.tk-collect-btn');
  beansEl = fab.querySelector('.tk-beans');

  // 采集按钮点击
  collectBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    if (collectBtn.disabled) return;
    doCollectAndSend();
  });

  // 拖动逻辑: 拖动顶部 header 区域
  const header = fab.querySelector('.tk-header');
  let isDragging = false;
  let hasMoved = false;
  let startX = 0, startY = 0;
  let offsetX = 0, offsetY = 0;

  header.addEventListener('mousedown', (e) => {
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
    fab.style.right = 'auto';
    fab.style.bottom = 'auto';
    const fw = fab.offsetWidth;
    const fh = fab.offsetHeight;
    fab.style.left = Math.max(0, Math.min(window.innerWidth - fw, e.clientX - offsetX)) + 'px';
    fab.style.top = Math.max(0, Math.min(window.innerHeight - fh, e.clientY - offsetY)) + 'px';
  });

  document.addEventListener('mouseup', () => {
    if (!isDragging) return;
    isDragging = false;
    fab.classList.remove('dragging');
  });

  updateFabStatus();
}

// ========== 设置按钮状态 ==========
function setBtnState(text, extraClass) {
  if (!collectBtn) return;
  clearTimeout(btnTimer);
  collectBtn.textContent = text;
  collectBtn.className = 'tk-collect-btn' + (extraClass ? ' ' + extraClass : '');
  if (extraClass === 'success' || extraClass === 'fail') {
    // 3秒后恢复
    btnTimer = setTimeout(() => {
      collectBtn.textContent = '📦 一键采集';
      collectBtn.className = 'tk-collect-btn';
      collectBtn.disabled = false;
    }, 3000);
  }
}

// ========== 更新卡片状态 ==========
async function updateFabStatus() {
  const apiKey = await getApiKey();
  const statusText = fab ? fab.querySelector('.tk-status-text') : null;
  if (!dot) return;
  if (!apiKey) {
    dot.className = 'tk-dot red';
    if (statusText) statusText.textContent = '未配置密钥';
    if (beansEl) beansEl.textContent = '点扩展图标填密钥';
    return;
  }
  dot.className = 'tk-dot gray';
  if (statusText) statusText.textContent = '查询中...';
  try {
    const res = await bgFetch('/api/billing/balance', {
      method: 'GET',
      headers: { 'Authorization': 'Bearer ' + apiKey },
    });
    if (res.ok) {
      const avail = res.data.available;
      const est = avail > 0 ? Math.floor((avail + 10) / 11) : 0;
      if (avail <= 0) {
        dot.className = 'tk-dot red';
        if (statusText) statusText.textContent = '金豆不足';
      } else if (avail <= 11) {
        dot.className = 'tk-dot yellow';
        if (statusText) statusText.textContent = '余额偏低';
      } else {
        dot.className = 'tk-dot green';
        if (statusText) statusText.textContent = '已连接';
      }
      if (beansEl) beansEl.textContent = '💰 可用 ' + avail + ' 豆（约' + est + '条）';
    } else if (res.status === 401) {
      dot.className = 'tk-dot red';
      if (statusText) statusText.textContent = '密钥无效';
    }
  } catch {
    dot.className = 'tk-dot gray';
    if (statusText) statusText.textContent = '查询失败';
  }
}

// ========== 核心: 采集 + 发送 (一键到底) ==========
async function doCollectAndSend() {
  // 按钮立即变「正在采集」
  collectBtn.disabled = true;
  setBtnState('⏳ 正在采集...', 'loading');

  // 1. 校验密钥
  const apiKey = await getApiKey();
  if (!apiKey) {
    setBtnState('❌ 未配置密钥', 'fail');
    return;
  }

  // 2. 注入 inject.js 读取 rawData
  let injectResult;
  try {
    injectResult = await new Promise((resolve) => {
      const timeout = setTimeout(() => resolve({ ok: false, error: '采集超时，请刷新页面重试' }), 8000);
      const handler = (event) => {
        if (event.source !== window) return;
        if (!event.data || event.data.source !== 'tk-collector') return;
        clearTimeout(timeout);
        window.removeEventListener('message', handler);
        resolve(event.data);
      };
      window.addEventListener('message', handler);
      const script = document.createElement('script');
      script.src = chrome.runtime.getURL('inject.js');
      script.onload = function () { this.remove(); };
      (document.head || document.documentElement).appendChild(script);
    });
  } catch (e) {
    setBtnState('❌ 采集失败', 'fail');
    return;
  }

  if (!injectResult || !injectResult.ok) {
    setBtnState('❌ ' + (injectResult?.error || '采集失败').slice(0, 10), 'fail');
    return;
  }

  // 3. 构建 payload 并发送
  const payload = buildPayload(injectResult.data);
  try {
    const res = await bgFetch('/api/temu/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + apiKey },
      body: payload,
    });
    const data = res.data || {};
    if (res.ok && data.ok) {
      setBtnState('✅ 采集成功', 'success');
      updateFabStatus();
    } else if (res.status === 402) {
      setBtnState('❌ 金豆不足', 'fail');
      updateFabStatus();
    } else {
      setBtnState('❌ 发送失败', 'fail');
    }
  } catch (e) {
    setBtnState('❌ 网络错误', 'fail');
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
