"""状态 / 连接 / 刷新 / 健康检查。"""
from __future__ import annotations

import re
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_service, get_state, require_gateway
from api.models import ConnectReq
from api.utils import gateway_http_error
from config import editor_url_for
from protocol import MijiaGateway
from service import GatewayService
from state import AppState

router = APIRouter()

_CONNECT_CODE = re.compile(r"[0-9]{6}")
_CONNECT_MIN_INTERVAL = 2.0


@router.get("/api/status")
async def api_status(state: AppState = Depends(get_state)):
    live = bool(state.connected and state.gateway is not None
                and state.gateway.connected)
    return {
        "connected": live,
        "gateway_url": state.gateway_url,
        "gateway_web_url": editor_url_for(state.gateway_url),
        "device_count": len(state.devices),
        "online_count": sum(1 for d in state.devices.values() if d.get("online")),
        "rule_count": len(state.rules),
        "enabled_count": sum(1 for r in state.rules if r.get("enabled")),
        "last_log": state.last_log,
        "last_push": state.last_push,
        "last_error": state.last_error,
        "has_passcode": bool(state.passcode and live),
        "push_seq": state.push_seq,
    }


@router.post("/api/connect")
async def api_connect(
    req: ConnectReq,
    service: GatewayService = Depends(get_service),
):
    code = (req.passcode or "").strip()
    if not _CONNECT_CODE.fullmatch(code):
        raise HTTPException(400, detail="登录码必须是 6 位数字")

    state = service.state
    now = time.monotonic()
    if now - state.last_connect_at < _CONNECT_MIN_INTERVAL:
        raise HTTPException(429, detail="连接尝试过于频繁，请稍后再试")
    state.last_connect_at = now

    new_url: str | None = None
    if req.gateway_url:
        new_url = req.gateway_url.strip().rstrip("/")
        parts = urlsplit(new_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise HTTPException(400, detail="网关地址必须是有效的 http(s) URL")
        if parts.username or parts.password:
            raise HTTPException(400, detail="网关地址不能包含用户名/密码")
        try:
            _ = parts.port
        except ValueError:
            raise HTTPException(400, detail="网关地址端口无效")

    ok, err = await service.connect(code, new_url)
    if not ok:
        if err == "正在连接中，请稍候":
            raise HTTPException(409, detail=err)
        if "TimeoutError" in err or "Timeout" in err:
            raise HTTPException(504, detail=err)
        raise HTTPException(502, detail=err)
    return {
        "ok": True,
        "device_count": len(service.state.devices),
        "rule_count": len(service.state.rules),
    }


@router.post("/api/refresh")
async def api_refresh(
    service: GatewayService = Depends(get_service),
    gateway: MijiaGateway = Depends(require_gateway),
):
    try:
        device_count, rule_count = await service.refresh()
        return {"ok": True, "device_count": device_count,
                "rule_count": rule_count}
    except Exception as err:
        raise gateway_http_error(err) from err


@router.get("/api/health")
async def api_health():
    return {"ok": True}
