"""米家极客版 —— 规则开关实体。"""
from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_NEW_RULE
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
    tracked: dict[str, RuleSwitch] = {}

    @callback
    def _sync_rules() -> None:
        rules = coordinator.rules or []
        new_entities = []
        for rule in rules:
            rule_id = str(rule.get("id") or "")
            if not rule_id or rule_id in tracked:
                continue
            entity = RuleSwitch(coordinator, rule_id)
            tracked[rule_id] = entity
            new_entities.append(entity)
        if new_entities:
            _LOGGER.info("新增 %s 个规则开关实体", len(new_entities))
            async_add_entities(new_entities)

    _sync_rules()
    entry.async_on_unload(
        async_dispatcher_connect(hass, SIGNAL_NEW_RULE, _sync_rules)
    )


class RuleSwitch(MijiaGeekEntity, SwitchEntity):
    """单条自动化规则的启停开关。"""

    _attr_has_entity_name = False
    _attr_icon = "mdi:sitemap"

    def __init__(self, coordinator: MijiaGeekCoordinator, rule_id: str):
        super().__init__(coordinator, f"rule_{rule_id}")
        self._rule_id = rule_id
        self._optimistic: bool | None = None

    @property
    def _rule(self) -> dict | None:
        for rule in (self.coordinator.rules or []):
            if str(rule.get("id")) == self._rule_id:
                return rule
        return None

    @property
    def name(self) -> str:
        rule = self._rule
        name = (rule or {}).get("name") or f"规则 {self._rule_id[-6:]}"
        return name

    @property
    def available(self) -> bool:
        return self.coordinator.connected and self._rule is not None

    @property
    def is_on(self) -> bool:
        if self._optimistic is not None:
            return self._optimistic
        rule = self._rule
        return bool((rule or {}).get("enabled"))

    async def async_turn_on(self, **kwargs) -> None:
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set_enabled(False)

    async def _set_enabled(self, enable: bool) -> None:
        self._optimistic = enable
        self.async_write_ha_state()
        try:
            await self.coordinator.async_set_rule_enabled(self._rule_id, enable)
            _LOGGER.info("规则 %s 已%s", self.name,
                         "启用" if enable else "停用")
        except Exception as err:
            _LOGGER.error("切换规则 %s 失败: %s", self._rule_id, err)
            await self.coordinator.async_refresh()
        finally:
            self._optimistic = None
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        rule = self._rule or {}
        return {
            "rule_id": self._rule_id,
            "update_time": rule.get("updateTime"),
            "create_time": rule.get("createTime"),
        }
