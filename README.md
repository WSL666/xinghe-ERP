# Product Pipeline Digital App

基于 FastAPI 的电商商品（Temu）图片生成流水线后端，从原始商品数据一路跑：标题翻译 → 视觉解析 → 图片生成 → 导出 Excel，并把旧图/新图/视频上传到阿里云 OSS。

## 技术栈

- **Web**：FastAPI + Uvicorn（ASGI），Session cookie 鉴权 + Bearer API Key（插件用）
- **语言**：Python 3.13（conda 环境 `wsl-test`）
- **存储**：PostgreSQL 16 + pgvector（业务数据/导入记录）、Redis 7（流水线任务队列 + 并发计数）
- **对象存储**：阿里云 OSS（旧图、生成图、视频）
- **AI**：DeepSeek（标题翻译）、自建兼容 OpenAI 的 Chat/Vision 端点（视觉解析）、VibeLearning（图生图）
- **反代/证书**：Caddy（自动 ACME，DNS-01 via alidns）
- **前端**：纯静态 HTML/CSS/JS，由 FastAPI 的 `StaticFiles` 直接托管
- **进程管理**：systemd（`product-pipeline.service` web + `product-pipeline-worker.service` worker，均 `Restart=always`）
- **采集端**：Chrome 扩展（`collector/temu-collector/`，Manifest V3，v2.0）

## 运行拓扑（生产实际架构）

```
浏览器/插件 ──HTTPS:8443──▶ Caddy ──HTTP──▶ Uvicorn web(127.0.0.1:6688) ──▶ PostgreSQL(:5433)
                                                   │
                                                   └──写入──▶ Redis 队列(仅 Unix socket) ◀──BRPOP──┐
                                                                                                    │
                                     worker.py 独立进程(N 个线程) ─────────────────────────────────┘
```

- **域名**：`https://wangshilin888.com:8443`（Caddy 反代到 `127.0.0.1:6688`）
- **应用监听**：`127.0.0.1:6688`，HTTP（TLS 由 Caddy 终止，**uvicorn 不要加 `--ssl-*`**）
- **数据库**：PostgreSQL `127.0.0.1:5433`（容器内 5432）
- **Redis：只走 Unix socket，不监听任何 TCP 端口**（详见下方「Redis Unix socket 设计」）
- **进程拆分（web 与 worker 分离）**：
  - **web 进程**：`product-pipeline.service`（uvicorn 单进程，只接 HTTP / 入队，`--workers` 不需要再设）
  - **worker 进程**：`product-pipeline-worker.service`（`python worker.py`，只消费 Redis 队列跑流水线）
  - 两者通过 Redis 队列解耦：web 重启不杀在跑的任务；想加并发只调 worker，不动 web；未来 worker 可单独搬到别的机器
- **当前生产配置**：`PIPELINE_CONCURRENCY=32`（全局 32 个 worker 线程）+ `PIPELINE_MAX_PER_USER=1`（每人同时只跑 1 条），**同时支持 32 人各跑 1 条**，第 33 人自动排队
- **并发模型**：
  - worker 进程内启动 `PIPELINE_CONCURRENCY` 个线程同时 `BRPOP` 同一个 Redis 队列
  - 每用户最多 `PIPELINE_MAX_PER_USER` 个并发任务（同一用户超额任务回队尾等待）
  - 空闲线程几乎不占内存（阻塞在 BRPOP 等活），可放心开多
  - 并发计数存 Redis（原子 INCR/DECR + TTL），多进程共享、崩溃不泄漏
  - 兼容旧用法：web 设 `PIPELINE_EMBED_WORKERS=1` 时仍在 web 内起 worker（仅测试用，生产不开）

### 并发调参（PIPELINE_CONCURRENCY / PIPELINE_MAX_PER_USER）

两个环境变量控制并发，**改完要重启 worker 才生效**：

