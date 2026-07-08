// ========== API 密钥配置 ==========
let cachedShopConfig = {};

// 固定后端域名，所有人通过此域名连接（HTTPS，由 Caddy 反代到内网 6688）
const DEFAULT_PIPELINE_URL = 'https://wangshilin888.com:8443';

// URL 固定，API Key 从配置读取（发货/包装等配置已移到网站「设置」页，导出时读取）
function buildPipelineConfig(shopCfg) {
  return {
    url: DEFAULT_PIPELINE_URL,
    apiKey: String((shopCfg && shopCfg.apiKey) || '').trim(),
  };
}

function updateShopStatus() {
  chrome.storage.local.get(['shopConfig'], result => {
    const cfg = result.shopConfig || {};
    cachedShopConfig = cfg;
    const statusEl = document.getElementById('shop-status');
    if (cfg.apiKey) {
      statusEl.textContent = '已连接 ✓';
      statusEl.style.color = '#27ae60';
    } else {
      statusEl.textContent = '未配置';
      statusEl.style.color = '#aaa';
    }
  });
}

function toggleKeyPanel(show) {
  const panel = document.getElementById('key-panel');
  if (!panel) return;
  const willShow = show ?? (panel.style.display === 'none');
  panel.style.display = willShow ? 'flex' : 'none';
  if (willShow) {
    chrome.storage.local.get(['shopConfig'], result => {
      const el = document.getElementById('shop-api-key');
      if (el) el.value = (result.shopConfig || {}).apiKey || '';
      el && el.focus();
    });
  }
}

async function saveShopConfig() {
  const cfg = {};
  const el = document.getElementById('shop-api-key');
  const val = el ? el.value.trim() : '';
  if (val) cfg.apiKey = val;
  await chrome.storage.local.set({ shopConfig: cfg });
  cachedShopConfig = cfg;
  toggleKeyPanel(false);  // 保存后自动收起
  updateShopStatus();
  refreshBeansStatus();
}

// 获取店铺配置（供导出使用）
async function getShopConfig() {
  const result = await chrome.storage.local.get(['shopConfig']);
  return result.shopConfig || {};
}

// 绑定事件
// 可用金豆(开弹窗时查1次, 采集不重复查)
let cachedAvailableBeans = null;

async function refreshBeansStatus() {
  // 开弹窗/保存配置后调1次: 查可用余额, 更新提示 + 控制"发送到管线"按钮
  const cfg = await getShopConfig();
  const pipelineCfg = buildPipelineConfig(cfg);
  const el = document.getElementById('beans-status');
  const sendBtn = document.getElementById('sendPipelineBtn');
  if (!el) return;
  if (!pipelineCfg.apiKey) {
    el.textContent = '';
    cachedAvailableBeans = null;
    return;
  }
  el.style.color = '#999';
  el.textContent = '⏳ 查询余额...';
  try {
    const res = await fetch(pipelineCfg.url + '/api/billing/balance', {
      headers: { 'Authorization': `Bearer ${pipelineCfg.apiKey}` },
    });
    if (res.status === 401) {
      el.textContent = '⚠️ API密钥无效';
      el.style.color = '#e74c3c';
      cachedAvailableBeans = null;
      return;
    }
    const data = await res.json();
    if (!data.ok) throw new Error(data.error || '查询失败');
    const avail = data.available;
    cachedAvailableBeans = avail;
    // 每条链接悲观冻结 1 + 输入图数(通常~11), 保守按11估"约可采几条"
    const est = avail > 0 ? Math.floor((avail + 10) / 11) : 0;
    if (avail <= 0) {
      el.textContent = `🔴 金豆不足(可用${avail})，请充值`;
      el.style.color = '#e74c3c';
    } else {
      el.textContent = `💰 可用${avail}金豆（约可采${est}条）`;
      el.style.color = avail <= 11 ? '#e67e22' : '#27ae60';
    }
  } catch (e) {
    el.textContent = '余额查询失败';
    el.style.color = '#aaa';
    cachedAvailableBeans = null;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  updateShopStatus();
  refreshBeansStatus();

  // 配置按钮: 展开密钥输入面板
  const cfgBtn = document.getElementById('cfg-btn');
  if (cfgBtn) cfgBtn.addEventListener('click', () => toggleKeyPanel());
  // 取消按钮: 收起
  const cancelBtn = document.getElementById('cfg-cancel-btn');
  if (cancelBtn) cancelBtn.addEventListener('click', () => toggleKeyPanel(false));
  // 保存按钮
  const saveBtn = document.getElementById('shop-save-btn');
  if (saveBtn) saveBtn.addEventListener('click', () => saveShopConfig());
  // 回车也能保存
  const keyInput = document.getElementById('shop-api-key');
  if (keyInput) keyInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') saveShopConfig();
  });
});

