"""米家极客版 —— 按钮实体。"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MijiaGeekCoordinator
from .entity import MijiaGeekEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MijiaGeekCoordinator = (
        hass.data[DOMAIN][entry.entry_id]["coordinator"])
    async_add_entities([
        RefreshButton(coordinator),
        ApplyPasscodeButton(coordinator),
    ])


class RefreshButton(MijiaGeekEntity, ButtonEntity):
    _attr_name = "刷新数据"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: MijiaGeekCoordinator):
        super().__init__(coordinator, "btn_refresh")

    async def async_press(self) -> None:
        _LOGGER.info("手动刷新米家极客版数据")
        await self.coordinator.async_refresh_bridge()


class ApplyPasscodeButton(MijiaGeekEntity, ButtonEntity):
    """把「登录码」输入框里的码发给 Bridge 重连网关。"""

    _attr_name = "应用登录码"
    _attr_icon = "mdi:key-check"

    def __init__(self, coordinator: MijiaGeekCoordinator):
        super().__init__(coordinator, "btn_apply_passcode")

    async def async_press(self) -> None:
        await self.coordinator.apply_passcode()

    @property
    def extra_state_attributes(self):
        return {
            "提示": "先在「登录码」输入框填入 6 位码，再点本按钮",
            "当前状态": (
                "已连接" if self.coordinator.connected
                else "未连接（需登录码）"
            ),
            "bridge_url": self.coordinator.bridge_url,
        }
