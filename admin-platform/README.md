# admin-platform · 超级管理员系统（1级）

> **只有平台所有者（超管）可访问。** 独立进程、独立端口、独立登录态，与主应用物理隔离。

## 三级权限体系

| 级别 | 角色 | 系统 | 数据可见范围 |
|------|------|------|------------|
| **1级** | 超级管理员 | `admin-platform/`（本目录） | **全平台所有人** |
| 2级 | 企业管理员 | `admin-enterprise/` | 仅本企业 |
| 3级 | 普通用户 | `backend/` + `frontend/` | 仅自己 |

本目录是 **1级**。超管登录后能查看、管控全平台的所有用户、企业、任务、计费和系统资源。

## 技术栈

- **后端**：FastAPI + Uvicorn，独立进程监听 `127.0.0.1:6689`
- **前端**：纯静态 HTML / CSS / JS（方案 A），由后端 StaticFiles 托管
- **数据库**：共享主应用的 PostgreSQL（同一个 DB），只读 + 受控写入
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
│       ├── ai.py            # AI 资源管理
│       ├── system.py        # 系统配置
│       └── audit.py         # 安全审计
├── frontend/
│   ├── index.html           # 登录页（独立入口）
│   ├── dashboard.html       # 驾驶舱
│   └── assets/
│       ├── css/admin.css
│       └── js/
│           ├── app.js       # 路由 / 鉴权 / 请求封装
│           ├── dashboard.js
│           ├── users.js
│           └── ...
├── deploy/
│   └── admin-platform.service   # systemd 单元
└── README.md
```

## 功能模块（9 大模块）

| 模块 | 说明 |
|------|------|
| 运营驾驶舱 | 全平台实时数据看板：用户数 / 企业数 / 采集量 / 生图量 / 队列 / 系统健康 |
| 用户管理 | 全平台 3级用户：列表 / 详情 / 冻结 / 调余额 / 重置Key / 强制下线 |
| 企业管理 | 全平台 2级企业：入驻审批 / 冻结 / 成员 / 用量 |
| 计费与财务 | 金豆充值 / 流水 / 定价配置 / 财务报表 |
| 任务监控 | 全平台任务：总览 / 详情 / 队列监控 / 错误中心 / 重试 |
| AI 资源 | API Key 池 / 模型配置 / Prompt 模板 / 成本核算 |
| 内容与存储 | OSS 用量 / 作品库 / 导出文件管理 |
| 系统运维 | 并发配置 / 功能开关 / 公告 / 插件版本 |
| 安全审计 | 操作日志 / 登录日志 / 异常检测 |

## 启动方式

```bash
cd admin-platform/backend
# 初始化数据库（首次）
python -c "from store import init_db; init_db()"
# 启动
python main.py
# 或
uvicorn main:app --host 127.0.0.1 --port 6689
```

默认监听 `127.0.0.1:6689`，仅本机可访问；生产环境由 Caddy 反代对外。

## 环境变量

| 变量 | 默认 | 说明 |
|------|------|------|
| `ADMIN_PORT` | `6689` | 监听端口 |
| `DATABASE_URL` | （继承主应用） | PostgreSQL 连接串，与主应用共用 |
| `ADMIN_SECRET_KEY` | `admin-dev-change-me` | 超管 Session 签名密钥（**生产必改**） |
| `ADMIN_COOKIE_NAME` | `ppe_admin_session` | 超管 Session cookie 名 |
| `ADMIN_DEFAULT_USERNAME` | `admin` | 首次启动自动创建的初始超管账号 |
| `ADMIN_DEFAULT_PASSWORD` | `admin123` | 初始密码（**首次登录后必改**） |
| `ADMIN_ALLOW_CIDR` | （空=不限制） | 可选 IP 白名单 CIDR |

## 安全说明

- 超管账号体系独立于普通 `users` 表，**不共用登录态**，从根上杜绝越权
- 所有 `/api/admin/*` 接口都要过 `require_admin()` 鉴权依赖
- 敏感操作（充值 / 冻结 / 删除）强制写审计日志（`admin_audit_logs`）
- 可选 IP 白名单（`ADMIN_ALLOW_CIDR`）
- 初始密码仅用于首次登录，登录后应立即修改

## 数据库新增表（增量，不动现有表结构）

- `platform_admins` —— 超管账号
- `admin_audit_logs` —— 操作审计
- `recharge_orders` —— 充值订单
- `pricing_configs` —— 定价配置
- `feature_flags` —— 功能开关
- `announcements` —— 系统公告

现有表仅加列：`users.is_frozen`、`enterprises.is_frozen`、`enterprises.plan_type`。

## 开发顺序

- **P0**：骨架 + 登录 + 驾驶舱壳（当前）
- **P1**：驾驶舱 + 用户管理 + 企业管理 + 金豆充值
- **P2**：任务监控 + 错误中心 + 队列监控
- **P3**：AI 资源 + 定价配置 + 财务报表
- **P4**：审计日志 + 公告 + 功能开关
