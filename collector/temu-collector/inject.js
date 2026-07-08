// inject.js — 运行在页面 MAIN world, 能访问 window.rawData
// 被 content.js 用 chrome.scripting.executeScript 注入
// 采集后通过 window.postMessage 把结果传回 content.js (ISOLATED world)

(function () {
  // 内联 extractFromRawData 的核心逻辑(不能 import, MAIN world 无模块)
  try {
    if (!window.rawData) {
      window.postMessage({ source: 'tk-collector', ok: false, error: '未找到商品数据，请确保在 Temu 商品详情页使用' }, '*');
      return;
    }

    // ========== 以下逻辑与 extract.js / popup.js 完全一致 ==========
    const raw = window.rawData;
    const store = raw.store;
    if (!store) {
      window.postMessage({ source: 'tk-collector', ok: false, error: '页面数据结构异常，请刷新后重试' }, '*');
      return;
    }

    const goods = store.goods || {};
    const title = goods.goodsName || store.title || '未获取到标题';
    const goodsId = goods.goodsId || store.goodsId || '';
    const minPrice = goods.minOnSalePriceStr || '';
    const maxPrice = goods.minToMaxPriceStr || '';
    const priceRange = maxPrice || minPrice || '未获取到价格';

    let categoryTags = [];
    const breadcrumbLinks = document.querySelectorAll('[data-testid="breadcrumb"] a, nav[aria-label="Breadcrumb"] a, .breadcrumb a, [class*="breadCrumb"] a');
    breadcrumbLinks.forEach(a => {
      const text = (a.textContent || '').trim();
      if (text && text !== '首页' && text !== 'Home') categoryTags.push(text);
    });
    if (!categoryTags.length) {
      const metaKeywords = document.querySelector('meta[name="keywords"]');
      if (metaKeywords) {
        categoryTags = (metaKeywords.getAttribute('content') || '').split(',').map(x => x.trim()).filter(Boolean);
      }
    }

    const goodsSoldTip = goods.goodsSoldTip || goods.sideSalesTip || '';
    const salesCount = goodsSoldTip.replace(/，by\s*$/i, '').trim();

    const mallData = store.mall?.mallData || {};
    const shop = {
      name: mallData.mallName || goods.saleInfo?.mallName || '未知店铺',
      goodsCount: (mallData.goodsNumUnit || []).join(' ') || '',
      followers: (mallData.followerNumUnit || []).join(' ') || ''
    };

    const rating = mallData.mallStarStr || '';
    const ratingNum = parseFloat(rating) || 0;
    const reviewCount = mallData.reviewNumStr || '';
    const starCount = Math.round(ratingNum);
    const starText = '★'.repeat(starCount) + '☆'.repeat(5 - starCount);

    const rawProps = goods.goodsProperty || goods.props || goods.properties || goods.attrList || [];
    const propMap = {};
    const goodsPropertyFull = rawProps.map(p => {
      if (p.key && p.values) {
        const v = Array.isArray(p.values) ? p.values.join('、') : p.values;
        propMap[p.key] = v;
        return { propName: p.key, refPid: p.refPid || '', pid: p.pid || '', templatePid: p.templatePid || '', numberInputValue: p.numberInputValue || '', valueUnit: p.valueUnit || '', vid: p.vid || '', propValue: v };
      }
      if (p.propName) {
        propMap[p.propName] = p.propValue || '';
        return { propName: p.propName, refPid: p.refPid || '', pid: p.pid || '', templatePid: p.templatePid || '', numberInputValue: p.numberInputValue || '', valueUnit: p.valueUnit || '', vid: p.vid || '', propValue: p.propValue || '' };
      }
      return p;
    });

    const allImgSet = new Set();
    const galleryImgList = [];
    const skuImgList = [];
    const videoList = [];

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
      if (normalized.includes('.gif') || normalized.includes('supplier-public-tag') || normalized.includes('algo_framework')) return;
      if (normalized.includes('upload_aimg')) {
        const allowedAimgPaths = ['/goods_details/', '/commodity/', '/pho/', '/temu/', '/product/'];
        if (!allowedAimgPaths.some(p => normalized.includes(p))) return;
      }
      if (!allImgSet.has(normalized)) {
        allImgSet.add(normalized);
        if (category === 'gallery') galleryImgList.push(normalized);
        else if (category === 'sku') skuImgList.push(normalized);
      }
    }

    function addVideo(videoObj) {
      if (!videoObj || !videoObj.videoUrl) return;
      const url = normalizeUrl(videoObj.videoUrl);
      if (!url) return;
      if (!videoList.find(v => v.url === url)) {
        videoList.push({
          url,
          poster: normalizeUrl(videoObj.url || ''),
          width: videoObj.width || 0,
          height: videoObj.height || 0
        });
      }
    }

    // ===== 以下与 extract.js 完全一致 =====

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
      if (goods.video.url) addImg(goods.video.url, 'gallery');
    }

    // 4.4 SKU 列表 (store.sku)
    const skus = store.sku || [];
    if (!Array.isArray(skus)) skus = [];
    skus.forEach(sku => {
      if (sku.thumbUrl) addImg(sku.thumbUrl, 'sku');
      if (sku.specShowImageUrl) addImg(sku.specShowImageUrl, 'sku');
    });

    // 规格树
    const specLevelKeySet = new Set();
    const specLevelKeys = [];
    const specLevelValueMap = {};
    const specLevelValueImgSet = {};
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

    const skuLabelSet = new Set();
    const skuWithLabels = [];
    skus.forEach(sku => {
      const specs = sku.specs || [];
      const label = specs.map(sp => sp.specValue).join(' / ') || '';
      const price = sku.normalPriceStr || sku.displayPriceStr || sku.priceStr || '';
      const imgUrl = normalizeUrl(sku.specShowImageUrl || sku.thumbUrl);
      specs.forEach(sp => {
        const key = sp.specKey;
        const val = sp.specValue;
        if (key && val && specLevelValueImgSet[key] && imgUrl && !specLevelValueImgSet[key].has(val)) {
          specLevelValueImgSet[key].add(val);
          specLevelValueMap[key][val] = imgUrl;
        }
      });
      if (imgUrl && !skuLabelSet.has(imgUrl + '|' + label)) {
        skuLabelSet.add(imgUrl + '|' + label);
        const specObj = {};
        specs.forEach(sp => { specObj[sp.specKey] = sp.specValue; });
        skuWithLabels.push({
          url: imgUrl,
          label: label,
          specs: specObj,
          specList: specs.map(sp => ({ key: sp.specKey, value: sp.specValue })),
          skuId: sku.skuId || '',
          price: price
        });
      }
    });

    const specTree = specLevelKeys.map(specKey => {
      const valueImgMap = specLevelValueMap[specKey] || {};
      const valueCount = {};
      skus.forEach(sku => {
        (sku.specs || []).forEach(sp => { if (sp.specKey === specKey) valueCount[sp.specValue] = (valueCount[sp.specValue] || 0) + 1; });
      });
      return { specKey, values: Object.keys(valueImgMap).map(sv => ({ specValue: sv, imgUrl: valueImgMap[sv] || '', skuCount: valueCount[sv] || 1 })) };
    });

    if (goods.imageUrl) addImg(goods.imageUrl);
    const pageUrl = window.location ? window.location.href : '';
    const categoryId = goods.catId || goods.categoryId || store.categoryId || '';
    const origin = (mallData.mallRegionStr || mallData.regionStr || '');
    const allImgs = [...galleryImgList, ...skuImgList];

    const collectedData = {
      goodsId, title, priceRange, categoryTags, salesCount,
      rating, starText, reviewCount, shop, propMap,
      goodsProperty: goodsPropertyFull,
      galleryImgs: galleryImgList,
      skuImgs: skuImgList,
      skuWithLabels, specTree, videos: videoList,
      allImgs, totalCount: allImgs.length,
      pageUrl, categoryId, origin,
      rawSummary: {
        skuCount: skus.length,
        specLevels: specLevelKeys,
        skuList: skus.map(s => ({
          skuId: s.skuId,
          skcId: s.skcId || s.productSkcId || '',
          spuId: goodsId,
          thumbUrl: normalizeUrl(s.thumbUrl || ''),
          specShowImageUrl: normalizeUrl(s.specShowImageUrl || ''),
          price: s.normalPriceStr || s.displayPriceStr || s.priceStr || '',
          specs: (s.specs || []).map(sp => sp.specValue).join(' / '),
          specObj: Object.fromEntries((s.specs || []).map(sp => [sp.specKey, sp.specValue])),
          rawSpecs: (s.specs || []).map(sp => ({ specKey: sp.specKey, specValue: sp.specValue, specKeyId: sp.specKeyId || 0, specValueId: sp.specValueId || 0 })),
          stock: s.stockQuantity || 0,
          skcPreviewImg: normalizeUrl(s.skcPreviewImgUrl || s.thumbUrl || '')
        }))
      }
    };

    window.postMessage({ source: 'tk-collector', ok: true, data: collectedData }, '*');
  } catch (e) {
    window.postMessage({ source: 'tk-collector', ok: false, error: '采集异常: ' + e.message }, '*');
  }
})();
