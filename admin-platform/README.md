# admin-platform · 超级管理员系统（1级）

> **只有平台所有者（超管）可访问。** 独立进程、独立端口、独立登录态，与主应用物理隔离。

## 当前状态：✅ 全部完成

九大模块（P0-P3）已全部开发并测试通过，可投入生产使用。

## 三级权限体系

| 级别 | 角色 | 系统 | 入口地址 | 数据可见范围 |
|------|------|------|---------|------------|
| **1级** | 超级管理员 | `admin-platform/`（本目录） | `:8444` | **全平台所有人** |
| 2级 | 企业管理员 | `admin-enterprise/` | （待开发） | 仅本企业 |
| 3级 | 普通用户 | `backend/` + `frontend/` | `:8443` | 仅自己 |

超管登录后能查看、管控全平台的所有用户、企业、任务、计费和系统资源。

## 访问方式

- **地址**：`https://wangshilin888.com:8444`（Caddy 反代 → `127.0.0.1:6689`）
- **默认账号**：`admin` / `admin123`（**首次登录后必改**）
- 与用户端 `:8443` 完全隔离，登录态不共用

## 技术栈

- **后端**：FastAPI + Uvicorn，独立进程监听 `127.0.0.1:6689`
- **前端**：纯静态 HTML / CSS / JS（方案 A），由后端 StaticFiles 托管，强制 no-cache
- **数据库**：共享主应用的 PostgreSQL（同一个 DB），只读 + 受控写入
- **Redis**：共享主应用的 Redis（Unix socket），用于 AI Key 池管理
- **鉴权**：独立的超管账号体系（`platform_admins` 表）+ 独立 Session cookie `ppe_admin_session`

## 目录结构

```
admin-platform/
├── backend/
│   ├── main.py              # FastAPI 入口（端口 6689）
│   ├── config.py            # 配置：共享 DATABASE_URL，独立 ADMIN_PORT / ADMIN_SECRET
│   ├── deps.py              # 超管鉴权依赖 require_admin()
│   ├── store.py             # 超管专用查询层（统计聚合 / 跨用户查询 / 审计写入）
│   └── routers/
│       ├── auth.py          # 登录 / 登出
│       ├── dashboard.py     # 运营驾驶舱
│       ├── users.py         # 用户管理
│       ├── enterprises.py   # 企业管理
│       ├── billing.py       # 计费与财务
│       ├── tasks.py         # 任务监控
│       ├── ai.py            # AI 资源管理（Key 池 CRUD）
│       ├── pricing.py       # 定价配置
│       ├── monitoring.py    # 错误中心 + 队列监控
│       └── audit.py         # 安全审计
├── frontend/
│   ├── index.html           # 登录页（独立入口）
│   ├── dashboard.html       # 驾驶舱
│   └── assets/
│       ├── css/admin.css
│       └── js/
│           ├── app.js       # 路由 / 鉴权 / 请求封装
│           └── ...          # 各模块页面 JS
└── README.md
```

## 功能模块（9 大模块，已全部完成）

| 模块 | 说明 | 状态 |
|------|------|------|
| 运营驾驶舱 | 全平台实时数据看板（12个统计卡可点击跳转+自动筛选）+ 实时任务流（5秒刷新） | ✅ |
| 用户管理 | 列表 / 详情 / 充值 / 冻结 / 禁用 / **软删除归档** / 完整档案抽屉（基本信息+任务+流水+订单） | ✅ |
| 企业管理 | 列表 / 详情 / 成员管理 / 冻结 / **企业消费排行** | ✅ |
| 计费与财务 | 金豆流水 / 充值订单 / 消费排行 / 财务报表 | ✅ |
| 任务监控 | 富格式表格（三行标题+图片+视频+金豆+来源+完整时间），7种搜索，AI图片增删（同工作台），灯箱左右翻页 | ✅ |
| 错误中心 | 分类失败统计 + 重试 | ✅ |
| AI 资源 | API Key 池管理（视觉解析 / 图片生成，融合界面：粘贴添加 + Key 列表 + 独立统计） | ✅ |
| 定价配置 | 定价 CRUD | ✅ |
| 安全审计 | 操作日志 / 登录日志 | ✅ |

### AI 资源模块（Key 池管理）

