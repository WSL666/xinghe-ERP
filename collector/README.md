# 采集插件（Chrome 扩展）

Temu 商品采集插件，Manifest V3。采集页面商品 → 发送到后端流水线生成图片。

## 目录结构

```
collector/temu-collector/
  manifest.json      扩展清单
  popup.html         弹窗 UI
  popup.js           采集 + 发送逻辑
  xlsx.full.min.js   导出 Excel 用的库
```

## 下载机制（重要：改了代码无需手动打包）

网页端 `GET /api/temu/plugin/download` 下载的 zip，由后端 `_ensure_plugin_zip()`（在 `backend/platforms/temu/router.py`）自动打包：

- **进程内存缓存** + **源码修改时间检测**
- 第一次下载：打包成 `collector/temu-collector.zip` 并缓存
- 后续下载：直接发缓存的 zip 文件（瞬间完成）
- **改了插件源码**（popup.js/html 等）：下次下载自动检测到 mtime 更新，重新打包
- 重启服务：缓存丢失，首次下载重新打包

**结论**：改完插件代码，用户下次点下载就是最新版，**不用手动打包、不用重启服务**。

## 金豆计费联动（预扣 hold + 结算 settle）

插件与后端的金豆扣费机制联动，详见 `backend/billing/README.md`。关键点：

- **开弹窗时查 1 次余额**（`GET /api/billing/balance`，用 Bearer API Key）
  - 显示「💰 可用 X 金豆（约可采 N 条）」，不足红字「🔴 金豆不足，请充值」
  - 不是每条采集都查，只开弹窗 / 保存配置 / 采集成功后查
- **采集发送**（`POST /api/temu/import`）
  - 后端入队前预扣 11 金豆（悲观上限：视觉 1 + 输入图数 ×1）
  - 成功响应顺带返回 `available`（插件显示剩余，零额外请求）
  - 余额不足返回 402 → 插件红字醒目提示「金豆不足，已采集的会自动续跑」
- **欠费不爆队列**：余额不足的链接标 `insufficient`，不进 Redis 队列；充值后自动重新预扣入队

## 金豆计费常量（后端 `backend/billing/store.py`）

| 常量 | 值 | 说明 |
|------|-----|------|
| `BEANS_FLOOR` | -10 | 可用余额下限（允许欠到此） |
| `HOLD_VISION` | 1 | 视觉解析冻结额度 |
| `HOLD_PER_IMAGE` | 1 | 每张输入图冻结额度 |

每条链接悲观预扣 `1 + 输入图数`（通常 11），100 金豆约可排 10 条。

## 调试

```bash
# 看插件下载是否打包了最新代码
ls -la collector/temu-collector.zip
# 改了 popup.js 后，下次下载会自动重新打包(mtime 更新)

# 看后端服务日志
tail -f /var/log/product-pipeline.log
```
