"""米家中枢网关极客版 —— Home Assistant 集成（Bridge REST 客户端）。

本集成不再直接连接网关：
  · 网关长连接、登录码、协议握手全部由 mijia-geek-bridge 负责；
  · HA 只轮询 Bridge REST，并通过 SSE 接收推送；
  · HA 重启 / 重载不会消耗新的登录码。
"""
from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.issue_registry import IssueSeverity
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .bridge import BridgeAuthError, BridgeClient
from .const import (
    CONF_API_TOKEN,
    CONF_BRIDGE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import MijiaGeekCoordinator
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON,
             Platform.SWITCH, Platform.TEXT]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v2（直连网关）→ v3（Bridge 客户端）。

    旧配置里只有网关地址，无法推断 Bridge 地址，因此迁移后需要用户
    通过“重新配置”填写 Bridge URL 与 Token。
    """
    if entry.version < 3:
        hass.config_entries.async_update_entry(
            entry,
            version=3,
            data={CONF_BRIDGE_URL: "", CONF_API_TOKEN: ""},
        )
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"migrate_{entry.entry_id}",
            is_fixable=False,
            severity=IssueSeverity.WARNING,
            translation_key="migrate_to_bridge",
        )
        _LOGGER.warning(
            "米家极客版配置已迁移为 Bridge 客户端；"
            "请通过“重新配置”填写 Bridge 地址与 API Token"
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    bridge_url = str(entry.data.get(CONF_BRIDGE_URL, "")).strip().rstrip("/")
    if not bridge_url:
        raise ConfigEntryError(
            "缺少 Bridge 地址，请在集成选项中重新配置")
    token = str(entry.data.get(CONF_API_TOKEN, "")).strip()
    interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    ir.async_delete_issue(hass, DOMAIN, f"migrate_{entry.entry_id}")
    client = BridgeClient(async_get_clientsession(hass), bridge_url, token)
    coordinator = MijiaGeekCoordinator(
        hass, client, bridge_url, interval,
        entry=entry, entry_id=entry.entry_id,
    )

    # Bridge 不可达时让 HA 自动重试；Token 错误时触发 reauth
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
        "bridge_url": bridge_url,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)

    task = hass.async_create_task(_event_loop(hass, entry, coordinator))
    entry.async_on_unload(task.cancel)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """选项/重新认证/重新配置后重载。"""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if not any(isinstance(v, dict) and v.get("coordinator")
                   for v in hass.data.get(DOMAIN, {}).values()):
            async_unregister_services(hass)
    return unload_ok


async def _event_loop(hass: HomeAssistant, entry: ConfigEntry,
                      coordinator: MijiaGeekCoordinator) -> None:
    """订阅 Bridge SSE；断开后自动重连。"""
    while True:
        try:
            async for event in coordinator.client.events():
                coordinator.handle_event(event)
        except asyncio.CancelledError:
            raise
        except BridgeAuthError:
            _LOGGER.error("Bridge API Token 失效，触发重新认证")
            entry.async_start_reauth(hass)
            return
        except Exception as err:
            _LOGGER.warning("Bridge 事件流断开，5 秒后重连: %s", err)
        else:
            _LOGGER.info("Bridge 事件流已结束，5 秒后重连")
        await asyncio.sleep(5)
