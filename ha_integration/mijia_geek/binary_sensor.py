"""米家极客版 —— 连接状态 binary_sensor。"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MijiaGeekCoordinator
from .entity import MijiaGeekEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MijiaGeekCoordinator = (
        hass.data[DOMAIN][entry.entry_id]["coordinator"])
    async_add_entities([ConnectedBinary(coordinator)])


class ConnectedBinary(MijiaGeekEntity, BinarySensorEntity):
    _attr_name = "网关连接"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: MijiaGeekCoordinator):
        super().__init__(coordinator, "connected")

    @property
    def available(self) -> bool:
        # Bridge 不可用时也应显示 off，而不是 unavailable
        return True

    @property
    def is_on(self):
        return bool(self.coordinator.connected)

    @property
    def extra_state_attributes(self):
        return {
            "auth_failed": bool(getattr(self.coordinator, "auth_failed", False)),
            "has_pending_code": bool(
                getattr(self.coordinator, "pending_passcode", "") or ""),
            "last_error": self.coordinator.last_error,
            "bridge_url": self.coordinator.bridge_url,
        }
