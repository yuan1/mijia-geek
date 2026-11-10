"""配置流程：连接 mijia-geek-bridge。"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .bridge import BridgeAuthError, BridgeClient, BridgeError
from .const import (
    CONF_API_TOKEN,
    CONF_BRIDGE_URL,
    CONF_SCAN_INTERVAL,
    DEFAULT_BRIDGE_URL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    NAME,
)

_LOGGER = logging.getLogger(__name__)


async def _validate(hass, url: str, token: str) -> str | None:
    """返回错误 key，成功返回 None。"""
    if not url.startswith(("http://", "https://")):
        return "invalid_url"
    client = BridgeClient(async_get_clientsession(hass), url, token)
    try:
        await client.status()
    except BridgeAuthError:
        return "invalid_auth"
    except BridgeError:
        return "cannot_connect"
    return None


class MijiaGeekConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """首次配置 / 重新认证 / 重新配置。"""

    VERSION = 3

    def __init__(self) -> None:
        self._entry: config_entries.ConfigEntry | None = None

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            url = str(user_input[CONF_BRIDGE_URL]).strip().rstrip("/")
            token = str(user_input.get(CONF_API_TOKEN, "")).strip()

            error = await _validate(self.hass, url, token)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(f"{DOMAIN}_{url}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{NAME} ({url})",
                    data={CONF_BRIDGE_URL: url, CONF_API_TOKEN: token},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_BRIDGE_URL, default=DEFAULT_BRIDGE_URL): str,
                vol.Optional(CONF_API_TOKEN, default=""): str,
            }),
            errors=errors,
        )

    # ---------- 重新认证（Token 失效） ----------
    async def async_step_reauth(self, entry_data: dict | None = None) -> FlowResult:
        entry_id = (self.context or {}).get("entry_id")
        if entry_id:
            self._entry = self.hass.config_entries.async_get_entry(entry_id)
        if self._entry is None:
            return self.async_abort(reason="unknown_entry")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None) -> FlowResult:
        entry = self._entry
        if entry is None:
            return self.async_abort(reason="unknown_entry")
        errors: dict[str, str] = {}
        url = entry.data[CONF_BRIDGE_URL]

        if user_input is not None:
            token = str(user_input.get(CONF_API_TOKEN, "")).strip()
            error = await _validate(self.hass, url, token)
            if error:
                errors["base"] = error
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_API_TOKEN: token},
                )
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({
                vol.Optional(CONF_API_TOKEN, default=""): str,
            }),
            description_placeholders={"url": url},
            errors=errors,
        )

    # ---------- 重新配置（地址/Token 变更） ----------
    def _reconfigure_entry(self):
        getter = getattr(self, "_get_reconfigure_entry", None)
        if getter is not None:
            return getter()
        entry_id = (self.context or {}).get("entry_id")
        return self.hass.config_entries.async_get_entry(entry_id) \
            if entry_id else None

    async def async_step_reconfigure(self, user_input=None) -> FlowResult:
        entry = self._reconfigure_entry()
        if entry is None:
            return self.async_abort(reason="unknown_entry")
        self._entry = entry
        errors: dict[str, str] = {}

        if user_input is not None:
            url = str(user_input[CONF_BRIDGE_URL]).strip().rstrip("/")
            token = str(user_input.get(CONF_API_TOKEN, "")).strip()
            error = await _validate(self.hass, url, token)
            if error:
                errors["base"] = error
            else:
                unique_id = f"{DOMAIN}_{url}"
                duplicate = any(
                    other.entry_id != entry.entry_id
                    and other.unique_id == unique_id
                    for other in self.hass.config_entries.async_entries(DOMAIN)
                )
                if duplicate:
                    errors["base"] = "already_configured"
                else:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        unique_id=unique_id,
                        data={CONF_BRIDGE_URL: url, CONF_API_TOKEN: token},
                    )
                    return self.async_abort(reason="reconfigure_successful")

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema({
                vol.Required(CONF_BRIDGE_URL,
                             default=entry.data.get(CONF_BRIDGE_URL,
                                                    DEFAULT_BRIDGE_URL)): str,
                vol.Optional(CONF_API_TOKEN,
                             default=entry.data.get(CONF_API_TOKEN, "")): str,
            }),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return MijiaGeekOptionsFlow(config_entry)


class MijiaGeekOptionsFlow(config_entries.OptionsFlow):
    """选项：扫描间隔。"""

    def __init__(self, entry=None) -> None:
        self._entry = entry

    def _get_entry(self):
        entry = getattr(self, "config_entry", None)
        return entry or self._entry

    async def async_step_init(self, user_input=None) -> FlowResult:
        entry = self._get_entry()
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = DEFAULT_SCAN_INTERVAL
        if entry is not None:
            current = entry.options.get(CONF_SCAN_INTERVAL,
                                        DEFAULT_SCAN_INTERVAL)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_SCAN_INTERVAL, default=current): vol.All(
                    int, vol.Range(min=15, max=3600)),
            }),
        )