// ========== 商品采集 ==========
let collectedData = null;

document.getElementById('collectBtn').addEventListener('click', async () => {
  const resultEl = document.getElementById('result');

  resultEl.innerHTML = '<div style="text-align:center;padding:20px;">⏳ 正在解析页面数据...</div>';

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

    const [res] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: extractFromRawData,
      world: 'MAIN'  // 必须用 MAIN 世界才能访问页面 window.rawData
    });

    collectedData = res.result;

    if (!collectedData || collectedData.error) {
      resultEl.innerHTML = `<div style="color:red;padding:10px;">❌ ${collectedData?.error || '采集失败：无法读取页面数据'}</div>`;
      return;
    }

    const { title, priceRange, categoryTags, salesCount, rating, starText, reviewCount, shop, propMap, goodsProperty, galleryImgs, skuImgs, skuWithLabels, specTree, videos, allImgs } = collectedData;
    const totalCount = allImgs.length;

    // 缩略图行渲染函数
    function renderThumbRow(imgs) {
      if (!imgs.length) return '';
      let row = '<div style="display:flex;flex-wrap:wrap;gap:3px;margin:4px 0;">';
      imgs.forEach(url => {
        row += `<img src="${url}" style="width:48px;height:48px;object-fit:cover;border-radius:3px;border:1px solid #eee;" onerror="this.style.display='none'">`;
      });
      row += '</div>';
      return row;
    }

    let html = `
      <div style="font-size:13px;line-height:1.6;">
        <div style="font-weight:bold;color:#333;margin-bottom:2px;">📦 ${title}</div>
        <div style="color:#fb7701;font-weight:bold;font-size:15px;">💰 ${priceRange}</div>`;

    // 标签
    if (categoryTags.length) {
      html += `<div style="color:#888;font-size:11px;margin-top:2px;">🏷️ ${categoryTags.join(' > ')}</div>`;
    }

    // 销量 + 评分
    html += '<div style="color:#666;font-size:12px;margin-top:4px;">';
    if (salesCount) {
      html += `📊 ${salesCount}`;
    }
    if (rating) {
      html += ` | ⭐ ${rating} <span style="color:#fb7701;">${starText}</span>`;
    }
    if (reviewCount) {
      html += ` | 💬 ${reviewCount} 条评论`;
    }
    html += '</div>';

    // 店铺
    html += `<div style="color:#666;font-size:11px;margin-top:2px;">🏪 ${shop.name}`;
    if (shop.goodsCount) html += ` | 📦 ${shop.goodsCount}`;
    if (shop.followers) html += ` | 👥 ${shop.followers}`;
    html += '</div>';

    // 产品详细信息
    if (goodsProperty.length) {
      html += '<div style="margin-top:6px;padding:6px 8px;background:#f5f5f5;border-radius:6px;font-size:11px;">';
      html += '<div style="font-weight:600;color:#555;margin-bottom:3px;">📋 产品详细信息</div>';
      goodsProperty.forEach(prop => {
        html += `<div style="color:#666;line-height:1.8;"><span style="color:#999;">${prop.propName || prop.key}：</span>${prop.propValue || (prop.values || []).join('、')}</div>`;
      });
      html += '</div>';
    }

    html += `
        <hr style="border:none;border-top:1px solid #ddd;margin:8px 0;">
        <div style="font-size:12px;">🖼️ <b>共 ${totalCount} 张商品图片</b></div>
      </div>`;

    // 主图轮播
    if (galleryImgs.length) {
      html += `<div style="margin-top:8px;"><div style="font-size:12px;font-weight:600;color:#555;">📷 主图轮播 (${galleryImgs.length}张)</div>`;
      html += renderThumbRow(galleryImgs);
      html += '</div>';
    }

    // SKU规格图 - 按规格层级分组展示
    if (specTree && specTree.length) {
      html += '<div style="margin-top:8px;">';
      html += '<div style="font-size:12px;font-weight:600;color:#555;margin-bottom:4px;">🎨 商品规格 (共' + (skuWithLabels ? skuWithLabels.length : 0) + '个SKU，' + specTree.length + '级规格)</div>';

      specTree.forEach((level, levelIdx) => {
        html += '<div style="margin-bottom:6px;padding:4px 6px;background:#fafafa;border-radius:6px;">';
        html += '<div style="font-size:11px;font-weight:600;color:#888;margin-bottom:3px;">' + (levelIdx + 1) + '️⃣ ' + level.specKey + '（' + level.values.length + '种）</div>';
        html += '<div style="display:flex;flex-wrap:wrap;gap:5px;">';
        level.values.forEach(item => {
          html += '<div style="text-align:center;font-size:10px;">';
          html += '<img src="' + item.imgUrl + '" style="width:56px;height:56px;object-fit:cover;border-radius:4px;border:1px solid #eee;display:block;" onerror="this.style.display=\'none\'" title="' + item.specValue + '">';
          html += '<span style="color:#333;display:block;max-width:56px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:1px;line-height:1.2;">' + item.specValue + '</span>';
          html += '</div>';
        });
        html += '</div></div>';
      });

      // 如果SKU有独立价格差异，展示价格
      const hasPrice = skuWithLabels && skuWithLabels.some(s => s.price);
      if (hasPrice) {
        html += '<div style="font-size:10px;color:#999;margin-top:2px;">💡 不同规格价格可能不同，详见导出CSV</div>';
      }

      html += '</div>';
    } else if (skuWithLabels && skuWithLabels.length) {
      // 兼容单级展示
      html += '<div style="margin-top:6px;"><div style="font-size:12px;font-weight:600;color:#555;">🎨 SKU规格图 (' + skuWithLabels.length + '种规格)</div>';
      html += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin:4px 0;">';
      skuWithLabels.forEach(item => {
        html += '<div style="text-align:center;font-size:10px;margin-bottom:4px;">';
        html += '<img src="' + item.url + '" style="width:64px;height:64px;object-fit:cover;border-radius:4px;border:1px solid #ddd;display:block;" onerror="this.style.display=\'none\'" title="' + item.label + '">';
        html += '<span style="color:#333;display:block;max-width:64px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:2px;">' + item.label + '</span>';
        if (item.price) {
          html += '<span style="color:#fb7701;display:block;max-width:64px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:9px;">' + item.price + '</span>';
        }
        html += '</div>';
      });
      html += '</div></div>';
    }

    // 视频
    if (videos.length) {
      html += `<div style="margin-top:6px;"><div style="font-size:12px;font-weight:600;color:#555;">🎬 商品视频 (${videos.length}个)</div>`;
      videos.forEach((v, i) => {
        html += `<div style="font-size:11px;color:#888;margin:2px 0;">视频${i + 1}: <a href="${v.url}" target="_blank" style="color:#007bff;">${v.width}x${v.height} MP4</a></div>`;
      });
      html += '</div>';
    }

    resultEl.innerHTML = html;

    document.getElementById('sendPipelineBtn').style.display = 'block';

  } catch (error) {
    resultEl.innerHTML = `<div style="color:red;padding:10px;">❌ 采集失败：${error.message}</div>`;
    console.error(error);
  }
});

