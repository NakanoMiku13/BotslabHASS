"""DataUpdateCoordinator for Botslab Vacuum."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BotslabApi, BotslabAuthError, BotslabError, BotslabSession
from .const import (
    CONF_DEVICE_NAME, CONF_M2, CONF_PRODUCT_KEY, CONF_Q, CONF_REGION,
    CONF_SID, CONF_SIGN_SECRET, CONF_T, DEFAULT_SCAN_INTERVAL, DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class BotslabCoordinator(DataUpdateCoordinator[dict]):
    """Polls the device shadow and exposes parsed state (identifier -> value)."""

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
                sign_secret=d[CONF_SIGN_SECRET], region=d.get(CONF_REGION, "na1"),
            ),
        )
        self.device_title = entry.title or d.get(CONF_DEVICE_NAME, "Botslab")

    async def _async_update_data(self) -> dict:
        try:
            state = await self.api.get_property()
            if not state:
                # fall back to the desired/report shadow
                state = await self.api.get_desired_property()
            return state
        except BotslabAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except BotslabError as err:
            raise UpdateFailed(str(err)) from err
