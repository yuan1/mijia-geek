"""米家极客版 —— 传感器实体。"""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
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
    coordinator: MijiaGeekCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities([
        DeviceCountSensor(coordinator),
        OnlineCountSensor(coordinator),
        RuleCountSensor(coordinator),
        EnabledCountSensor(coordinator),
        VarCountSensor(coordinator),
        LastLogSensor(coordinator),
        PushCountSensor(coordinator),
        LastPushSensor(coordinator),
    ])


class MijiaGeekSensor(MijiaGeekEntity, SensorEntity):
    def __init__(self, coordinator: MijiaGeekCoordinator, key: str) -> None:
        super().__init__(coordinator, key)


class DeviceCountSensor(MijiaGeekSensor):
    _attr_name = "设备总数"
    _attr_icon = "mdi:devices"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator):
        super().__init__(coordinator, "device_count")

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("device_count", 0)

    @property
    def extra_state_attributes(self):
        devs = (self.coordinator.data or {}).get("devices", {})
        online = [(d.get("name"), d.get("roomName"))
                  for d in devs.values() if d.get("online")]
        offline = [(d.get("name"), d.get("roomName"))
                   for d in devs.values() if not d.get("online")]
        return {
            "online_devices": [f"{n}[{r}]" if r else n for n, r in online[:40]],
            "offline_devices": [f"{n}[{r}]" if r else n for n, r in offline[:40]],
            "online_count": len(online),
            "offline_count": len(offline),
        }


class OnlineCountSensor(MijiaGeekSensor):
    _attr_name = "在线设备数"
    _attr_icon = "mdi:lan-connect"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator):
        super().__init__(coordinator, "online_count")

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("online_count", 0)


class RuleCountSensor(MijiaGeekSensor):
    _attr_name = "自动化规则数"
    _attr_icon = "mdi:sitemap"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator):
        super().__init__(coordinator, "rule_count")

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("rule_count", 0)

    @property
    def extra_state_attributes(self):
        rules = (self.coordinator.data or {}).get("rules", [])
        return {
            "rules": [
                {"id": r.get("id"), "name": r.get("name"),
                 "enabled": r.get("enabled")}
                for r in rules[:60]
            ]
        }


class EnabledCountSensor(MijiaGeekSensor):
    _attr_name = "启用规则数"
    _attr_icon = "mdi:play-circle-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator):
        super().__init__(coordinator, "enabled_count")

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("enabled_count", 0)


class VarCountSensor(MijiaGeekSensor):
    _attr_name = "变量数量"
    _attr_icon = "mdi:variable"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator):
        super().__init__(coordinator, "var_count")

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get("var_count", 0)

    @property
    def extra_state_attributes(self):
        variables = getattr(self.coordinator, "var_list", {}) or {}
        out = {}
        for key, value in list(variables.items())[:40]:
            if isinstance(value, dict):
                out[key] = {
                    "name": (value.get("userData") or {}).get("name", key),
                    "type": value.get("type"),
                    "value": value.get("value"),
                }
        return {"variables": out}


class LastLogSensor(MijiaGeekSensor):
    _attr_name = "最近执行日志"
    _attr_icon = "mdi:text-box-outline"

    def __init__(self, coordinator):
        super().__init__(coordinator, "last_log")

    @property
    def native_value(self):
        value = (self.coordinator.data or {}).get("last_log", "")
        return value[:250] if value else "暂无日志"


class PushCountSensor(MijiaGeekSensor):
    _attr_name = "推送消息数"
    _attr_icon = "mdi:message-flash"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator):
        super().__init__(coordinator, "push_count")

    @property
    def native_value(self):
        return getattr(self.coordinator, "push_count", 0)


class LastPushSensor(MijiaGeekSensor):
    _attr_name = "最近推送"
    _attr_icon = "mdi:message-text-clock"

    def __init__(self, coordinator):
        super().__init__(coordinator, "last_push")

    @property
    def native_value(self):
        last_push = getattr(self.coordinator, "last_push", {}) or {}
        summary = last_push.get("summary") or ""
        return summary[:250] if summary else "暂无推送"

    @property
    def extra_state_attributes(self):
        last_push = getattr(self.coordinator, "last_push", {}) or {}
        return {
            "kind": last_push.get("kind"),
            "device_name": last_push.get("device_name"),
            "device_id": last_push.get("device_id"),
            "room": last_push.get("room"),
            "model": last_push.get("model"),
            "siid": last_push.get("siid"),
            "piid": last_push.get("piid"),
            "eiid": last_push.get("eiid"),
            "value": last_push.get("value"),
        }
