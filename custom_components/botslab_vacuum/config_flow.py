"""Config + reauth flow for Botslab Vacuum.

The user enters their Botslab/360 account email + password. The integration
bootstraps the full session (Q / T / sid) headlessly via :mod:`.auth` and
auto-discovers the device. On expiry the coordinator re-logs-in automatically;
this flow's reauth step is the fallback when credentials themselves change.
"""
from __future__ import annotations

import uuid

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import BotslabApi, BotslabError, BotslabSession
from .auth import CaptchaRequired, LoginError, async_login
from .const import (
    CONF_DEVICE_NAME, CONF_EMAIL, CONF_M2, CONF_PASSWORD, CONF_PRODUCT_KEY,
    CONF_Q, CONF_REGION, CONF_SID, CONF_SIGN_SECRET, CONF_T,
    DEFAULT_REGION, DEFAULT_SIGN_SECRET, DOMAIN,
)

STEP_USER = vol.Schema({
    vol.Required(CONF_EMAIL): str,
    vol.Required(CONF_PASSWORD): str,
    vol.Optional(CONF_REGION, default=DEFAULT_REGION): str,
})


class BotslabConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _bootstrap(self, email: str, password: str, region: str, m2: str) -> dict:
        """Log in, discover the device, return the full config entry data dict."""
        session = async_get_clientsession(self.hass)
        tokens = await async_login(
            session, email, password, m2=m2, region=region,
            sign_secret=DEFAULT_SIGN_SECRET,
        )
        api = BotslabApi(session, BotslabSession(
            q=tokens["q"], t=tokens["t"], sid=tokens["sid"], m2=m2,
            product_key="", device_name="", sign_secret=DEFAULT_SIGN_SECRET, region=region,
        ))
        devices = await api.device_list()
        if not devices:
            raise BotslabError("no devices on account")
        dev = devices[0]
        return {
            CONF_EMAIL: email, CONF_PASSWORD: password, CONF_REGION: region, CONF_M2: m2,
            CONF_SIGN_SECRET: DEFAULT_SIGN_SECRET,
            CONF_Q: tokens["q"], CONF_T: tokens["t"], CONF_SID: tokens["sid"],
            CONF_PRODUCT_KEY: dev["product_key"], CONF_DEVICE_NAME: dev["device_name"],
            "_device_title": dev.get("device_title", "Botslab Vacuum"),
        }

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                data = await self._bootstrap(
                    user_input[CONF_EMAIL], user_input[CONF_PASSWORD],
                    user_input.get(CONF_REGION, DEFAULT_REGION), str(uuid.uuid4()),
                )
            except CaptchaRequired:
                errors["base"] = "captcha"
            except LoginError:
                errors["base"] = "invalid_auth"
            except BotslabError:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(data[CONF_DEVICE_NAME])
                self._abort_if_unique_id_configured()
                title = data.pop("_device_title")
                return self.async_create_entry(title=title, data=data)
        return self.async_show_form(step_id="user", data_schema=STEP_USER, errors=errors)

    async def async_step_reauth(self, entry_data: dict) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            m2 = entry.data.get(CONF_M2) or str(uuid.uuid4())
            region = entry.data.get(CONF_REGION, DEFAULT_REGION)
            try:
                data = await self._bootstrap(
                    user_input[CONF_EMAIL], user_input[CONF_PASSWORD], region, m2,
                )
            except CaptchaRequired:
                errors["base"] = "captcha"
            except LoginError:
                errors["base"] = "invalid_auth"
            except BotslabError:
                errors["base"] = "cannot_connect"
            else:
                data.pop("_device_title", None)
                return self.async_update_reload_and_abort(entry, data={**entry.data, **data})
        schema = vol.Schema({
            vol.Required(CONF_EMAIL, default=entry.data.get(CONF_EMAIL, "")): str,
            vol.Required(CONF_PASSWORD): str,
        })
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors)
