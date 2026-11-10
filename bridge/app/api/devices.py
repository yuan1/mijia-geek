"""设备列表接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_state, require_gateway
from protocol import MijiaGateway
from state import AppState

router = APIRouter()


@router.get("/api/devices")
async def api_devices(
    state: AppState = Depends(get_state),
    gateway: MijiaGateway = Depends(require_gateway),
):
    return {"devices": state.devices, "count": len(state.devices)}