| 变量 | 作用 | 在哪设 | 生产值 |
|------|------|--------|--------|
| `PIPELINE_CONCURRENCY` | worker 进程内线程数（全局并发上限 = 同时能跑几条） | worker service 的 `Environment=` 行 | **32** |
| `PIPELINE_MAX_PER_USER` | 单用户最多同时跑几个任务（防霸占） | worker service 的 `Environment=` 行 或 `backend/.env` | **1** |

```bash
# 例: 想支持 32 人每人 1 条(当前生产配置)
# 编辑 /etc/systemd/system/product-pipeline-worker.service
#   Environment="PIPELINE_CONCURRENCY=32"
#   Environment="PIPELINE_MAX_PER_USER=1"
systemctl daemon-reload
systemctl restart product-pipeline-worker.service
tail -3 /var/log/product-pipeline-worker.log   # 确认打印的并发数已变
```

**容量推算**（4C/7G 无 swap，可用约 3.3G，每任务峰值 ~30-50MB）：

| 配置 | 同时支持人数 | 内存占用 | 说明 |
|------|------------|----------|------|
| `CONCURRENCY=16, MAX_PER_USER=1` | 15 人 | ~480MB | 试水，很安全 |
| `CONCURRENCY=32, MAX_PER_USER=1` | **32 人** | **~960MB** | **当前生产，推荐甜点位** |
| `CONCURRENCY=48, MAX_PER_USER=1` | 45 人 | ~1.4G | 偏紧，要盯内存，API 限流风险 |

> 内存不是唯一瓶颈：并发越高，同时调视觉/图片生成 API 的请求越多，API 供应商限流和 key 池容量才是真正的天花板。建议先跑 32，观察 key 池命中率和排队情况再决定是否上调。


> 端口：生产对外是 **8443（HTTPS，Caddy）**，应用本体是 **6688（HTTP，内网）**，PostgreSQL 绑 `127.0.0.1:5433`，Redis 无 TCP 端口。

## Redis Unix socket 设计（关键）

Redis **不监听 TCP 端口**（`--port 0`），只通过 Unix socket 通信：`/var/run/product-pipeline/redis.sock`。后端连接串是 `unix:///var/run/product-pipeline/redis.sock?db=0`。

**为什么这样设计**：历史上 Redis 监听 `127.0.0.1:6380`，被 VS Code Remote 的自动端口转发盯上——它对监听端口建大量 TCP 连接不释放，撑爆 Redis 连接数，导致 worker `brpop` 失败、任务永远卡在 `queued`。改用 Unix socket 后，VS Code 只能转发 TCP 端口、转发不了 socket 文件，**该问题从物理上消除**。

**三层保障**：
1. **根治**：Redis 关闭 TCP（`--port 0`），只开 socket —— VSCode 无端口可转发。
2. **兜底**：Redis `--maxclients 256` + `--timeout 120` —— 即使有异常连接，空闲 2 分钟回收、总数不超 256。
3. **socket 目录自建**：`/var/run` 是 tmpfs，重启清空。systemd service 的 `ExecStartPre` 会在每次启动前自动 `install -d -m 0777 /var/run/product-pipeline`，无需手动维护。

**验证**：
```bash
# TCP 端口应连不上(预期: Connection refused = 正确)
redis-cli -p 6380 ping
# socket 应通(预期: PONG)
redis-cli -s /var/run/product-pipeline/redis.sock ping
# 连接数应稳定在个位数到几十(正常 ~5)
redis-cli -s /var/run/product-pipeline/redis.sock info clients | grep connected_clients
```

## 快速开始

### 一键启动（生产）

```bash
# 在仓库根目录 /root/workspace/wsl-workplace
docker compose up -d && sleep 5
systemctl start product-pipeline.service          # web(HTTP)
systemctl start product-pipeline-worker.service   # worker(消费队列跑流水线)

# 验证
curl http://127.0.0.1:6688/api/health             # 应返回 {"ok":true,"status":"healthy"}
systemctl status product-pipeline-worker.service  # worker 应 active(running)
```

