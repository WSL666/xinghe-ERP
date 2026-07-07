# 金豆计费模块（预扣 + 结算）

企业级预授权模型：和酒店预授权、云厂商按量计费同一思路。
金豆是**后付费**（跑完才知道扣几颗：视觉可能失败、图可能只成功一半），
但"可用余额"必须**即时**反映未结算任务的占用，否则并发跑时多个任务
互不知道彼此消耗，必然超扣。

## 数据模型（users 表）

| 字段 | 含义 |
|------|------|
| `beans` | 真实余额（已结算，前端展示的金豆数） |
| `frozen_beans` | 冻结中（预扣占住，未真扣） |
| — | **可用余额 = `beans` - `frozen_beans`** |

> `frozen_beans` 由 `init_billing_tables()` 幂等添加（`ALTER ... ADD COLUMN IF NOT EXISTS`），
> 服务重启自动迁移，默认 0，无需手动建列。

## 计费规则

| 项 | 金豆 | 说明 |
|----|------|------|
| 视觉解析成功 | 1 | 失败不扣 |
| 每张成功图 | 各 1 | 失败的图不扣 |
| **悲观预扣上限** | `1 + 输入图数` | 例：10 张图 → 冻结 11 金豆 |
| 欠费下限 | -10 | 可用余额 > -10 才能预扣 |

100 金豆约可排 **10 条**链接（每条冻结 11）。

## 完整流程

```
采集入队            worker 执行            任务收尾
   │                   │                     │
   ▼                   │                     │
 hold(上限)            │                     │
 frozen += 上限  ──────▶ 直接跑(已预扣) ──────▶ settle / release
 可用余额即时降低       │                     │
   │                   │              frozen -= 上限(解冻)
   │ 可用 ≤ -10 ──▶ 拒绝              beans  -= 实际成本(真扣)
   │ 标 insufficient  │              多冻的自动回到可用
   │ 不入队            │              全失败 → release(只解冻不扣)
   ▼                   ▼                     ▼
```

### 1. 入队预扣 `hold_beans(uid, 上限, import_id)`
- 原子冻结：`frozen_beans += 上限`，条件 `beans - frozen - 上限 >= -10`
- 可用余额不足 → 标 `insufficient`，**不入 Redis 队列**（不会爆队列）
- 幂等：同 `import_id` 只 hold 一次

### 2. worker 执行
- 已预扣过，直接跑，不查余额

### 3. 收尾结算 `settle_beans(uid, import_id, hold_amount, vision_ok, 成功图数)`
- 实际成本 = `视觉成功1 + 成功图数`
- `frozen -= hold_amount`（解冻全部预扣）
- `beans -= 实际成本`（真扣）
- 多冻的（`hold_amount - 实际`）随解冻自动回到可用余额
- 全失败 → `release_beans`：只解冻不扣
- 幂等：同 `import_id` 只结算一次（防队列重复/重跑）

### 4. 充值恢复 `restore_insufficient(uid)`
- 充值后扫该用户所有 `insufficient` 任务，重新预扣入队
- 仍不够的继续留 `insufficient`，等下次充值

## 为什么天然支持 N 并发（关键）

预扣是**即时、原子**的：每条链接入队就立即降低可用余额。
10 个任务并发预扣时，第 N 个看到的可用余额已经减去了前 N-1 个的冻结——
**数学上不可能超扣**，无论并发几条。这正是它比"事后扣费"优雅的根本原因：
事后扣费时并发任务互不知道彼此消耗，必然撞车；预扣让每次占用立即反映到余额上。

## API

| 接口 | 方法 | 鉴权 | 说明 |
|------|------|------|------|
| `/api/billing/balance` | GET | session **或** Bearer API Key | 返回 `beans` + `available`（插件开弹窗查 1 次） |
| `/api/billing/transactions` | GET | session | 消费/充值记录 |
| `/api/billing/recharge` | POST | X-Admin-Token | 管理员充值，**充值后自动恢复 insufficient 任务** |

> `/api/temu/import` 成功响应也顺带返回 `available`，插件据此显示剩余，零额外请求。

## 欠费提示（B + C 方案）

- **B（列表）**：`insufficient` 单独成箱，`GET /api/temu/imports?insufficient=1` 拉取，
  采集箱排除 insufficient（不污染）。前端标注「余额不足待充值，充值后自动续跑」。
- **C（插件）**：开弹窗查 1 次余额（不是每条采集都查）→
  - 显示「💰 可用 X 金豆（约可采 N 条）」
  - 不足红字「🔴 金豆不足，请充值」
  - 采集返回 402 醒目提示，不显示成"未知错误"

## 关键常量（`billing/store.py`）

```python
BEANS_FLOOR    = -10   # 可用余额下限（允许欠到此）
HOLD_VISION    = 1     # 视觉解析冻结额度
HOLD_PER_IMAGE = 1     # 每张输入图冻结额度
```

## 调用方

| 触发点 | 位置 | 调用 |
|--------|------|------|
| 采集入队 | `platforms/temu/router.py` | `hold_beans`（不够 → insufficient） |
| 流水线收尾 | `platforms/temu/pipeline.py` | `settle_beans` / `release_beans` |
| worker 取出 | `orchestrator.py` | 跳过 insufficient（防御性） |
| 充值 | `billing/router.py` | `restore_insufficient` |

## 常见问题

**Q：hold 流水的 `amount` 为什么是 0？**
A：hold 不进账面（真实余额 `beans` 没变，只 `frozen_beans` 变了）。若存非 0，
`release` 的 `amount <> 0` 防重检查会被 hold 记录误命中，导致全失败时无法释放冻结。
冻结额度由 `frozen_beans` 字段跟踪，`settle` 时由调用方用 `hold_amount_for()` 重新算传入。

**Q：用户欠费后一直采集会不会爆队列？**
A：不会。欠费的链接标 `insufficient`，**不进 Redis 队列**，只在 DB 留一条记录。

**Q：一条链接会扣几次？**
A：一次。`hold → settle` 一条链接一条消费流水，幂等防重复。
