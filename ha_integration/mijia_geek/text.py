"""米家极客版 —— 登录码输入框实体。

登录码只写入 coordinator 内存，随后由 coordinator 发给 Bridge；
不会进入实体状态/recorder，日志只打印长度。
"""
from __future__ import annotations

import logging

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MijiaGeekCoordinator
from .entity import MijiaGeekEntity

_LOGGER = logging.getLogger(__name__)

_MASK = "•"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MijiaGeekCoordinator = (
        hass.data[DOMAIN][entry.entry_id]["coordinator"])
    async_add_entities([PasscodeText(coordinator)])


class PasscodeText(MijiaGeekEntity, TextEntity):
    """输入 6 位登录码，再由 Bridge 连接网关。"""

    _attr_name = "登录码"
    _attr_icon = "mdi:dialpad"
    _attr_native_min = 0
    _attr_native_max = 6
    _attr_mode = TextMode.PASSWORD
    _attr_should_poll = False

    def __init__(self, coordinator: MijiaGeekCoordinator):
        super().__init__(coordinator, "passcode_input")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        # 只回显掩码，绝不把真实登录码写进状态机
        return _MASK * len(getattr(self.coordinator, "pending_passcode", "") or "")

    @property
    def extra_state_attributes(self):
        return {
            "has_pending_code": bool(
                getattr(self.coordinator, "pending_passcode", "") or ""),
            "提示": "输入 6 位码后点「应用登录码」。登录码一次性，不会保存。",
            "bridge_url": self.coordinator.bridge_url,
        }

    async def async_set_value(self, value: str) -> None:
        digits = "".join(ch for ch in (value or "") if ch.isdigit())[:6]
        self.coordinator.pending_passcode = digits
        self.async_write_ha_state()
        _LOGGER.info("登录码已写入内存（%s 位）", len(digits))
