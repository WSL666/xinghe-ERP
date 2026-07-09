"""应用入口:组装 FastAPI app,挂载公共路由 + 各平台路由 + 队列。

本文件只做"组装",不写业务逻辑:
  - 配置 lifespan(启动DB池+队列,关闭时清理)
  - 挂载 CORS/Session 中间件
  - 挂载静态资源
  - 挂载公共路由(core.app)
  - 挂载各平台路由(platforms.temu.router)

新增平台(如 1688):在 platforms/alibaba1688/ 写好 router.py,
然后在这里加一行 app.include_router(...)。
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from config import FRONTEND_ROOT, get_settings
from store import (
    close_pool, get_or_create_dev_user, init_db, open_pool,
)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


settings = get_settings()


class NoCacheStaticFiles(StaticFiles):
    """静态文件强制 no-cache,避免浏览器缓存旧版 CSS/JS。"""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if resp.status_code == 200:
            resp.headers["cache-control"] = "no-cache"
        return resp


def _startup() -> None:
    """启动:开DB池 + 初始化表 + 恢复中断任务 + 启动worker。"""
    open_pool()
    init_db()
    # 金豆消费记录表(billing 模块)
    from billing.store import init_billing_tables
    init_billing_tables()
    # API Key 池自愈:池子为空时从 .env 自动恢复兜底 key(防 FLUSHDB/Redis 重启后丢失)
    from api_key_pool.pool import bootstrap_from_env
    bootstrap_from_env()
    if settings.app_env != "production":
        get_or_create_dev_user()
    # 崩溃恢复已移交 worker 进程独占(worker.py 启动时清队列 + 重新入队)。
    # web 进程不再做崩溃恢复,避免和 worker 产生"两个消费者都入队"的竞态。
    # worker 已拆到独立进程(worker.py),web 默认不内嵌 worker。
    # 测试/兼容旧用法:设 PIPELINE_EMBED_WORKERS=1 时仍在 web 内起 worker。
    if os.getenv("PIPELINE_EMBED_WORKERS", "").strip().lower() in {"1", "true", "yes", "on"}:
        # 仅测试/兼容旧用法时在 web 内嵌 worker(惰性 import,避免非 embed 模式下的多余依赖)
        import mq.redis_queue as _pq
        from mq.orchestrator import worker_handler as _wh
        _pq.start_workers(_wh)
        logging.getLogger("startup").info("embedded workers enabled (PIPELINE_EMBED_WORKERS=1)")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _startup()
    try:
        yield
    finally:
        close_pool()
        if os.getenv("PIPELINE_EMBED_WORKERS", "").strip().lower() in {"1", "true", "yes", "on"}:
            import mq.redis_queue as _pq
            _pq.stop_workers()


app = FastAPI(title="Product Pipeline Digital App", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins) if settings.cors_origins
                   else ["https://localhost:8443", "https://127.0.0.1:8443"],
    allow_origin_regex=r"chrome-extension://.*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key)

# 静态资源
app.mount("/frontend", NoCacheStaticFiles(directory=str(FRONTEND_ROOT)), name="frontend")
app.mount(
    "/dashboard/assets",
    NoCacheStaticFiles(directory=str(FRONTEND_ROOT / "dashboard" / "assets")),
    name="dashboard-assets",
)

# ── 挂载路由 ──
# 公共路由(auth/enterprise/页面/health)
from core.app import router as common_router  # noqa: E402
app.include_router(common_router)

# 各平台路由(新增平台在此加一行)
from platforms.temu.router import router as temu_router  # noqa: E402
app.include_router(temu_router)

# API Key 池管理已迁移到超级管理员系统(admin-platform)的 AI 资源模块
# pool.py 引擎仍由流水线使用, 仅移除了旧的 /admin/keys HTML 面板入口

# 充值/金豆(/api/billing/*)
from billing import router as billing_router  # noqa: E402
app.include_router(billing_router)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": str(exc.detail)})
