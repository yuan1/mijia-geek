"""规则相关接口。"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_state, require_gateway
from api.models import SaveRuleReq, ToggleReq
from api.utils import (
    build_save_cfg,
    gateway_http_error,
    prepare_nodes,
    rule_user_data,
)
from protocol import MijiaGateway
from state import AppState

_LOGGER = logging.getLogger("mijia_bridge.api")

router = APIRouter()


@router.get("/api/rules")
async def api_rules(
    state: AppState = Depends(get_state),
    gateway: MijiaGateway = Depends(require_gateway),
):
    return {"rules": state.rules, "count": len(state.rules)}


@router.get("/api/rule/{rule_id}")
async def api_rule_detail(
    rule_id: str,
    gateway: MijiaGateway = Depends(require_gateway),
):
    try:
        return await gateway.get_graph(rule_id)
    except Exception as err:
        raise gateway_http_error(err) from err


@router.put("/api/rule/{rule_id}")
async def api_rule_save(
    rule_id: str,
    req: SaveRuleReq,
    gateway: MijiaGateway = Depends(require_gateway),
    state: AppState = Depends(get_state),
):
    if not isinstance(req.nodes, list):
        raise HTTPException(400, detail="nodes 必须是数组")
    existing = next(
        (r for r in state.rules if str(r.get("id")) == rule_id), None)
    # 先归一化 + 校验，避免把网关必然拒绝的结构发出去
    nodes, changes = prepare_nodes(req.nodes, state.specs)
    if changes:
        _LOGGER.info("规则 %s 保存前已归一化 %d 处: %s", rule_id, len(changes), changes)
    cfg = build_save_cfg(rule_id, req, existing)
    try:
        await gateway.set_graph(rule_id, nodes, cfg)
        state.rules = await gateway.get_graph_list() or []
        return {"ok": True, "rule_id": rule_id}
    except Exception as err:
        raise gateway_http_error(err) from err


@router.post("/api/rule/{rule_id}/toggle")
async def api_rule_toggle(
    rule_id: str,
    req: ToggleReq,
    gateway: MijiaGateway = Depends(require_gateway),
    state: AppState = Depends(get_state),
):
    rule = next((r for r in state.rules if str(r.get("id")) == rule_id), None)
    if rule is None:
        raise HTTPException(404, detail="规则不存在或列表已过期，请刷新")
    try:
        await gateway.change_graph_config(rule_id, req.enable,
                                          rule_user_data(rule))
        state.rules = await gateway.get_graph_list() or []
        return {"ok": True}
    except Exception as err:
        raise gateway_http_error(err) from err


@router.delete("/api/rule/{rule_id}")
async def api_rule_delete(
    rule_id: str,
    gateway: MijiaGateway = Depends(require_gateway),
    state: AppState = Depends(get_state),
):
    try:
        await gateway.delete_graph(rule_id)
        state.rules = await gateway.get_graph_list() or []
        return {"ok": True}
    except Exception as err:
        raise gateway_http_error(err) from err