两个 systemd service（模板在 `deploy/`，需 `cp` 到 `/etc/systemd/system/` 后 `systemctl daemon-reload`）：
- `product-pipeline.service` → 托管 uvicorn web，`Restart=always`，`ExecStartPre` 自动创建 Redis socket 目录，日志追加到 `/var/log/product-pipeline.log`
- `product-pipeline-worker.service` → 托管 `python worker.py`，`Restart=always`，`TimeoutStopSec=300`（给任务 5 分钟优雅收尾），日志追加到 `/var/log/product-pipeline-worker.log`

### 一键停止

```bash
systemctl stop product-pipeline-worker.service                   # 先停 worker(优雅收尾任务)
systemctl stop product-pipeline.service                          # 再停 web
docker stop product-pipeline-redis product-pipeline-postgres     # 停 Redis + Postgres
```

### 重启后端（改了代码或 .env 后）

> **web 和 worker 是两个独立进程**。改了代码或 `.env` 后，**两个都要重启**，
> 否则没重启的那个还会跑旧代码（常见坑：改了 pipeline 逻辑只重启 web，worker 仍跑老代码）。

```bash
systemctl restart product-pipeline.service          # web(改 API/路由时重启)
systemctl restart product-pipeline-worker.service   # worker(改 pipeline/队列逻辑时必须重启)

# 查看重启是否成功
systemctl status product-pipeline.service
systemctl status product-pipeline-worker.service
tail -5 /var/log/product-pipeline-worker.log        # 看到 "worker started" = 正常
```

- 重启 worker 会触发崩溃恢复：把 DB 里 `queued`/`generating` 的任务自动重新入队
- Redis socket 目录由 web service 的 `ExecStartPre` 自动维护，无需手动创建

### 首次部署（一次性）

```bash
# 1. 起基础设施
docker compose up -d          # PostgreSQL(:5433) + Redis(Unix socket)

# 2. 准备后端
cd backend
cp .env.example .env          # 编辑,填入 AI/OSS/数据库密钥
pip install -r requirements.txt

# 3. 安装 systemd service(两个:web + worker)
cp /root/workspace/wsl-workplace/deploy/product-pipeline.service /etc/systemd/system/
cp /root/workspace/wsl-workplace/deploy/product-pipeline-worker.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable product-pipeline.service product-pipeline-worker.service

# 4. 启动(两个都要)
systemctl start product-pipeline.service          # web
systemctl start product-pipeline-worker.service   # worker

# 5. 验证两个进程都在跑
systemctl status product-pipeline.service
systemctl status product-pipeline-worker.service
curl http://127.0.0.1:6688/api/health             # {"ok":true,"status":"healthy"}
```

### 手动启动（开发/调试，不走 systemd）

```bash
conda activate wsl-test
cd backend

# 方式A: 两个进程都手动开(开两个终端)
uvicorn main:app --host 127.0.0.1 --port 6688     # 终端1: web
PIPELINE_CONCURRENCY=4 python worker.py           # 终端2: worker

# 方式B: web 内嵌 worker(旧模式,简单测试用)
PIPELINE_EMBED_WORKERS=1 uvicorn main:app --host 127.0.0.1 --port 6688
# 此时只起一个进程,web 里同时跑 worker 线程(改代码无需单独重启 worker)
```

> 镜像源：`docker-compose.yml` 的镜像名带 `m.daocloud.io` 加速前缀（`pgvector/pgvector:pg16`、`redis:7.4-alpine`）。

### 访问地址

| 地址 | 说明 |
|---|---|
| `https://wangshilin888.com:8443/` | 落地页 |
| `https://wangshilin888.com:8443/dashboard` | 工作台 |
| `https://wangshilin888.com:8443/docs` | API 文档 |
| `https://wangshilin888.com:8443/api/health` | 健康检查 |

> Caddy 需运行：`systemctl start caddy`（默认开机自启）。

### 本地开发（无域名/无 Caddy）

```bash
cd backend
python -m uvicorn main:app --reload --host 127.0.0.1 --port 6688
```

`.env` 里把 `APP_ENV=development`，此时自动创建开发账号（`admin / 123456`），cookie 不强制 HTTPS。本地若用 Docker 起的 Redis，连接串指向对应 socket 或 TCP。

