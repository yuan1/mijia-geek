"""REST API 路由聚合。"""
from __future__ import annotations

from fastapi import APIRouter

from api import devices, events, logs, rules, status, variables

router = APIRouter()
router.include_router(status.router)
router.include_router(devices.router)
router.include_router(rules.router)
router.include_router(variables.router)
router.include_router(logs.router)
router.include_router(events.router)

__all__ = ["router"]