// ========== 发送到管线 ==========
document.getElementById('sendPipelineBtn').addEventListener('click', async () => {
  if (!collectedData) return;

  const resultEl = document.getElementById('result');
  const sendBtn = document.getElementById('sendPipelineBtn');

  sendBtn.disabled = true;
  sendBtn.textContent = '⏳ 发送中...';

  try {
    // 提取纯数字价格
    function cleanPrice(p) {
      if (!p) return '';
      const s = String(p).replace(/[^\d.]/g, '');
      const num = parseFloat(s);
      return isNaN(num) ? '' : String(num);
    }

    const now = new Date();
    const pad2 = n => String(n).padStart(2, '0');
    const localNow = now.getFullYear() + '-' + pad2(now.getMonth()+1) + '-' + pad2(now.getDate()) + ' ' + pad2(now.getHours()) + ':' + pad2(now.getMinutes()) + ':' + pad2(now.getSeconds());

    const shopCfg = await getShopConfig();
    const pipelineCfg = buildPipelineConfig(shopCfg);
    if (!pipelineCfg.apiKey) {
      toggleKeyPanel(true);  // 自动展开密钥面板
      throw new Error('❌ 请先填写 API 密钥（从网站「设置」复制后点保存）');
    }
    const rawSummary = collectedData.rawSummary || {};
    const skuList = rawSummary.skuList || [];
    const specLevelNames = (collectedData.specTree || []).map(l => l.specKey);

    // 构建 SKU 数组（每个 SKU 独立一行）
    const skus = skuList.map(sku => {
      const specObj = sku.specObj || {};
      const specKeys = Object.keys(specObj);
      const rawSpecs = sku.rawSpecs || [];

      // SKC属性 JSON
      const skcAttr = JSON.stringify([{
        parentSpecId: 0, parentSpecName: '',
        specId: 0, specName: '',
        previewImgUrls: sku.skcPreviewImg || sku.thumbUrl || '',
        extCode: '', productSkcId: sku.skcId || ''
      }]);

      // SKU属性 JSON
      const skuAttr = JSON.stringify(specKeys.map(k => {
        const rs = rawSpecs.find(s => s.specKey === k);
        return {
          specId: rs ? rs.specValueId : 0,
          parentSpecName: k,
          specName: specObj[k],
          parentSpecId: rs ? rs.specKeyId : 0
        };
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

    // 无 SKU 时的回退
    if (skus.length === 0) {
      skus.push({
        variantName: '默认',
        specName1: '', specValue1: '',
        specName2: '', specValue2: '',
        previewImage: collectedData.galleryImgs[0] || '',
        price: cleanPrice(collectedData.priceRange),
        stock: 0,
        skcProps: '[]', skuProps: '[]',
        spuId: collectedData.goodsId || '',
        skcId: '', skuId: '',
      });
    }

    // 构建完整 payload（发货/包装配置已移到网站「设置」页，导出时读取，不再随采集发送）
    const payload = {
      goodsId: collectedData.goodsId || '',
      // 平台来源：供后台按采集箱分组展示（temu/1688/ozon）
      platform: 'temu',
      categoryId: collectedData.categoryId || '',
      videoUrl: (collectedData.videos || []).map(v => v.url).join('\n'),
      videos: (collectedData.videos || []).map(v => ({
        url: v.url || '',
        poster: v.poster || '',
        width: v.width || 0,
        height: v.height || 0,
      })),
      spec: {
        skuCount: skus.length,
        specLevels: specLevelNames,
        specTree: (collectedData.specTree || []).map(level => ({
          specKey: level.specKey || '',
          values: (level.values || []).map(item => ({
            specValue: item.specValue || '',
            imgUrl: item.imgUrl || '',
          })),
        })),
        productProps: (collectedData.goodsProperty || []).filter(p => !['商品编号', '产地'].includes((p.propName || p.key || '').trim())).map(p => ({
          propName: p.propName || '',
          propValue: p.propValue || '',
        })),
      },
      createdAt: localNow,
      product: {
        title: collectedData.title || '',
        galleryImages: (collectedData.galleryImgs || []).slice(0, 10),
        firstImage: (collectedData.galleryImgs || [])[0] || '',
        productProps: (collectedData.goodsProperty || []).filter(p => !['商品编号', '产地'].includes((p.propName || p.key || '').trim())).map(p => ({
          propName: p.propName || '',
          refPid: p.refPid || '',
          pid: p.pid || '',
          templatePid: p.templatePid || '',
          numberInputValue: p.numberInputValue || '',
          valueUnit: p.valueUnit || '',
          vid: p.vid || '',
          propValue: p.propValue || ''
        })),
      },
      skus: skus,
    };

    const res = await fetch(pipelineCfg.url + '/api/temu/import', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${pipelineCfg.apiKey}`,
      },
      body: JSON.stringify(payload),
    });

    const data = await res.json();

    if (data.ok) {
      // 成功: 顺带更新缓存 + 显示剩余可用(后端已在响应里返回)
      if (typeof data.available === 'number') cachedAvailableBeans = data.available;
      const balTip = (typeof data.available === 'number')
        ? `<br><span style="color:#888;font-size:10px;">💰 剩余可用 ${data.available} 金豆</span>`
        : '';
      resultEl.innerHTML += `
        <div style="margin-top:8px;padding:8px;background:#f0f9ff;border:1px solid #91d5ff;border-radius:6px;font-size:12px;">
          ✅ <b>已发送到管线！</b> #${data.import_id} — ${data.title || ''}
          <br>${data.sku_count} 个SKU，${data.total_images} 张轮播图
          <br><span style="color:#888;font-size:10px;">管线页面已自动接收，可继续采集下一条</span>
          ${balTip}
        </div>`;
      // 余额可能变了, 刷新顶部状态(1次轻量查询)
      refreshBeansStatus();
    } else if (res.status === 402) {
      // 金豆不足: 醒目红色提示, 不显示成"未知错误"
      resultEl.innerHTML += `
        <div style="margin-top:8px;padding:10px;background:#fff0f0;border:2px solid #e74c3c;border-radius:6px;font-size:12px;">
          🔴 <b>金豆不足，无法发送</b>
          <br>${data.error || '余额不足'}
          <br><span style="color:#e74c3c;">请充值后继续，已采集的会自动续跑</span>
        </div>`;
      // 余额变了(或首次查), 刷新状态
      refreshBeansStatus();
    } else {
      throw new Error(data.error || '未知错误');
    }
  } catch (e) {
    resultEl.innerHTML += `
      <div style="margin-top:8px;padding:8px;background:#fff2f0;border:1px solid #ffccc7;border-radius:6px;font-size:12px;">
        ❌ <b>发送失败:</b> ${e.message}
        <br><span style="color:#999;">请确保管线服务已启动且 API 密钥正确</span>
      </div>`;
    console.error('发送管线失败:', e);
  }

  sendBtn.disabled = false;
  sendBtn.textContent = '🚀 发送到管线';
});
