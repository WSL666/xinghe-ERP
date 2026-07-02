# API Key 池模块

为视觉解析（chat）和图片生成（vibe）两个 AI 接口提供 **Redis 三状态 Key 池轮换**，
解决高并发下单 Key 被限流 / 失效的问题。

> 本模块只保留**引擎**（`pool.py`）。
> Key 的增删改查管理界面已迁移到 **超级管理员系统**（`admin-platform`）的
> **AI 资源** 模块，旧的 `/admin/keys` HTML 面板（`admin.py` / `run.py`）已删除。

## 文件结构

```
api_key_pool/
├── __init__.py   包入口，对外暴露公共 API
├── pool.py       Redis 三状态 key 池核心（可用 / 冷却 / 失效，LRU 轮换）
└── README.md     本文件
```

## 三状态模型

每个接口（provider）在 Redis 里有三个集合：

| 状态 | Redis Key | 含义 | 自动流转 |
|---|---|---|---|
| **可用** available | `apikeys:{provider}:available` | 正常取用 | 失败后视情况移出 |
| **冷却** cooling | `apikeys:{provider}:cooling` | 临时问题（429/超时） | 5 分钟后自动回到可用 |
| **失效** failed | `apikeys:{provider}:failed` | 401/403 认证失败/封禁 | 不自动恢复，由超管手动处理 |

另外 `apikeys:{provider}:failcount` (HASH) 记录每个 key 的连续失败次数。

## 轮换策略

- **失败才轮换**：成功继续用同一个 key（保持连接复用），不无谓切换。
- **LRU 取用**：`acquire()` 取「最久未使用」的可用 key，均匀分摊负载。
- **401/403** → 直接进 **失效板块**，不再浪费重试。
- **429/超时/5xx** → 失败计数 +1，连续 3 次进 **冷却 5 分钟**，到期自动恢复试用。
- **池空** → `acquire()` 返回 `None`，调用方回退 `.env` 的单 key 兜底（不卡死）。
- **全失效** → 返回 None 报错降级，而非默默卡死。

## 两个池（provider）

| provider | 用途 | 调用点 |
|---|---|---|
| `chat` | 视觉解析 | `core/vision.py` 的 `analyze_product_with_retry` |
| `vibe` | 图片生成 | `core/image_gen.py` 的 `generate_one_image` |

两池在同一个 Redis 里用不同前缀区分，数据完全隔离。

## 接入点（业务代码）

- `core/vision.py`：每次重试从 `get_pool("chat").acquire()` 取 key，401/403 进失效。
- `core/image_gen.py`：每次重试从 `get_pool("vibe").acquire()` 取 key，401/403 进失效。
- `main.py`：`pool.py` 引擎仍由流水线使用，仅挂载了公共 / 平台 / 充值路由。

## 启动兜底

`bootstrap_from_env()`：启动时若某池为空，自动从 `.env` 的
`CHAT_API_KEY` / `VIBE_API_KEY` 注入兜底 key，避免 Redis 清空后无 key 可用。

## 公共 API

```python
from api_key_pool import get_pool, PROVIDERS, all_snapshots

pool = get_pool("vibe")          # 取某池
pool.add("sk-xxx")               # 新增 key
key = pool.acquire()             # 取可用 key（LRU），无则 None
pool.mark_success(key)           # 成功反馈（清失败计数）
pool.mark_failed(key, 401, "")   # 失败反馈，返回 "failed"/"cooling"
pool.remove(key)                 # 删除
pool.revive(key)                 # 失效拉回可用
pool.snapshot()                  # 状态快照（脱敏）
```

> Key 的 **增删改查管理界面** 在超级管理员系统的 AI 资源模块，
> 登录 `admin-platform` 即可操作，无需再访问任何本机面板。

## 配置

`.env`：
```
REDIS_URL=unix:///var/run/product-pipeline/redis.sock?db=0   # 复用现有 Redis
CHAT_API_KEY=sk-xxx   # 视觉解析兜底 key（空池启动时用）
VIBE_API_KEY=sk-xxx   # 图片生成兜底 key（空池启动时用）
```

## 设计决策

- **为什么用 Redis 而不是新建数据库**：key 池是高频读写的运行态（取 key、标记、恢复），
  需要跨 worker 共享 + 原子操作。Redis 正是为此设计，复用现有 Redis 零额外运维。
- **为什么是 WATCH/MULTI 而非 Lua**：取 key 用乐观锁（WATCH + MULTI）保证
  「回收+取用+更新时间戳」原子，真实 Redis 和测试环境都兼容。
- **为什么失败才轮换**：成功继续用保持连接复用、减少开销；每次都换反而浪费且无意义。
