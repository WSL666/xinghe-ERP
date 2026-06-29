# API Key 池模块

为视觉解析（chat）和图片生成（vibe）两个 AI 接口提供 **Redis 三状态 Key 池轮换** + **内网管理面板**，解决高并发下单 Key 被限流/失效的问题。

## 文件结构

```
api_key_pool/
├── __init__.py   包入口，对外暴露公共 API
├── pool.py       Redis 三状态 key 池核心（可用/冷却/失效，LRU 轮换）
├── admin.py      内网管理面板（HTML 页面 + CRUD JSON API）
└── README.md     本文件
```

## 管理面板布局（左右结构）

```
┌─────────────┬──────────────────────────────────────┐
│  模型列表    │  视觉解析模型            可用3 冷却1 失效2 │
│  (竖排滚动)  │  ┌────────────────────────────────┐  │
│             │  │ + 添加 Key: [_____________] [添加]│  │
│ ▸ 视觉解析  │  └────────────────────────────────┘  │
│   图片生成  │  🟢 正常 Key（可用+冷却）  共 4 个     │
│             │  ┌────┬────┬────┬────┬────┬────┐    │
│   (新模型   │  │Key │状态│添加│失败│失败│操作│    │
│   在此加)   │  │脱敏│    │时间│次数│原因│    │    │
│             │  └────┴────┴────┴────┴────┴────┘    │
│             │  🔴 失效 Key          [批量删除全部] │
│             │  ┌────┬────┬────┬────┬────┬────┐    │
│             │  │Key │状态│失效│失败│失败│操作│    │
│             │  │脱敏│    │时间│次数│原因│    │    │
│             │  └────┴────┴────┴────┴────┴────┘    │
└─────────────┴──────────────────────────────────────┘
```

- **左侧**：模型列表（竖排，可上下滚动）。点击切换右侧显示的模型。
  新增模型只需在 `pool.py` 的 `PROVIDERS` 加一行，左侧自动出现。
- **右侧**：当前模型的两张表（纵向叠放）。
  - 上表：**正常 Key**（可用 + 冷却）
  - 下表：**失效 Key**（401/403 自动掉入，可一键批量删）
- **表格字段**：Key（脱敏）、状态、添加时间、失败次数、失败原因（含 403/503 等码）。
- **增删改查**：
  - 增：底部输入框粘贴 key → 添加到可用池
  - 删：每行「删除」+ 失效表「批量删除全部」
  - 改：每行「标记失效」/「恢复」（改状态）
  - 查：列表 + 顶部统计（可用/冷却/失效数量）

## 访问安全

面板 `/admin/keys` 双层保护：
1. **仅本机访问**：`request.client.host` 必须 `127.0.0.1`。生产 uvicorn 绑 `127.0.0.1`，
   外部经 Caddy 到不了此路由。
2. **本地 token**：`.env` 的 `ADMIN_TOKEN`，请求需带 `?token=` 或 `Authorization: Bearer`。

本地电脑访问需 SSH 端口转发：
```bash
ssh -L 6688:127.0.0.1:6688 root@服务器IP
# 浏览器: http://127.0.0.1:6688/admin/keys?token=你的TOKEN
```

## 三状态模型

每个接口（provider）在 Redis 里有三个集合：

| 状态 | Redis Key | 含义 | 自动流转 |
|---|---|---|---|
| **可用** available | `apikeys:{provider}:available` | 正常取用 | 失败后视情况移出 |
| **冷却** cooling | `apikeys:{provider}:cooling` | 临时问题（429/超时） | **5 分钟后自动回到可用** |
| **失效** failed | `apikeys:{provider}:failed` | 401/403 认证失败/封禁 | 不自动恢复，等人工批量删 |

另外 `apikeys:{provider}:failcount` (HASH) 记录每个 key 的连续失败次数。

## 轮换策略

- **失败才轮换**：成功继续用同一个 key（保持连接复用），不无谓切换。
- **LRU 取用**：`acquire()` 取「最久未使用」的可用 key，均匀分摊负载。
- **401/403** → 直接进**失效板块**，不再浪费重试。
- **429/超时/5xx** → 失败计数 +1，连续 3 次进**冷却 5 分钟**，到期自动恢复试用。
- **池空** → `acquire()` 返回 `None`，调用方回退 `.env` 的单 key 兜底（不卡死）。
- **全失效** → 返回 None 报错降级，而非默默卡死。

