"""HA 服务注册与处理。"""
from __future__ import annotations

import json
import logging

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    SERVICE_REFRESH,
    SERVICE_TOGGLE_RULE,
    SERVICE_SET_ENABLED,
    SERVICE_GET_GRAPH,
    SERVICE_GET_LOG,
    SERVICE_GET_LOG_ALL,
    SERVICE_DELETE_RULE,
    SERVICE_SET_VAR,
    SERVICE_GET_VAR,
    SERVICE_GET_VAR_SCOPES,
    SERVICE_CREATE_VAR,
    SERVICE_DELETE_VAR,
    SERVICE_SAVE_RULE,
    SERVICE_SET_PASSCODE,
)
from .coordinator import MijiaGeekCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICES = (
    SERVICE_REFRESH, SERVICE_TOGGLE_RULE, SERVICE_SET_ENABLED,
    SERVICE_GET_GRAPH, SERVICE_GET_LOG, SERVICE_GET_LOG_ALL,
    SERVICE_DELETE_RULE, SERVICE_SET_VAR, SERVICE_GET_VAR,
    SERVICE_GET_VAR_SCOPES, SERVICE_CREATE_VAR, SERVICE_DELETE_VAR,
    SERVICE_SAVE_RULE, SERVICE_SET_PASSCODE,
)


def async_unregister_services(hass: HomeAssistant) -> None:
    for service in SERVICES:
        if hass.services.has_service(DOMAIN, service):
            hass.services.async_remove(DOMAIN, service)


def _coerce_var_value(value, info):
    """按 Bridge 返回的变量类型（number / string）转换调用方的值。"""
    if not isinstance(info, dict):
        return value
    vtype = info.get("type")
    if vtype == "number":
        if isinstance(value, bool):
            raise ValueError("number 类型变量不能使用布尔值")
        if isinstance(value, str):
            try:
                return float(value) if "." in value else int(value)
            except ValueError:
                raise ValueError("number 类型变量的值必须是数字")
        return value
    if vtype == "string":
        return value if isinstance(value, str) else str(value)
    return value


