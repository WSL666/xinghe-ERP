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

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from config import FRONTEND_ROOT, get_settings
from store import (
    close_pool, get_or_create_dev_user, init_db, list_resumable_imports, open_pool,
)
import pipeline_queue
from orchestrator import worker_handler


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
    if settings.app_env != "production":
        get_or_create_dev_user()
    # 崩溃恢复:重新入队上次进程死亡时未完成的任务
    try:
        for row in list_resumable_imports():
            pipeline_queue.enqueue_pipeline(int(row["user_id"]), int(row["id"]))
    except Exception:
        import logging
        logging.getLogger("startup").exception("re-enqueue failed (redis down?)")
    # 启动 worker,handler 为 orchestrator.worker_handler(按平台分发)
    pipeline_queue.start_workers(worker_handler)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _startup()
    try:
        yield
    finally:
        close_pool()
        pipeline_queue.stop_workers()


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


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": str(exc.detail)})
