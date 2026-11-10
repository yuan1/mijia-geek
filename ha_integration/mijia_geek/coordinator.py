"""Bridge 数据协调器。"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)

from .bridge import BridgeAuthError, BridgeClient, BridgeError, BridgeUnavailable
from .const import DOMAIN, EVENT_PUSH, SIGNAL_NEW_RULE

_LOGGER = logging.getLogger(__name__)


class MijiaGeekCoordinator(DataUpdateCoordinator):
    """轮询 Bridge REST，并用 SSE 接收推送。"""

    def __init__(self, hass: HomeAssistant, client: BridgeClient,
                 bridge_url: str, interval: int,
                 entry=None, entry_id: str = "") -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=timedelta(seconds=interval),
            config_entry=entry,
        )
        self.client = client
        self.bridge_url = bridge_url
        self.entry_id = entry_id or (entry.entry_id if entry is not None else "")
        self.devices: dict = {}
        self.rules: list = []
        self.var_list: dict = {}
        self.last_log: str = ""
        self.connected: bool = False
        self.auth_failed: bool = False
        self.last_error: str = ""
        self.has_passcode: bool = False
        self.last_push: dict = {}
        self.push_count: int = 0
        self.known_rule_ids: set[str] = set()
        self.pending_passcode: str = ""

    async def _async_update_data(self):
        try:
            status = await self.client.status()
        except BridgeAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except BridgeUnavailable as err:
            raise UpdateFailed(str(err)) from err
        except BridgeError as err:
            raise UpdateFailed(str(err)) from err

        self.last_log = status.get("last_log") or ""
        self.connected = bool(status.get("connected"))
        self.auth_failed = False
        self.last_error = status.get("last_error") or ""
        self.has_passcode = bool(status.get("has_passcode"))
        if isinstance(status.get("last_push"), dict):
            self.last_push = status["last_push"]
        self.push_count = int(status.get("push_seq") or 0)

        if self.connected:
            try:
                devices, rules = await asyncio.gather(
                    self.client.devices(),
                    self.client.rules(),
                )
            except BridgeAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except BridgeUnavailable:
                # 状态查询和明细查询之间连接刚好断开：保留上次数据，
                # 让实体仍然创建/存在，用户可重新输入登录码。
                self.connected = False
                devices, rules = self.devices, self.rules
            except BridgeError as err:
                raise UpdateFailed(str(err)) from err

            try:
                variables = await self.client.variables("global")
            except BridgeError:
                variables = self.var_list
        else:
            devices, rules, variables = (
                self.devices, self.rules, self.var_list)

        self.devices = devices or {}
        self.rules = rules or []
        self.var_list = variables or {}

        current_ids = {str(r.get("id")) for r in self.rules if r.get("id")}
        if self.connected and current_ids - self.known_rule_ids:
            # switch 读取 coordinator.rules，此时已经赋值，不再依赖 coordinator.data
            async_dispatcher_send(self.hass, SIGNAL_NEW_RULE)
        self.known_rule_ids = current_ids

        return {
            "devices": self.devices,
            "rules": self.rules,
            "device_count": len(self.devices),
            "online_count": sum(
                1 for d in self.devices.values() if d.get("online")),
            "rule_count": len(self.rules),
            "enabled_count": sum(
                1 for r in self.rules if r.get("enabled")),
            "last_log": self.last_log,
            "var_count": len(self.var_list),
            "push_count": self.push_count,
            "connected": self.connected,
            "auth_failed": self.auth_failed,
            "last_error": self.last_error,
            "has_passcode": self.has_passcode,
        }

    def handle_event(self, event: dict) -> None:
        """处理 SSE 事件。"""
        kind = event.get("kind")
        if kind == "ready":
            previous = self.connected
            self.connected = bool(event.get("connected", self.connected))
            if self.connected != previous:
                self.async_update_listeners()
                self.hass.async_create_task(self.async_refresh())
            return
        if kind == "connection":
            self.connected = bool(event.get("connected"))
            self.auth_failed = False
            self.last_error = event.get("last_error") or ""
            self.async_update_listeners()
            self.hass.async_create_task(self.async_refresh())
            return

        self.last_push = event
        self.push_count = max(self.push_count, int(event.get("seq") or 0))
        self.hass.bus.async_fire(EVENT_PUSH, event)
        self.async_update_listeners()

    async def apply_passcode(self) -> bool:
        """把新登录码发给 Bridge；登录码只存在 Bridge 内存。"""
        code = (self.pending_passcode or "").strip()
        if len(code) != 6 or not code.isdigit():
            _LOGGER.error("登录码无效：需要 6 位数字，当前 %s 位", len(code))
            return False

        try:
            await self.client.connect(code)
        except BridgeError as err:
            self.auth_failed = True
            self.connected = False
            self.last_error = str(err)
            _LOGGER.error("登录码应用失败: %s", err)
            return False

        self.pending_passcode = ""
        self.auth_failed = False
        await self.async_refresh()
        return True

    async def async_set_rule_enabled(self, rule_id: str, enable: bool) -> None:
        await self.client.toggle_rule(rule_id, enable)
        await self.async_refresh()

    async def async_refresh_bridge(self) -> None:
        await self.client.refresh()
        await self.async_refresh()
