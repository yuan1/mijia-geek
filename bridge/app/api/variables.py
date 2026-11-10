"""变量相关接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from api.deps import require_gateway
from api.models import CreateVarReq, SetVarReq, VarReq
from api.utils import (coerce_create_value, gateway_http_error, validate_set_value, validate_var_type)
from protocol import MijiaGateway

router = APIRouter()


@router.get("/api/variables")
async def api_variables(
    scope: str = "global",
    gateway: MijiaGateway = Depends(require_gateway),
):
    try:
        variables = await gateway.get_var_list(scope)
        return {"scope": scope, "variables": variables or {}}
    except Exception as err:
        raise gateway_http_error(err) from err


@router.get("/api/variable/scopes")
async def api_var_scopes(gateway: MijiaGateway = Depends(require_gateway)):
    try:
        return {"scopes": await gateway.get_var_scope_list()}
    except Exception as err:
        raise gateway_http_error(err) from err


@router.get("/api/variable/{var_id}")
async def api_var_get(
    var_id: str,
    scope: str = "global",
    gateway: MijiaGateway = Depends(require_gateway),
):
    try:
        value = await gateway.get_var_value(scope, var_id)
        return {"scope": scope, "var_id": var_id, "value": value}
    except Exception as err:
        raise gateway_http_error(err) from err


@router.put("/api/variable/{var_id}")
async def api_var_set(
    var_id: str,
    req: SetVarReq,
    gateway: MijiaGateway = Depends(require_gateway),
):
    value = validate_set_value(req.value)
    try:
        await gateway.set_var_value(req.scope, var_id, value)
        return {"ok": True}
    except Exception as err:
        raise gateway_http_error(err) from err


@router.post("/api/variable")
async def api_var_set_legacy(
    req: VarReq,
    gateway: MijiaGateway = Depends(require_gateway),
):
    """兼容旧客户端；推荐 PUT /api/variable/{var_id}。"""
    value = validate_set_value(req.value)
    try:
        await gateway.set_var_value(req.scope, req.var_id, value)
        return {"ok": True}
    except Exception as err:
        raise gateway_http_error(err) from err


@router.post("/api/variables")
async def api_var_create(
    req: CreateVarReq,
    gateway: MijiaGateway = Depends(require_gateway),
):
    var_type = validate_var_type(req.var_type)
    value = coerce_create_value(var_type, req.value)
    try:
        await gateway.create_var(req.scope, req.var_id, var_type,
                                 value, req.name)
        return {"ok": True}
    except Exception as err:
        raise gateway_http_error(err) from err


@router.delete("/api/variable/{var_id}")
async def api_var_delete(
    var_id: str,
    scope: str = Query("global"),
    gateway: MijiaGateway = Depends(require_gateway),
):
    try:
        await gateway.delete_var(scope, var_id)
        return {"ok": True}
    except Exception as err:
        raise gateway_http_error(err) from err
