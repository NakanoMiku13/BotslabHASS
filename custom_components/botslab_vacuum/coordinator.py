"""DataUpdateCoordinator for Botslab Vacuum (with automatic re-login)."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BotslabApi, BotslabAuthError, BotslabError, BotslabSession
from .auth import LoginError, async_login
from .const import (
    CONF_DEVICE_NAME, CONF_EMAIL, CONF_M2, CONF_PASSWORD, CONF_PRODUCT_KEY,
    CONF_Q, CONF_REGION, CONF_SID, CONF_SIGN_SECRET, CONF_T,
    DEFAULT_REGION, DEFAULT_SCAN_INTERVAL, DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class BotslabCoordinator(DataUpdateCoordinator[dict]):
    """Polls the device shadow; re-logs-in automatically when the session dies."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.entry = entry
        d = entry.data
        self.api = BotslabApi(
            async_get_clientsession(hass),
            BotslabSession(
                q=d[CONF_Q], t=d[CONF_T], sid=d[CONF_SID], m2=d[CONF_M2],
                product_key=d[CONF_PRODUCT_KEY], device_name=d[CONF_DEVICE_NAME],
                sign_secret=d[CONF_SIGN_SECRET], region=d.get(CONF_REGION, DEFAULT_REGION),
            ),
        )
        self.device_title = entry.title or d.get(CONF_DEVICE_NAME, "Botslab")
        self.map_coordinator = BotslabMapCoordinator(hass, self)

    async def _relogin(self) -> bool:
        """Refresh Q/T/sid from the stored email+password. Returns True on success."""
        d = self.entry.data
        email, password = d.get(CONF_EMAIL), d.get(CONF_PASSWORD)
        if not (email and password):
            return False  # legacy entry without credentials -> manual reauth
        try:
            tokens = await async_login(
                async_get_clientsession(self.hass), email, password,
                m2=d[CONF_M2], region=d.get(CONF_REGION, DEFAULT_REGION),
                sign_secret=d[CONF_SIGN_SECRET],
            )
        except (LoginError, BotslabError) as err:
            _LOGGER.warning("Botslab auto re-login failed: %s", err)
            return False
        # persist + apply the fresh session in place (keeps api references valid)
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={**d, CONF_Q: tokens["q"], CONF_T: tokens["t"], CONF_SID: tokens["sid"]},
        )
        self.api._cfg.q = tokens["q"]
        self.api._cfg.t = tokens["t"]
        self.api._cfg.sid = tokens["sid"]
        _LOGGER.info("Botslab session refreshed via auto re-login")
        return True

    async def _fetch(self) -> dict:
        state = await self.api.get_property()
        if not state:
            state = await self.api.get_desired_property()
        return state

    async def _async_update_data(self) -> dict:
        try:
            return await self._fetch()
        except BotslabAuthError as err:
            if await self._relogin():
                try:
                    return await self._fetch()
                except BotslabAuthError as err2:
                    raise ConfigEntryAuthFailed(str(err2)) from err2
                except BotslabError as err2:
                    raise UpdateFailed(str(err2)) from err2
            raise ConfigEntryAuthFailed(str(err)) from err
        except BotslabError as err:
            raise UpdateFailed(str(err)) from err


class BotslabMapCoordinator(DataUpdateCoordinator):
    """Fetches the map package, renders it to a PNG (cached by upload tag)."""

    def __init__(self, hass: HomeAssistant, main: "BotslabCoordinator") -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_map",
            update_interval=timedelta(seconds=60),
        )
        self.main = main
        self._tag: str | None = None
        self._png: bytes | None = None

    async def _fetch_bin(self):
        try:
            return await self.main.api.get_map_bin()
        except BotslabAuthError:
            if await self.main._relogin():
                return await self.main.api.get_map_bin()
            raise

    async def _async_update_data(self) -> bytes | None:
        try:
            text, tag = await self._fetch_bin()
        except BotslabAuthError as err:
            raise UpdateFailed(str(err)) from err
        except BotslabError as err:
            raise UpdateFailed(str(err)) from err
        if not text:
            return self._png
        if tag and tag == self._tag and self._png:
            return self._png
        try:
            from . import map_render
            png = await self.hass.async_add_executor_job(map_render.render_png, text)
            self._png, self._tag = png, tag
        except Exception as err:  # noqa: BLE001 - rendering must never break polling
            _LOGGER.warning("Botslab map render failed: %s", err)
        return self._png
