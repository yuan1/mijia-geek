"""API 辅助函数：规则 userData 与变量类型校验。"""
from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

from fastapi import HTTPException

from api.models import SaveRuleReq
from service import NotConnectedError

_LOGGER = logging.getLogger("mijia_bridge.api")

VAR_TYPES = ("string", "number")


def gateway_http_error(err: Exception) -> HTTPException:
    """把网关异常映射为稳定的 HTTP 状态码。"""
    if isinstance(err, HTTPException):
        return err
    if isinstance(err, NotConnectedError):
        return HTTPException(503, detail=str(err))
    if isinstance(err, (asyncio.TimeoutError, TimeoutError)):
        return HTTPException(504, detail="网关请求超时")
    if isinstance(err, ConnectionError):
        return HTTPException(503, detail=str(err))
    _LOGGER.exception("网关调用失败")
    return HTTPException(502, detail=str(err))


def rule_user_data(rule: dict) -> dict:
    """尽量保留规则原有的布局信息。"""
    base = dict(rule.get("userData") or {}) if isinstance(rule.get("userData"), dict) else {}
    base.setdefault("name", rule.get("name", ""))
    base["lastUpdateTime"] = rule.get("updateTime", 0)
    if not isinstance(base.get("transform"), dict):
        base["transform"] = {"x": 0, "y": 0, "scale": 1, "rotate": 0}
    return base


def build_save_cfg(rule_id: str, req: SaveRuleReq,
                   existing: dict | None = None) -> dict:
    cfg = dict(req.cfg or {})
    cfg["id"] = rule_id
    cfg.setdefault("uiType", "graph")
    if "enable" not in cfg:
        if existing is not None and "enabled" in existing:
            cfg["enable"] = bool(existing.get("enabled"))
        else:
            cfg["enable"] = False
    user_data = cfg.get("userData")
    if not isinstance(user_data, dict):
        user_data = {}
    user_data.setdefault("name", req.name or rule_id)
    user_data.setdefault("lastUpdateTime", 0)
    if not isinstance(user_data.get("transform"), dict):
        user_data["transform"] = {"x": 0, "y": 0, "scale": 1, "rotate": 0}
    cfg["userData"] = user_data
    return cfg


def validate_var_type(var_type: str) -> str:
    if var_type not in VAR_TYPES:
        raise HTTPException(400, detail=f"变量类型必须是 {'/'.join(VAR_TYPES)}")
    return var_type


def coerce_create_value(var_type: str, value: Any) -> Any:
    """按变量类型校验/补默认值（createVar）。"""
    if var_type == "number":
        if value in (None, ""):
            return 0
        if isinstance(value, bool):
            raise HTTPException(400, detail="number 类型变量不能使用布尔值")
        try:
            return float(value) if "." in str(value) else int(value)
        except (TypeError, ValueError):
            raise HTTPException(400, detail="number 类型变量的值必须是数字")
    return "" if value is None else str(value)


def validate_set_value(value: Any) -> Any:
    if isinstance(value, float) and math.isnan(value):
        raise HTTPException(400, detail="变量值不能是 NaN")
    return value
