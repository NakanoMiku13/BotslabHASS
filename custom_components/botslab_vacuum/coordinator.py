"""DataUpdateCoordinator for Botslab Vacuum (auto re-login + live map)."""
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
    ATTR_STATUS, ATTR_STATUS_TEST, CONF_DEVICE_NAME, CONF_EMAIL, CONF_M2,
    CONF_PASSWORD, CONF_PRODUCT_KEY, CONF_Q, CONF_REGION, CONF_SID,
    CONF_SIGN_SECRET, CONF_T, DEFAULT_REGION, DEFAULT_SCAN_INTERVAL, DOMAIN,
    WORKING_STATUS,
)

_LOGGER = logging.getLogger(__name__)

_CLEANING_INTERVAL = 10
_IDLE_INTERVAL = DEFAULT_SCAN_INTERVAL


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
        self.robot_pose = None           # [x_mm, y_mm, angle_raw]
        self.robot_path: list | None = None
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

    def _is_cleaning(self, state: dict) -> bool:
        raw = state.get(ATTR_STATUS, state.get(ATTR_STATUS_TEST))
        try:
            return WORKING_STATUS.get(int(raw)) == "cleaning"
        except (TypeError, ValueError):
            return False

    async def _postprocess(self, state: dict) -> None:
        """Extract live robot pose + path and adapt the poll cadence."""
        loc = state.get("CurrentLocation")
        if isinstance(loc, str):
            try:
                import json
                loc = json.loads(loc)
            except ValueError:
                loc = None
        if isinstance(loc, (list, tuple)) and len(loc) >= 2:
            self.robot_pose = list(loc)

        cleaning = self._is_cleaning(state)
        want = _CLEANING_INTERVAL if cleaning else _IDLE_INTERVAL
        if self.update_interval != timedelta(seconds=want):
            self.update_interval = timedelta(seconds=want)
        if cleaning:
            try:
                path = await self.api.get_clean_track_all()
                if path:
                    self.robot_path = path
            except BotslabError as err:
                _LOGGER.debug("Botslab track fetch failed: %s", err)
        # let the map/camera re-render with the fresh pose
        self.map_coordinator.async_update_listeners()

    async def _async_update_data(self) -> dict:
        try:
            state = await self._fetch()
        except BotslabAuthError as err:
            if await self._relogin():
                try:
                    state = await self._fetch()
                except BotslabAuthError as err2:
                    raise ConfigEntryAuthFailed(str(err2)) from err2
                except BotslabError as err2:
                    raise UpdateFailed(str(err2)) from err2
            else:
                raise ConfigEntryAuthFailed(str(err)) from err
        except BotslabError as err:
            raise UpdateFailed(str(err)) from err
        try:
            await self._postprocess(state)
        except Exception as err:  # noqa: BLE001 - never break polling on overlay prep
            _LOGGER.debug("Botslab postprocess failed: %s", err)
        return state


class BotslabMapCoordinator(DataUpdateCoordinator):
    """Fetches + renders the map base (cached by upload tag); composes live PNG."""

    def __init__(self, hass: HomeAssistant, main: "BotslabCoordinator") -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_map",
            update_interval=timedelta(seconds=60),
        )
        self.main = main
        self._tag: str | None = None
        self.rendered = None             # map_render.RenderedMap
        self.rooms: list[dict] = []
        self.map_id: int | None = None
        self.areas: list[dict] = []

    async def _fetch_bin(self):
        try:
            return await self.main.api.get_map_bin()
        except BotslabAuthError:
            if await self.main._relogin():
                return await self.main.api.get_map_bin()
            raise

    def current_png(self) -> bytes | None:
        """Cheap live compose (robot + heading + path) over the cached base."""
        if self.rendered is None:
            return None
        from . import map_render
        try:
            return map_render.compose_png(
                self.rendered, pose=self.main.robot_pose, path=self.main.robot_path
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Botslab map compose failed: %s", err)
            return None

    async def _async_update_data(self) -> bytes | None:
        try:
            text, tag = await self._fetch_bin()
        except BotslabAuthError as err:
            raise UpdateFailed(str(err)) from err
        except BotslabError as err:
            raise UpdateFailed(str(err)) from err
        if not text:
            return self.current_png()
        from . import map_render
        if tag != self._tag or self.rendered is None:
            try:
                rendered = await self.hass.async_add_executor_job(map_render.build_map, text)
                self.rendered = rendered
                self._tag = tag
                self.rooms = rendered.rooms
                self.areas = rendered.areas
                try:  # map_id lives on the raw record, not on geom
                    self.map_id = map_render.parse_map(text).get("mapId")
                except Exception:  # noqa: BLE001
                    pass
            except Exception as err:  # noqa: BLE001 - render must never break polling
                _LOGGER.warning("Botslab map build failed: %s", err)
        return self.current_png()