def async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_REFRESH):
        return

    def _coord(call: ServiceCall) -> MijiaGeekCoordinator:
        entries = [
            value for value in hass.data.get(DOMAIN, {}).values()
            if isinstance(value, dict) and value.get("coordinator")
        ]
        if not entries:
            raise ValueError("没有已配置的 Bridge")
        if len(entries) > 1:
            urls = ", ".join(
                entry["coordinator"].bridge_url for entry in entries)
            raise ValueError(
                f"检测到多个 Bridge（{urls}），服务暂不支持定向调用")
        return entries[0]["coordinator"]

    async def handle_refresh(call: ServiceCall):
        await _coord(call).async_refresh_bridge()

    async def handle_toggle_rule(call: ServiceCall):
        await _coord(call).async_set_rule_enabled(
            call.data["rule_id"], call.data["enable"])

    async def handle_get_graph(call: ServiceCall):
        c = _coord(call)
        rid = call.data["rule_id"]
        detail = await c.client.rule_detail(rid)
        _LOGGER.info("规则 %s 详情: %s", rid, detail)
        hass.bus.async_fire(f"{DOMAIN}_rule_detail",
                            {"rule_id": rid, "detail": detail})
        return detail

    async def handle_get_log(call: ServiceCall):
        c = _coord(call)
        page = call.data.get("page", 0)
        log = await c.client.logs(page)
        _LOGGER.info("Bridge 日志(第%s页):\n%s", page, log)
        hass.bus.async_fire(f"{DOMAIN}_log", {"page": page, "log": log})
        return log

    async def handle_get_log_all(call: ServiceCall):
        c = _coord(call)
        pages = max(1, min(int(call.data.get("pages", 3)), 20))
        text = await c.client.logs_all(pages)
        hass.bus.async_fire(f"{DOMAIN}_log", {"pages": pages, "log": text})
        return text

    async def handle_delete_rule(call: ServiceCall):
        c = _coord(call)
        await c.client.delete_rule(call.data["rule_id"])
        await c.async_refresh()

    async def handle_set_passcode(call: ServiceCall):
        code = str(call.data.get("passcode", "")).strip()
        c = _coord(call)
        c.pending_passcode = code
        ok = await c.apply_passcode()
        if not ok:
            raise ValueError("登录失败（登录码无效、已过期或 Bridge 不可达）")

    async def handle_get_var(call: ServiceCall):
        c = _coord(call)
        scope = call.data.get("scope", "global")
        var_id = call.data["var_id"]
        value = await c.client.get_var(scope, var_id)
        _LOGGER.info("变量 %s/%s = %s", scope, var_id, value)
        hass.bus.async_fire(f"{DOMAIN}_var_value", {
            "scope": scope, "var_id": var_id, "value": value})
        return value

    async def handle_get_var_scopes(call: ServiceCall):
        c = _coord(call)
        scopes = await c.client.scopes()
        _LOGGER.info("变量作用域: %s", scopes)
        hass.bus.async_fire(f"{DOMAIN}_var_scopes", {"scopes": scopes})
        return scopes

    async def handle_create_var(call: ServiceCall):
        c = _coord(call)
        var_type = call.data.get("var_type", "string")
        raw = call.data.get("value", "")
        if var_type == "number":
            if raw in (None, ""):
                value = 0
            elif isinstance(raw, bool):
                raise ValueError("number 类型变量不能使用布尔值")
            else:
                try:
                    value = float(raw) if "." in str(raw) else int(raw)
                except (TypeError, ValueError):
                    raise ValueError("number 类型变量的初始值必须是数字")
        else:
            value = "" if raw is None else str(raw)
        await c.client.create_var(
            call.data.get("scope", "global"),
            call.data["var_id"],
            var_type,
            value,
            call.data.get("name", ""),
        )
        await c.async_refresh()

    async def handle_delete_var(call: ServiceCall):
        c = _coord(call)
        await c.client.delete_var(
            call.data.get("scope", "global"),
            call.data["var_id"],
        )
        await c.async_refresh()

    async def handle_save_rule(call: ServiceCall):
        c = _coord(call)
        raw = call.data["graph"]
        graph = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(graph, dict):
            raise ValueError("graph 必须是包含 nodes / cfg 的 JSON 对象")
        nodes = graph.get("nodes", [])
        cfg = graph.get("cfg", {})
        if not isinstance(nodes, list):
            raise ValueError("graph.nodes 必须是数组")
        if not isinstance(cfg, dict):
            raise ValueError("graph.cfg 必须是对象")
        gid = call.data["rule_id"]
        cfg["id"] = gid
        await c.client.save_rule(gid, nodes, cfg,
                                 call.data.get("name", ""))
        _LOGGER.info("规则 %s 已保存", gid)
        await c.async_refresh()

    async def handle_set_var(call: ServiceCall):
        c = _coord(call)
        scope = call.data.get("scope", "global")
        var_id = call.data["var_id"]
        info = (c.var_list or {}).get(var_id) if scope == "global" else None
        value = _coerce_var_value(call.data["value"], info)
        await c.client.set_var(scope, var_id, value)
        await c.async_refresh()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, handle_refresh,
                                 schema=vol.Schema({}))
    for svc in (SERVICE_TOGGLE_RULE, SERVICE_SET_ENABLED):
        hass.services.async_register(
            DOMAIN, svc, handle_toggle_rule,
            schema=vol.Schema({
                vol.Required("rule_id"): cv.string,
                vol.Required("enable"): cv.boolean,
            }))
    hass.services.async_register(
        DOMAIN, SERVICE_GET_GRAPH, handle_get_graph,
        schema=vol.Schema({vol.Required("rule_id"): cv.string}),
        supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, SERVICE_GET_LOG, handle_get_log,
        schema=vol.Schema({vol.Optional("page", default=0): cv.positive_int}),
        supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, SERVICE_GET_LOG_ALL, handle_get_log_all,
        schema=vol.Schema({vol.Optional("pages", default=3): cv.positive_int}),
        supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_RULE, handle_delete_rule,
        schema=vol.Schema({vol.Required("rule_id"): cv.string}))
    hass.services.async_register(
        DOMAIN, SERVICE_SET_VAR, handle_set_var,
        schema=vol.Schema({
            vol.Optional("scope", default="global"): cv.string,
            vol.Required("var_id"): cv.string,
            vol.Required("value"): vol.Any(str, int, float),
        }))
    hass.services.async_register(
        DOMAIN, SERVICE_SET_PASSCODE, handle_set_passcode,
        schema=vol.Schema({vol.Required("passcode"): cv.string}))
    hass.services.async_register(
        DOMAIN, SERVICE_GET_VAR, handle_get_var,
        schema=vol.Schema({
            vol.Optional("scope", default="global"): cv.string,
            vol.Required("var_id"): cv.string,
        }),
        supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, SERVICE_GET_VAR_SCOPES, handle_get_var_scopes,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.OPTIONAL)
    hass.services.async_register(
        DOMAIN, SERVICE_CREATE_VAR, handle_create_var,
        schema=vol.Schema({
            vol.Optional("scope", default="global"): cv.string,
            vol.Required("var_id"): cv.string,
            vol.Optional("var_type", default="string"): vol.In(
                ["string", "number"]),
            vol.Optional("value", default=""): vol.Any(str, int, float),
            vol.Optional("name", default=""): cv.string,
        }))
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE_VAR, handle_delete_var,
        schema=vol.Schema({
            vol.Optional("scope", default="global"): cv.string,
            vol.Required("var_id"): cv.string,
        }))
    hass.services.async_register(
        DOMAIN, SERVICE_SAVE_RULE, handle_save_rule,
        schema=vol.Schema({
            vol.Required("rule_id"): cv.string,
            vol.Required("graph"): vol.Any(cv.string, dict),
            vol.Optional("name", default=""): cv.string,
        }))