## Chrome 采集插件

`collector/temu-collector/`（Manifest V3，v2.0）。

| 文件 | 说明 |
|---|---|
| `manifest.json` | 扩展配置，`host_permissions` 为 `*://*.temu.com/*` + `https://wangshilin888.com:8443/*` |
| `popup.html` | 弹窗界面：采集按钮、导出、店铺配置面板 |
| `popup.js` | 核心逻辑：页面解析、属性数据库匹配、发送管线、导出 XLSX |
| `attr_db.json` | 属性数据库（propName→pid/templatePid，pid\|propValue→vid） |
| `xlsx.full.min.js` | SheetJS，导出 xlsx 用 |

**关键行为：**
- **后端地址写死**：`popup.js` 里 `DEFAULT_PIPELINE_URL = 'https://wangshilin888.com:8443'`。
- **API 密钥**：插件「店铺配置」面板填入（从网站「设置 → 插件 API 密钥」复制）。
- **采集流程**：Temu 商品详情页点「采集」→ 解析 `window.rawData` → 「发送到管线」→ `POST /api/temu/import`（`Authorization: Bearer <key>`）。

## 配置（`backend/.env`）

关键项（完整字段见 `backend/config.py` 的 `Settings`，模板见 `backend/.env.example`）：

| 变量 | 说明 | 默认 |
|---|---|---|
| `APP_ENV` | `production` 时 cookie 强制 `secure` | `development` |
| `DATABASE_URL` | Postgres DSN | `...@127.0.0.1:5433/product_pipeline` |
| `REDIS_URL` | Redis 连接（**Unix socket**） | `unix:///var/run/product-pipeline/redis.sock?db=0` |
| `APP_SECRET_KEY` | Session token 签名密钥 | - |
| `CORS_ORIGINS` | 生产前端白名单（逗号分隔） | fallback 到 `localhost:8443` |
| `AUTO_VERIFY_USERS` | 注册是否免验证 | `true` |
| `PIPELINE_CONCURRENCY` | worker 线程数（全局并发） | `32`（生产） |
| `PIPELINE_MAX_PER_USER` | 每用户最大并发任务数 | `1`（生产） |
| `step2_*` | DeepSeek 标题翻译配置（base_url/api_key/model） | - |
| `CHAT_*` / `OPENAI_CHAT_BASE_URL` | 视觉解析模型配置 | - |
| `VIBE_*` / `IMAGE_MODEL` / `IMAGE_SIZE` | 图生图模型配置 | - |
| `OSS_*` | 阿里云 OSS（key/endpoint/bucket/folder/cdn） | - |
| `SMS_*` | 短信验证码（`console` 打印 / `aliyun` 阿里云） | `console` |

> 改了 `.env` 必须 `systemctl restart product-pipeline.service` 才生效。

### 超时与防僵尸（`backend/core/base.py`）

| 常量 | 值 | 说明 |
|---|---|---|
| `IMAGE_ATTEMPT_TIMEOUT` | `300.0` | 单次图生图请求超时 5 分钟 |
| `MAX_IMAGE_ATTEMPTS` | `2` | 图生图最多重试 2 次 |
| `VISION_TIMEOUT` | `300.0` | 视觉解析单次请求超时 5 分钟 |
| `VISION_MAX_ATTEMPTS` | `3` | 视觉解析最多重试 3 次 |
| `PIPELINE_TOTAL_TIMEOUT` | `900.0` | 单条流水线总兜底 15 分钟，超时强制判 `error` |

> 兜底：`platforms/temu/pipeline.py:execute` 入口设 deadline，每步前后检查；翻译/视觉线程用 `join(timeout=)` 包裹；任何步骤卡死最多 15 分钟判 `error` 并释放 worker，不产生僵尸任务。

## 代码结构

