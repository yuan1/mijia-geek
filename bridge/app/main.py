"""米家极客版桥接服务 —— FastAPI 入口。

职责：
  1. 维持与中枢网关的长连接（心跳保活）
  2. 提供 REST API + SSE 推送
  3. 提供内嵌 Web UI 配置登录码

设计要点：
  · 连接由本服务独占维护，HA / 其他系统只通过 REST 访问
  · 登录码一次性，绝不持久化
  · 业务状态在 state.py，连接逻辑在 service.py，协议在 protocol/
"""
from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from api import router
from config import API_TOKEN
from service import GatewayService
from state import AppState

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
_LOGGER = logging.getLogger("mijia_bridge")

state = AppState()
service = GatewayService(state)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _LOGGER.info("=== Mijia Geek Bridge 启动 ===")
    state.load()
    _LOGGER.info("网关: %s", state.gateway_url)
    if API_TOKEN:
        _LOGGER.info("API Token 已启用（/api/health 除外）")
    else:
        _LOGGER.warning("未设置 API_TOKEN，请仅通过 HA ingress 访问本服务")
    _LOGGER.info("登录码一次性且不持久化，请通过 Web UI 或 POST /api/connect 输入")

    app.state.service = service
    service.start_poll()
    try:
        yield
    finally:
        await service.shutdown()
        _LOGGER.info("=== 服务停止 ===")


app = FastAPI(title="Mijia Geek Bridge", lifespan=lifespan)
app.state.service = service


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    """可选的 API Token 鉴权；未设置 API_TOKEN 时保持向后兼容。"""
    path = request.url.path
    protected = path.startswith("/api") and path != "/api/health"
    if API_TOKEN and protected:
        supplied = request.headers.get("X-API-Token", "")
        if not supplied:
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                supplied = auth[7:]
        if not hmac.compare_digest(supplied.encode("utf-8"),
                                   API_TOKEN.encode("utf-8")):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


app.include_router(router)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def ui_index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")
