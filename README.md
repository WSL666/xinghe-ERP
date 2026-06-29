# Product Pipeline Digital App

基于 FastAPI 的电商商品（Temu）图片生成流水线后端，从原始商品数据一路跑：标题翻译 → 视觉解析 → 图片生成 → 导出 Excel，并把旧图/新图/视频上传到阿里云 OSS。

## 技术栈

- **Web**：FastAPI + Uvicorn（ASGI），Session cookie 鉴权 + Bearer API Key（插件用）
- **语言**：Python 3.13（conda 环境 `wsl-test`）
- **存储**：PostgreSQL 16 + pgvector（业务数据/导入记录）、Redis 7（流水线任务队列）
- **对象存储**：阿里云 OSS（旧图、生成图、视频）
- **AI**：DeepSeek（标题翻译）、自建兼容 OpenAI 的 Chat/Vision 端点（视觉解析）、VibeLearning（图生图）
- **反代/证书**：Caddy（自动 ACME，DNS-01 via alidns）
- **前端**：纯静态 HTML/CSS/JS，由 FastAPI 的 `StaticFiles` 直接托管
- **进程管理**：systemd（`product-pipeline.service`，`Restart=always`）
- **采集端**：Chrome 扩展（`collector/temu-collector/`，Manifest V3，v2.0）

## 运行拓扑（生产实际架构）

```
浏览器/插件 ──HTTPS:8443──▶ Caddy ──HTTP──▶ Uvicorn(127.0.0.1:6688) ──▶ PostgreSQL(:5433) / Redis(:6380)
                                                │
                                                └─▶ pipeline worker 线程（从 Redis 队列取任务）
```

- **域名**：`https://wangshilin888.com:8443`（Caddy 配置见 `/etc/caddy/Caddyfile`，反向代理到 `127.0.0.1:6688`）
- **应用监听**：`127.0.0.1:6688`，HTTP（TLS 由 Caddy 终止，**uvicorn 不要加 `--ssl-*`**）
- **数据库**：PostgreSQL `127.0.0.1:5433`（容器内 5432），Redis `127.0.0.1:6380`（容器内 6379）
- **并发模型**：
  - uvicorn `--workers 2` → 2 个进程，每个进程启动 `PIPELINE_CONCURRENCY=2` 个 worker 线程，共 4 个 worker 线程同时 `BRPOP` 同一个 Redis 队列
  - 每用户最多 `PIPELINE_MAX_PER_USER=2` 个并发任务（同一用户超额的任务会被 `RPUSH` 回队尾等待）

> 注意端口：生产对外是 **8443（HTTPS，Caddy）**，应用本体是 **6688（HTTP，内网）**，数据库/Redis 仅绑定 `127.0.0.1`。

## 快速开始

### 一键启动（生产）

```bash
# 在仓库根目录 /root/workspace/wsl-workplace
docker compose up -d && sleep 5 && systemctl start product-pipeline.service

# 验证
curl http://127.0.0.1:6688/api/health      # 应返回 {"ok":true,"status":"healthy"}
```

systemd service（`/etc/systemd/system/product-pipeline.service`）托管 uvicorn，`Restart=always` 崩溃 5 秒后自动重启，日志追加到 `/var/log/product-pipeline.log`。

### 一键停止

```bash
systemctl stop product-pipeline.service                                  # 停后端
docker stop product-pipeline-redis product-pipeline-postgres             # 停 Redis + Postgres
```

### 重启后端（改了代码或 .env 后）

```bash
systemctl restart product-pipeline.service
# 注意：重启会触发崩溃恢复，把 DB 里 queued/generating 的任务重新入队
```

### 首次部署（一次性）

```bash
# 1. 起基础设施
docker compose up -d          # PostgreSQL(:5433) + Redis(:6380)

# 2. 准备后端
cd backend
cp .env.example .env          # 编辑，填入 AI/OSS/数据库密钥
pip install -r requirements.txt

# 3. 安装 systemd service（可选，也可手动 uvicorn 启动）
cp /path/to/product-pipeline.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable product-pipeline.service

# 4. 启动
systemctl start product-pipeline.service
```

> 镜像源：`docker-compose.yml` 里的镜像名带 `m.daocloud.io` 加速前缀（`pgvector/pgvector:pg16`、`redis:7.4-alpine`），优先用本地缓存，避免从 Docker Hub 拉取超时。

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

`.env` 里把 `APP_ENV=development`，此时会自动创建开发账号（`admin / 123456`），cookie 不强制 HTTPS。

### Windows 上一键脚本

仓库根目录 `start_fastapi.bat`，内含两种方案：
- **方案 A**：uvicorn 直接终结 TLS（需 `SSL_CERT`/`SSL_KEY` 证书路径），监听 8443
- **方案 B**：反代模式，监听 8443 但不带 `--ssl-*`