## 两个池（provider）

| provider | 用途 | 调用点 |
|---|---|---|
| `chat` | 视觉解析 | `core/vision.py` 的 `analyze_product_with_retry` |
| `vibe` | 图片生成 | `core/image_gen.py` 的 `generate_one_image` |

两池在同一个 Redis 里用不同前缀区分，数据完全隔离。

## 接入点（业务代码）

本模块自包含，但接入逻辑写在现有业务文件里：

- `core/vision.py`：每次重试从 `get_pool("chat").acquire()` 取 key，401/403 进失效。
- `core/image_gen.py`：每次重试从 `get_pool("vibe").acquire()` 取 key，401/403 进失效。
- `main.py`：`from api_key_pool import router` 挂载 `/admin/keys`。

## 管理面板

路由前缀 `/admin/keys`，**双层安全**：

1. **仅本机访问**：校验 `request.client.host` 必须是 `127.0.0.1`/`::1`。生产 uvicorn 绑 `127.0.0.1`，外部经 Caddy 到不了此路由。
2. **本地 token**：`.env` 的 `ADMIN_TOKEN`，请求需带 `?token=` 或 `Authorization: Bearer`。为空时仅靠本机访问控制。

### 访问方式

面板只允许本机。本地电脑访问需 SSH 端口转发：
```bash
ssh -L 6688:127.0.0.1:6688 root@服务器IP
# 本地浏览器打开（token 从 .env 读）:
# http://127.0.0.1:6688/admin/keys?token=你的ADMIN_TOKEN
```

### 面板功能

- **查看**：两个池各显示 可用(绿)/冷却(橙)/失效(红) 三栏，每 5 秒自动刷新。
- **新增**：选 provider + 粘贴 key → 加入可用池。
- **删除**：单个删 / 批量删失效板块。
- **恢复**：把失效/冷却的 key 手动拉回可用。
- **清空**：清空某池所有状态（慎用）。

### API 端点

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/admin/keys` | HTML 面板 |
| GET | `/admin/keys/api/state` | 两池状态 JSON |
| POST | `/admin/keys/api/add` | 新增 key `{provider, key}` |
| POST | `/admin/keys/api/remove` | 删除 key `{provider, key}` |
| POST | `/admin/keys/api/bulk-remove` | 批量删 `{provider, keys:[]}` 或 `{provider, state:"failed"}` |
| POST | `/admin/keys/api/revive` | 失效拉回可用 `{provider, key}` |
| POST | `/admin/keys/api/clear` | 清空某池 `{provider}` |

## 公共 API

```python
from api_key_pool import get_pool, PROVIDERS, all_snapshots, router

pool = get_pool("vibe")          # 取某池
pool.add("sk-xxx")               # 新增 key
key = pool.acquire()             # 取可用 key（LRU），无则 None
pool.mark_success(key)           # 成功反馈（清失败计数）
pool.mark_failed(key, 401, "")   # 失败反馈，返回 "failed"/"cooling"
pool.remove(key)                 # 删除
pool.revive(key)                 # 失效拉回可用
pool.snapshot()                  # 状态快照（脱敏）
```

## 配置

`.env`：
```
ADMIN_TOKEN=随机长串    # 面板访问令牌
REDIS_URL=unix:///var/run/product-pipeline/redis.sock?db=0   # 复用现有 Redis
```

## 设计决策

- **为什么用 Redis 而不是新建数据库**：key 池是高频读写的运行态（取 key、标记、恢复），需要跨 worker 共享 + 原子操作。Redis 正是为此设计，复用现有 Redis 零额外运维。
- **为什么是 WATCH/MULTI 而非 Lua**：取 key 用乐观锁（WATCH + MULTI）保证「回收+取用+更新时间戳」原子，真实 Redis 和测试环境都兼容。
- **为什么失败才轮换**：成功继续用保持连接复用、减少开销；每次都换反而浪费且无意义。
