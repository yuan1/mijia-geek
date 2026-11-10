"""HA 实体基类。"""
from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MijiaGeekCoordinator


class MijiaGeekEntity(CoordinatorEntity):
    """所有实体共享的设备信息与 unique_id 规则。"""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MijiaGeekCoordinator, key: str) -> None:
        super().__init__(coordinator)
        identity = coordinator.entry_id or coordinator.bridge_url
        self._identity = identity
        self._attr_unique_id = f"mijia_geek_{identity}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        url = self.coordinator.bridge_url
        return DeviceInfo(
            identifiers={(DOMAIN, self._identity)},
            name=f"米家极客版 Bridge（{url}）",
            manufacturer="Mijia Geek Bridge",
            model="REST bridge",
            configuration_url=url,
        )