原主应用的 `/admin/keys` 面板已删除，Key 管理**全部迁移到这里**：

- 两个独立的 Key 池：`chat`（视觉解析模型）、`vibe`（图片生成模型）
- 融合界面：点击模型 Tab 切换，各自独立统计（可用 / 冷却 / 失效数量）
- 直接粘贴 Key 添加，无需再通过文件批量导入
- 失败原因完整展示（不截断）
- Redis 实时读写，24 项读写测试全部通过

### 用户软删除（归档）

用户删除不是物理删除，而是**软删除**：

- 标记 `is_deleted=true` + `is_active=false` + 手机号改名（释放原号）
- 用户无法登录，但**所有历史数据完整保留**（任务 / 金豆 / 图片 / 视频）
- 超管可在用户管理筛选「已删除」查看归档记录
- 该手机号可重新注册新账号（新账号看不到旧账号历史）

### 任务监控富格式

与用户工作台完全一致的显示效果：

- **三行标题**：源标题 / 中文标题 / 英文标题（带彩色标签）
- **图片列**：源图一行 + AI 图一行（全部展示），已删除图折叠区
- **视频列**：源视频行 + AI 视频槽位
- **灯箱预览**：左右翻页 + 键盘箭头
- **AI 图片增删**：用作成品 / 软删除 / 还原（同工作台操作）
- **7种搜索**：关键词 / 用户 / 编号 / 平台 / 状态 / 时间范围 / 错误信息

## 启动方式

**生产环境（systemd 托管，已配置开机自启）**：

```bash
systemctl start admin-platform.service      # 启动
systemctl restart admin-platform.service    # 重启（改代码后执行）
systemctl status admin-platform.service     # 查看状态
tail -f /var/log/admin-platform.log         # 查看日志
```

> service 文件位置：`/etc/systemd/system/admin-platform.service`

**开发/调试（手动启动）**：

```bash
cd admin-platform/backend
python main.py
# 或
uvicorn main:app --host 127.0.0.1 --port 6689
```

默认监听 `127.0.0.1:6689`，仅本机可访问；生产环境由 Caddy 反代（`:8444`）对外。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `ADMIN_PORT` | `6689` | 监听端口 |
| `DATABASE_URL` | （继承主应用 `backend/.env`） | PostgreSQL 连接串，与主应用共用 |
| `ADMIN_SECRET_KEY` | `admin-dev-change-me` | 超管 Session 签名密钥（**生产必改**） |
| `ADMIN_COOKIE_NAME` | `ppe_admin_session` | 超管 Session cookie 名 |
| `ADMIN_DEFAULT_USERNAME` | `admin` | 首次启动自动创建的初始超管账号 |
| `ADMIN_DEFAULT_PASSWORD` | `admin123` | 初始密码（**首次登录后必改**） |
| `ADMIN_ALLOW_CIDR` | （空=不限制） | 可选 IP 白名单 CIDR |
| `ADMIN_CORS_ORIGINS` | （空=fallback） | CORS 白名单，逗号分隔 |
| `REDIS_URL` | （继承主应用） | Redis 连接（Unix socket） |

> 配置来源：共享主应用的 `backend/.env`，读取 `DATABASE_URL` / `REDIS_URL` / `APP_ENV`。

## 安全说明

- 超管账号体系独立于普通 `users` 表，**不共用登录态**，从根上杜绝越权
- 所有 `/api/admin/*` 接口都要过 `require_admin()` 鉴权依赖
- 敏感操作（充值 / 冻结 / 删除）强制写审计日志（`admin_audit_logs`）
- 可选 IP 白名单（`ADMIN_ALLOW_CIDR`）
- 独立端口 `8444` 与用户端 `8443` 隔离，物理上分离
- 初始密码仅用于首次登录，登录后应立即修改

## 数据库新增表（增量，不动现有表结构）

- `platform_admins` —— 超管账号
- `admin_audit_logs` —— 操作审计
- `recharge_orders` —— 充值订单
- `pricing_configs` —— 定价配置
- `feature_flags` —— 功能开关
- `announcements` —— 系统公告

现有表仅加列：`users.is_frozen`、`users.is_deleted`、`users.deleted_at`、`enterprises.is_frozen`、`enterprises.plan_type`。
