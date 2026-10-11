"""Config + reauth flow for Botslab Vacuum.

The user pastes a captured 360 session (Q / T / sid) plus m2 and the app sign
secret; the device (product_key / device_name) is auto-discovered via device_list.
See docs/SESSION-CAPTURE.md in the repo for how to obtain these values.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BotslabApi, BotslabAuthError, BotslabError, BotslabSession
from .const import (
    CONF_DEVICE_NAME, CONF_M2, CONF_PRODUCT_KEY, CONF_Q, CONF_REGION,
    CONF_SID, CONF_SIGN_SECRET, CONF_T, DOMAIN,
)

STEP_USER = vol.Schema({
    vol.Required(CONF_Q): str,
    vol.Required(CONF_T): str,
    vol.Required(CONF_SID): str,
    vol.Required(CONF_M2): str,
    vol.Required(CONF_SIGN_SECRET): str,
    vol.Optional(CONF_REGION, default="na1"): str,
})


class BotslabConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _discover(self, data: dict):
        api = BotslabApi(
            async_get_clientsession(self.hass),
            BotslabSession(
                q=data[CONF_Q], t=data[CONF_T], sid=data[CONF_SID], m2=data[CONF_M2],
                product_key="", device_name="", sign_secret=data[CONF_SIGN_SECRET],
                region=data.get(CONF_REGION, "na1"),
            ),
        )
        return await api.device_list()

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                devices = await self._discover(user_input)
            except BotslabAuthError:
                errors["base"] = "invalid_auth"
            except BotslabError:
                errors["base"] = "cannot_connect"
            else:
                if not devices:
                    errors["base"] = "no_devices"
                else:
                    dev = devices[0]
                    data = {
                        **user_input,
                        CONF_PRODUCT_KEY: dev["product_key"],
                        CONF_DEVICE_NAME: dev["device_name"],
                    }
                    await self.async_set_unique_id(dev["device_name"])
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=dev.get("device_title", "Botslab Vacuum"), data=data
                    )
        return self.async_show_form(step_id="user", data_schema=STEP_USER, errors=errors)

    async def async_step_reauth(self, entry_data: dict) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            merged = {**entry.data, **user_input}
            try:
                devices = await self._discover(merged)
            except BotslabAuthError:
                errors["base"] = "invalid_auth"
            except BotslabError:
                errors["base"] = "cannot_connect"
            else:
                if devices:
                    return self.async_update_reload_and_abort(entry, data=merged)
                errors["base"] = "no_devices"
        schema = vol.Schema({
            vol.Required(CONF_Q): str,
            vol.Required(CONF_T): str,
            vol.Required(CONF_SID): str,
        })
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors)