> Linux 生产走的是 Caddy 反代 + systemd，端口为 6688（`start_fastapi.bat` 是 Windows 落地用，别照搬到 Linux）。

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
- **后端地址写死**：`popup.js` 里 `DEFAULT_PIPELINE_URL = 'https://wangshilin888.com:8443'`，所有人通过此域名连接，不再在 UI 里填 Pipeline URL。
- **API 密钥**：在插件「店铺配置」面板里填入（从网站「设置 → 插件 API 密钥」复制），随店铺配置一起存 `chrome.storage.local`。
- **采集流程**：在 Temu 商品详情页点「采集商品信息」→ 解析页面 `window.rawData` → 点「发送到管线」→ `POST /api/temu/import`（`Authorization: Bearer <key>`）。
- **属性数据库**：导出 XLSX 时查 `attr_db.json` 补全 pid/vid/templatePid，详见 `collector/temu-collector/属性数据库维护说明.md`。

## 配置（`backend/.env`）

关键项（完整字段见 `backend/config.py` 的 `Settings`，模板见 `backend/.env.example`）：

| 变量 | 说明 | 默认 |
|---|---|---|
| `APP_ENV` | `production` 时 cookie 强制 `secure` | `development` |
| `DATABASE_URL` | Postgres DSN | `...@127.0.0.1:5433/product_pipeline` |
| `REDIS_URL` | Redis 连接 | `redis://127.0.0.1:6380/0` |
| `APP_SECRET_KEY` | Session token 签名密钥 | - |
| `CORS_ORIGINS` | 生产前端白名单（逗号分隔） | fallback 到 `localhost:8443` |
| `AUTO_VERIFY_USERS` | 注册是否免验证 | `true` |
| `PIPELINE_CONCURRENCY` | 每进程 worker 线程数 | `2` |
| `PIPELINE_MAX_PER_USER` | 每用户最大并发任务数 | `1`（生产 `.env` 已设为 `2`） |
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
| `MAX_IMAGE_ATTEMPTS` | `2` | 图生图最多重试 2 次，失败即报错 |
| `VISION_TIMEOUT` | `300.0` | 视觉解析单次请求超时 5 分钟 |
| `VISION_MAX_ATTEMPTS` | `3` | 视觉解析最多重试 3 次 |
| `PIPELINE_TOTAL_TIMEOUT` | `900.0` | 单条流水线总兜底 15 分钟，超时强制判 `error` |

> 兜底机制：`platforms/temu/pipeline.py:execute` 在入口设 deadline，每步前后检查；翻译/视觉线程用 `join(timeout=剩余时间)` 包裹；任何步骤卡死最多 15 分钟就判 `error` 并释放 worker，不产生僵尸任务。

## 代码结构

```
backend/
├── main.py              # FastAPI 入口：组装 app、CORS/Session 中间件、lifespan 启停
├── config.py            # 读取 .env → Settings dataclass
├── store.py             # PostgreSQL 数据访问层（连接池 + 全部表操作 + init_db）
├── orchestrator.py      # 流水线编排入口：run_auto_pipeline 入队 + worker_handler 分发
├── pipeline_queue.py    # Redis 队列 + worker 线程池（BRPOP 消费、按用户限流、崩溃恢复）
├── core/                # 平台无关的核心工具
│   ├── base.py          #   常量（超时/并发）、env 加载、LLM 调用、日志
│   ├── app.py           #   公共路由（auth/enterprise/页面/health）
│   ├── vision.py        #   视觉模型调用与重试
│   ├── image_gen.py     #   VibeLearning 图生图调用（带超时/重试/httpx.Timeout）
│   ├── images.py        #   图片下载/编码/并行抓取
│   └── oss.py           #   OSS 上传（图/视频）
├── platforms/           # 各平台特化逻辑
│   ├── dispatch.py      #   按 platform 字段分发到对应 pipeline
│   └── temu/            #   Temu 平台
│       ├── router.py    #     /api/temu/* 路由（import/list/export/generate）
│       ├── pipeline.py  #     四步流水线编排（上传‖翻译‖视觉→生图→收尾，带 deadline）
│       ├── adapter.py   #     raw_json → Product 适配
│       ├── export.py    #     导出 xlsx
│       └── prompts/     #     translate / vision prompt 模板
├── models/              # 数据模型（Product、to_pipeline_input）
├── security.py          # 密码哈希、session token、API Key（create/hash/verify）
├── sms.py               # 短信验证码（console / 阿里云 Dysmsapi）
├── oss_client.py        # OSS 客户端封装
└── requirements.txt
frontend/                # 静态前端（dashboard 工作台），由 FastAPI StaticFiles 托管
collector/temu-collector/ # Chrome 采集插件（v2.0）
docker/                  # docker-compose 用到的 Postgres 初始化脚本
docker-compose.yml       # PostgreSQL + Redis 基础设施
start_fastapi.bat        # Windows 启动脚本（方案 A/B）
```

## 业务流水线（核心逻辑）

一次 `POST /api/temu/import` 的处理路径：

