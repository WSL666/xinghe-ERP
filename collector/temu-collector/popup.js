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

// ========== 核心：从 window.rawData 提取商品数据 ==========
function extractFromRawData() {
  try {
    // 1. 读取 rawData
    if (!window.rawData) {
      return { error: '未找到商品数据 (rawData)，请确保在Temu商品详情页使用' };
    }

    const raw = window.rawData;
    const store = raw.store;
    if (!store) {
      return { error: '页面数据结构异常，请刷新后重试' };
    }
    // 2. 提取商品基本信息
    const goods = store.goods || {};
    const title = goods.goodsName || store.title || '未获取到标题';
    const goodsId = goods.goodsId || store.goodsId || '';

    // 价格
    const minPrice = goods.minOnSalePriceStr || '';
    const maxPrice = goods.minToMaxPriceStr || '';
    const priceRange = maxPrice || minPrice || '未获取到价格';

    // 3. 标签/分类（从DOM面包屑提取）
    let categoryTags = [];
    // Temu面包屑通常是 <a> 链接在导航区域
    const breadcrumbLinks = document.querySelectorAll('[data-testid="breadcrumb"] a, nav[aria-label="Breadcrumb"] a, .breadcrumb a, [class*="breadCrumb"] a');
    breadcrumbLinks.forEach(a => {
      const text = (a.textContent || '').trim();
      if (text && text !== '首页' && text !== 'Home') {
        categoryTags.push(text);
      }
    });
    // 如果没有面包屑DOM，用meta keywords兜底
    if (!categoryTags.length) {
      const metaKeywords = document.querySelector('meta[name="keywords"]');
      if (metaKeywords) {
        const content = metaKeywords.getAttribute('content') || '';
        categoryTags = content.split(',').map(s => s.trim()).filter(Boolean);
      }
    }

    // 4. 销量
    const goodsSoldTip = goods.goodsSoldTip || goods.sideSalesTip || '';
    // 提取纯中文销量（如 "已售 95万件"）
    const salesCount = goodsSoldTip.replace(/，by\s*$/i, '').trim();

    // 5. 店铺信息
    const mallData = store.mall?.mallData || {};
    const shop = {
      name: mallData.mallName || goods.saleInfo?.mallName || '未知店铺',
      goodsCount: (mallData.goodsNumUnit || []).join(' ') || '',
      followers: (mallData.followerNumUnit || []).join(' ') || ''
    };

    // 6. 评分
    const rating = mallData.mallStarStr || '';
    const ratingNum = parseFloat(rating) || 0;
    const reviewCount = mallData.reviewNumStr || '';
    // 生成星级文字（四舍五入）
    const starCount = Math.round(ratingNum);
    const starText = '★'.repeat(starCount) + '☆'.repeat(5 - starCount);

    // 7. 产品详细信息（产地、材质、风格等）
    // 支持两种格式：key/values（简化）和 propName/propValue/refPid/vid/pid/templatePid/valueUnit（完整）
    const rawProps = goods.goodsProperty || goods.props || goods.properties || goods.attrList || [];
    const propMap = {};
    const goodsPropertyFull = rawProps.map(p => {
      if (p.key && p.values) {
        // 页面格式 → 转换为店小秘后台格式
        const v = Array.isArray(p.values) ? p.values.join('、') : p.values;
        propMap[p.key] = v;
        return {
          propName: p.key,
          refPid: p.refPid || '',
          pid: p.pid || '',
          templatePid: p.templatePid || '',
          numberInputValue: p.numberInputValue || '',
          valueUnit: p.valueUnit || '',
          vid: p.vid || '',
          propValue: v
        };
      }
      if (p.propName) {
        // 后端API格式：保证字段齐全
        propMap[p.propName] = p.propValue || '';
        return {
          propName: p.propName,
          refPid: p.refPid || '',
          pid: p.pid || '',
          templatePid: p.templatePid || '',
          numberInputValue: p.numberInputValue || '',
          valueUnit: p.valueUnit || '',
          vid: p.vid || '',
          propValue: p.propValue || ''
        };
      }
      return p;
    });

    // 4. ========== 核心：收集所有商品图片和视频 ==========
    const allImgSet = new Set();      // 所有图片（去重）
    const galleryImgList = [];        // 主图轮播图
    const skuImgList = [];            // SKU变体图
    const videoList = [];             // 视频列表

    // 辅助函数：标准化图片URL
    function normalizeUrl(url) {
      if (!url || typeof url !== 'string') return '';
      let clean = url.split('?')[0];
      if (clean.startsWith('//')) clean = 'https:' + clean;
      if (clean.startsWith('http://')) clean = clean.replace('http://', 'https://');
      return clean;
    }

    function addImg(url, category) {
      const normalized = normalizeUrl(url);
      if (!normalized) return;

      // 过滤非商品图片
      if (
        normalized.includes('.gif') ||
        normalized.includes('supplier-public-tag') ||
        normalized.includes('algo_framework')
      ) return;

      // upload_aimg 路径白名单：只放行商品相关图片，拦截UI图标/装饰元素
      if (normalized.includes('upload_aimg')) {
        const allowedAimgPaths = [
          '/goods_details/',
          '/commodity/',
          '/pho/',
          '/temu/',
          '/product/'
        ];
        const isProductImg = allowedAimgPaths.some(p => normalized.includes(p));
        if (!isProductImg) return;
      }

      if (!allImgSet.has(normalized)) {
        allImgSet.add(normalized);
        if (category === 'gallery') galleryImgList.push(normalized);
        else if (category === 'sku') skuImgList.push(normalized);
      }
    }

    // 辅助函数：收集视频
    function addVideo(videoObj) {
      if (!videoObj || !videoObj.videoUrl) return;
      const url = normalizeUrl(videoObj.videoUrl);
      if (!url) return;
      // 去重
      if (!videoList.find(v => v.url === url)) {
        videoList.push({
          url,
          poster: normalizeUrl(videoObj.url || ''),
          width: videoObj.width || 0,
          height: videoObj.height || 0
        });
      }
    }

    // 4.1 主图轮播 (gallery)
    const gallery = goods.gallery || [];
    gallery.forEach(item => {
      if (item.url) addImg(item.url, 'gallery');
      if (item.video) addVideo(item.video);
    });

    // 4.2 高清主图
    if (goods.hdThumbUrl) {
      addImg(goods.hdThumbUrl, 'gallery');
    }

    // 4.3 商品主视频
    if (goods.video) {
      addVideo(goods.video);
      // 视频封面图也加入图集
      if (goods.video.url) addImg(goods.video.url, 'gallery');
    }

    // 4.4 ===== 通用SKU多级规格提取 =====
    const skus = store.sku || [];
    const skuWithLabels = [];       // {url, label, skuId, price, specs[]} 完整SKU配对
    const skuLabelSet = new Set();

    // 4.4.1 自动检测所有规格层级（specKey），保持页面顺序
    const specLevelKeys = [];       // 有序的规格键名列表，如 ['颜色', '尺码']
    const specLevelKeySet = new Set();
    const specLevelValueMap = {};   // { specKey: { specValue: imgUrl } }  每个规格值对应的展示图
    const specLevelValueImgSet = {};// { specKey: Set } 去重用

    // 先遍历一遍SKU，收集所有规格键名（保持首次出现顺序）
    skus.forEach(sku => {
      (sku.specs || []).forEach(sp => {
        if (sp.specKey && !specLevelKeySet.has(sp.specKey)) {
          specLevelKeySet.add(sp.specKey);
          specLevelKeys.push(sp.specKey);
          specLevelValueMap[sp.specKey] = {};
          specLevelValueImgSet[sp.specKey] = new Set();
        }
      });
    });

    // 4.4.2 遍历SKU，构建配对数据 + 每级规格值→图片映射
    skus.forEach(sku => {
      const specs = sku.specs || [];
      // 多级标签：各specValue用 / 连接
      const label = specs.map(sp => sp.specValue).join(' / ') || '';
      const price = sku.normalPriceStr || sku.displayPriceStr || sku.priceStr || '';
      const imgUrl = normalizeUrl(sku.specShowImageUrl || sku.thumbUrl);

      // 记录：每个规格值对应的展示图（取首次出现的）
      specs.forEach(sp => {
        const key = sp.specKey;
        const val = sp.specValue;
        if (key && val && specLevelValueImgSet[key] && imgUrl && !specLevelValueImgSet[key].has(val)) {
          specLevelValueImgSet[key].add(val);
          specLevelValueMap[key][val] = imgUrl;
        }
      });

      // 构建SKU配对
      if (imgUrl && !skuLabelSet.has(imgUrl + '|' + label)) {
        skuLabelSet.add(imgUrl + '|' + label);
        const specObj = {};
        specs.forEach(sp => { specObj[sp.specKey] = sp.specValue; });
        skuWithLabels.push({
          url: imgUrl,
          label: label,
          specs: specObj,           // { '颜色': '黑色', '尺码': '17ProMax=17pro(9颗)' }
          specList: specs.map(sp => ({ key: sp.specKey, value: sp.specValue })),
          skuId: sku.skuId || '',
          price: price
        });
      }

      // 同时保持原有逻辑
      if (sku.thumbUrl) addImg(sku.thumbUrl, 'sku');
      if (sku.specShowImageUrl) addImg(sku.specShowImageUrl, 'sku');
    });

    // 4.4.3 构建结构化规格树（供popup分级展示）
    // specTree: [{ specKey, values: [{ specValue, imgUrl, skuCount }] }]
    const specTree = specLevelKeys.map(specKey => {
      const valueImgMap = specLevelValueMap[specKey] || {};
      // 统计每个specValue出现的SKU数
      const valueCount = {};
      skus.forEach(sku => {
        (sku.specs || []).forEach(sp => {
          if (sp.specKey === specKey) {
            valueCount[sp.specValue] = (valueCount[sp.specValue] || 0) + 1;
          }
        });
      });
      const values = Object.keys(valueImgMap).map(specValue => ({
        specValue,
        imgUrl: valueImgMap[specValue] || '',
        skuCount: valueCount[specValue] || 1
      }));
      return { specKey, values };
    });

    // 4.5 兼容旧数据路径
    if (goods.imageUrl) addImg(goods.imageUrl, 'gallery');

    // 4.6 提取页面URL和额外商品信息
    const pageUrl = (typeof window !== 'undefined' && window.location) ? window.location.href : '';
    const categoryId = goods.catId || goods.categoryId || store.categoryId || '';
    const origin = (mallData.mallRegionStr || mallData.regionStr || '');

    // 5. 合并所有图片（保持类别顺序：主图 → SKU图）
    const allImgs = [...galleryImgList, ...skuImgList];

    return {
      goodsId,
      title,
      priceRange,
      categoryTags,
      salesCount,
      rating,
      starText,
      reviewCount,
      shop,
      propMap,
      goodsProperty: goodsPropertyFull,
      galleryImgs: galleryImgList,
      skuImgs: skuImgList,
      skuWithLabels,            // 完整SKU：{url, label, specs, price, skuId}
      specTree,                 // 规格树：[{specKey, values: [{specValue, imgUrl}]}]
      videos: videoList,
      allImgs,
      totalCount: allImgs.length,
      pageUrl,                  // 页面URL
      categoryId,               // 分类ID
      origin,                   // 产地
      rawSummary: {
        skuCount: skus.length,
        specLevels: specLevelKeys,   // 规格层级名列表
        skuList: skus.map(s => ({
          skuId: s.skuId,
          skcId: s.skcId || s.productSkcId || '',
          spuId: goodsId,
          thumbUrl: normalizeUrl(s.thumbUrl || ''),
          specShowImageUrl: normalizeUrl(s.specShowImageUrl || ''),
          price: s.normalPriceStr || s.displayPriceStr || s.priceStr || '',
          specs: (s.specs || []).map(sp => sp.specValue).join(' / '),
          specObj: Object.fromEntries((s.specs || []).map(sp => [sp.specKey, sp.specValue])),
          // 保留原始specs数据（含specKeyId/specValueId），供导出SKU属性用
          rawSpecs: (s.specs || []).map(sp => ({
            specKey: sp.specKey,
            specValue: sp.specValue,
            specKeyId: sp.specKeyId || 0,
            specValueId: sp.specValueId || 0
          })),
          stock: s.stockQuantity || 0,
          skcPreviewImg: normalizeUrl(s.skcPreviewImgUrl || s.thumbUrl || '')
        }))
      }
    };

  } catch (e) {
    return { error: '解析异常: ' + e.message };
  }
}

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
