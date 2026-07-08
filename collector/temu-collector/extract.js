// ========== 共享采集逻辑: 从 window.rawData 提取商品数据 ==========
// popup.js 和 inject.js(MAIN world) 都引用此文件
// 注意: 此函数运行在页面上下文, 可访问 window.rawData 和 document

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