```
backend/
├── main.py              # FastAPI 入口:组装 app、CORS/Session 中间件、lifespan 启停
├── config.py            # 读取 .env → Settings dataclass
├── store.py             # PostgreSQL 数据访问层(连接池 + 全部表操作 + init_db)
├── orchestrator.py      # 流水线编排:run_auto_pipeline 入队 + worker_handler 分发
├── worker.py            # 独立 worker 进程入口(只消费 Redis 队列跑流水线,不跑 HTTP)
├── pipeline_queue.py    # Redis 队列 + worker 线程池(BRPOP、Redis 原子并发计数、崩溃恢复)
├── core/                # 平台无关的核心工具
│   ├── base.py          #   常量(超时/并发)、env 加载、LLM 调用、日志
│   ├── app.py           #   公共路由(auth/enterprise/页面/health)
│   ├── vision.py        #   视觉模型调用与重试
│   ├── image_gen.py     #   VibeLearning 图生图调用(带超时/重试)
│   ├── images.py        #   图片下载/编码/并行抓取
│   └── oss.py           #   OSS 上传(图/视频)
├── platforms/           # 各平台特化逻辑
│   ├── dispatch.py      #   按 platform 字段分发到对应 pipeline
│   └── temu/            #   Temu 平台
│       ├── router.py    #     /api/temu/* 路由(import/list/export/generate)
│       ├── pipeline.py  #     四步流水线(上传‖翻译‖视觉→生图→收尾,带 deadline)
│       ├── adapter.py   #     raw_json → Product 适配
│       ├── export.py    #     导出 xlsx
│       └── prompts/     #     translate / vision prompt 模板
├── models/              # 数据模型(Product、to_pipeline_input)
├── security.py          # 密码哈希、session token、API Key
├── sms.py               # 短信验证码(console / 阿里云)
├── oss_client.py        # OSS 客户端封装
├── api_key_pool/        # API Key 池(Redis 三态轮换 + 内网管理面板),见 api_key_pool/README.md
│   ├── pool.py         #   key 池核心(可用/冷却/失效, LRU 轮换, Redis 共享)
│   ├── admin.py        #   内网管理面板(左右布局 + 双表格, /admin/keys)
│   └── run.py          #   面板独立测试启动器(端口 7799, 内存隔离)
└── requirements.txt
frontend/                # 静态前端(dashboard 工作台),由 FastAPI StaticFiles 托管
collector/temu-collector/ # Chrome 采集插件(v2.0)
docker/                  # docker-compose 用到的 Postgres 初始化脚本
deploy/                  # 部署文件(systemd service 模板)
docker-compose.yml       # PostgreSQL + Redis 基础设施
```

## 业务流水线（核心逻辑）

一次 `POST /api/temu/import` 的处理路径：

1. **入队**（`orchestrator.py:run_auto_pipeline`）：状态置 `queued`，任务 `LPUSH` 进 Redis `pipeline:queue`
2. **Worker 消费**（`pipeline_queue.py:_worker_loop`）：
   - `BRPOP` 阻塞取任务（15s 超时轮询）
   - Redis 原子「检查并发上限 + 占座」（Lua 脚本 `INCR` + `EXPIRE`），超 `PIPELINE_MAX_PER_USER` 则回队尾等待
   - 进程启动时 recovery（`list_resumable_imports`）把 DB 里 `queued`/`generating` 的任务重新入队
3. **执行四步**（`platforms/temu/pipeline.py:execute`）：
   - **step1**：源图/视频上传 OSS（视频失败不阻断）
   - **step2**：DeepSeek 翻译标题（中/英）—— 与 step3 并行
   - **step3**：视觉模型解析轮播图，选参考图 + 生成 prompt —— 与 step2 并行
   - **step4**：VibeLearning 按提示词并行生成新图（`MAX_PARALLEL=10`），上传 OSS
   - 全程受 `PIPELINE_TOTAL_TIMEOUT`（15 分钟）deadline 保护
4. **计数释放**：`finally` 里 Redis 原子 `DECR`（Lua），任何路径都保证释放，崩溃由 TTL 兜底
5. 每步 `update_status` 写 DB，前端轮询查进度；终态 `done`/`error`

## 鉴权