1. **入队**（`orchestrator.py:run_auto_pipeline`）
   - 状态置 `queued`，任务 `LPUSH` 进 Redis `pipeline:queue`
2. **Worker 消费**（`pipeline_queue.py:_worker_loop`）
   - `BRPOP` 阻塞取任务（15s 超时轮询）
   - 取到后检查该用户并发数，超 `PIPELINE_MAX_PER_USER` 则 `RPUSH` 回队尾等待
   - 进程启动时 recovery（`list_resumable_imports`）会把 DB 里 `queued`/`generating` 的任务重新入队
3. **执行四步**（`platforms/temu/pipeline.py:execute`）
   - **step1**：源图/视频上传 OSS（视频失败不阻断）
   - **step2**：DeepSeek 翻译商品标题（中/英）—— 与 step3 并行
   - **step3**：视觉模型解析轮播图，选参考图 + 生成 prompt —— 与 step2 并行
   - **step4**：VibeLearning 按提示词并行生成新图（`MAX_PARALLEL=10`），上传 OSS
   - 全程受 `PIPELINE_TOTAL_TIMEOUT`（15 分钟）deadline 保护
4. 每一步都 `update_status` 写 DB，前端轮询可查进度；终态 `done`/`error`

## 鉴权

- **Session cookie**：登录后写 `ppe_session`（`secure` 由 `APP_ENV` 控制）
- **Bearer API Key**：插件走 `Authorization: Bearer <key>`（`/api/temu/*` 路由，`_plugin_user` 校验）
- **API Key 管理**：网站「设置 → 插件连接」可查看/重置，`POST /api/auth/api-key/reset`
- **开发账号**：`APP_ENV != production` 时自动创建 `admin / 123456`
- SMS/邮箱验证目前是 stub（`SMS_PROVIDER=console` 打印，或 `aliyun` 走阿里云短信）

## 常用运维检查

```bash
# 基础设施状态
docker compose ps

# 应用是否在监听
ss -tlnp | grep 6688

# 后端 service 状态 / 日志
systemctl status product-pipeline.service
tail -f /var/log/product-pipeline.log

# Caddy 状态 / 日志
systemctl status caddy
journalctl -u caddy -f

# 健康检查
curl -k https://localhost:8443/api/health   # 经 Caddy
curl http://127.0.0.1:6688/api/health       # 直连应用

# 队列堆积情况（0 = 空闲）
redis-cli -p 6380 llen pipeline:queue

# Redis 连接数（正常几十；上千 = 连接泄漏，见下方"已知坑"）
redis-cli -p 6380 info clients | grep connected_clients

# 卡住的任务（看 status/updated_at，长时间不变的可能是僵尸）
docker exec product-pipeline-postgres psql -U product_pipeline_user -d product_pipeline \
  -c "select id,user_id,status,status_msg,updated_at from imports where status not in ('done','error') order by id desc;"
```

## 已知坑与运维经验

### VS Code Remote 端口转发泄漏

VS Code Remote SSH 会自动转发监听中的端口（含 Redis 6380），其探测逻辑会大量建 TCP 连接不释放，撑爆 Redis 连接数，导致后端 worker `brpop` 失败、任务卡在 `queued`。

**已做的修复（三层防御）：**
1. **根治**：`/root/.vscode-server/data/Machine/settings.json` 设置 `remote.autoForwardPorts: false`，永久关闭 VS Code 自动端口转发。
2. **兜底**：`docker-compose.yml` 给 Redis 配 `--timeout 120 --tcp-keepalive 60`，空闲超过 2 分钟的连接自动回收。
3. **监控**：`redis-cli -p 6380 info clients | grep connected_clients`，正常几十个，上千就要排查。

### Redis 重启丢队列

Redis 队列是内存态，重启后队列清空，但 DB 里状态仍是 `queued`。

**恢复方式**：重启后端 `systemctl restart product-pipeline.service`，启动时 `list_resumable_imports` 会把 `queued`/`generating` 的任务自动重新入队。

### AI API 卡死导致僵尸任务

外部 AI API（视觉/图生图）可能 hang 住不返回，旧版本会无限占着 worker。

**已做的修复：**
- 单次图生图请求超时 5 分钟，最多重试 2 次（`IMAGE_ATTEMPT_TIMEOUT=300` / `MAX_IMAGE_ATTEMPTS=2`）
- 整条流水线兜底 15 分钟（`PIPELINE_TOTAL_TIMEOUT=900`），超时强制判 `error`
- 翻译/视觉线程用 `join(timeout=)` 包裹，到点不返回直接判超时

### worker `_active_per_user` 计数泄漏（已知缺陷）

`pipeline_queue.py` 的每用户并发计数器（`_active_per_user`）是进程内存态，进程异常退出时可能泄漏（计数没正确归零），导致该用户的任务被误判"已在运行"而反复 `RPUSH` 回队尾却不执行。

**临时解决**：重启后端 `systemctl restart product-pipeline.service` 清空内存计数器。
