"""FastAPI 依赖。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from protocol import MijiaGateway
from service import GatewayService, NotConnectedError
from state import AppState


def get_service(request: Request) -> GatewayService:
    return request.app.state.service


def get_state(service: GatewayService = Depends(get_service)) -> AppState:
    return service.state


def require_gateway(
    service: GatewayService = Depends(get_service),
) -> MijiaGateway:
    try:
        return service.ensure_gateway()
    except NotConnectedError:
        raise HTTPException(503, detail="网关未连接")