- **Session cookie**：登录后写 `ppe_session`（`secure` 由 `APP_ENV` 控制）
- **Bearer API Key**：插件走 `Authorization: Bearer <key>`（`_plugin_user` 校验）
- **API Key 管理**：网站「设置 → 插件连接」查看/重置，`POST /api/auth/api-key/reset`
- **开发账号**：`APP_ENV != production` 时自动创建 `admin / 123456`
- SMS 验证：`SMS_PROVIDER=console` 打印，或 `aliyun` 走阿里云短信

## 常用运维检查

```bash
# 基础设施状态
docker compose ps

# 应用是否在监听
ss -tlnp | grep 6688

# web service 状态 / 日志
systemctl status product-pipeline.service
tail -f /var/log/product-pipeline.log

# worker service 状态 / 日志(任务在跑/排队看这里)
systemctl status product-pipeline-worker.service
tail -f /var/log/product-pipeline-worker.log

# Caddy 状态 / 日志
systemctl status caddy
journalctl -u caddy -f

# 健康检查
curl -k https://localhost:8443/api/health    # 经 Caddy
curl http://127.0.0.1:6688/api/health        # 直连应用

# 队列堆积情况(0 = 空闲)
redis-cli -s /var/run/product-pipeline/redis.sock llen pipeline:queue

# 某用户当前并发计数(应为 nil 或小整数;任务完成后归零)
redis-cli -s /var/run/product-pipeline/redis.sock get pipeline:active:1

# Redis 连接数(正常 ~5;若飙升 = 有异常)
redis-cli -s /var/run/product-pipeline/redis.sock info clients | grep connected_clients

# 卡住的任务(看 status/updated_at,长时间不变可能是僵尸)
docker exec product-pipeline-postgres psql -U product_pipeline_user -d product_pipeline \
  -c "select id,user_id,status,status_msg,updated_at from imports where status not in ('done','error') order by id desc;"
```

## 运维经验

### Redis 连接被撑爆导致任务卡死（已根治）

历史根因：Redis 监听 TCP 6380 时，VS Code Remote 自动转发该端口，建大量连接不释放，撑爆连接数，worker `brpop` 失败，任务卡 `queued`。

**已根治**：Redis 改为只走 Unix socket（无 TCP 端口），VS Code 物理上无法转发，问题消除。仍保留 `--maxclients 256` + `--timeout 120` 作为兜底。判定方法：`connected_clients` 正常 ~5，若飙升即排查。

### 每用户并发计数泄漏（已根治）

历史缺陷：并发计数曾存进程内存 dict，进程崩溃时计数不归零，导致该用户任务被误判"已在运行"而反复回队、永不执行。

**已根治**：计数改为 Redis 原子操作（Lua `INCR`/`DECR`）+ TTL（1 小时）。多进程共享、进程崩溃由 TTL 自动清零，不再泄漏。判定：任务完成后 `pipeline:active:{user_id}` 应为 `(nil)`。

### Redis 重启丢队列

Redis 队列是内存态，重启后清空，但 DB 里状态仍是 `queued`。**恢复**：重启 web 和 worker，两者启动时都会自动把 `queued`/`generating` 的任务重新入队：

```bash
systemctl restart product-pipeline.service
systemctl restart product-pipeline-worker.service
```

### AI API 卡死导致僵尸任务（已有兜底）

外部 AI API 可能 hang 住。**兜底**：单次图生图超时 5 分钟（最多重试 2 次）；整条流水线兜底 15 分钟（`PIPELINE_TOTAL_TIMEOUT`）；翻译/视觉线程 `join(timeout=)` 到点判超时。

### 服务器重启后 Redis socket 目录丢失

`/var/run` 是 tmpfs，重启清空。systemd service 的 `ExecStartPre=/usr/bin/install -d -m 0777 /var/run/product-pipeline` 在每次启动前自动重建目录，无需手动操作。若未用 systemd 启动，需手动 `mkdir -p /var/run/product-pipeline && chmod 777 /var/run/product-pipeline`。
