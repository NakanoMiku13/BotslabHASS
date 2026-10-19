"""DataUpdateCoordinator for Botslab Vacuum (auto re-login + live map)."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import BotslabApi, BotslabAuthError, BotslabError, BotslabSession
from .auth import LoginError, async_login
from .const import (
    ATTR_STATUS, ATTR_STATUS_TEST, CONF_DEVICE_NAME, CONF_EMAIL, CONF_M2,
    CONF_PASSWORD, CONF_PRODUCT_KEY, CONF_Q, CONF_REGION, CONF_SID,
    CONF_SIGN_SECRET, CONF_T, DEFAULT_REGION, DEFAULT_SCAN_INTERVAL, DOMAIN,
    SUCTION_MODE_DEFAULT, SVC_START, SVC_SUCTION, WORKING_STATUS,
)

_LOGGER = logging.getLogger(__name__)

# routine suction name -> SuctionPowLevel (None = leave as-is / "auto")
SUCTION_LEVELS = {"quiet": 0, "normal": 1, "strong": 2, "max": 3}

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
        self._seq_task = None            # running sequential room-clean task
        self.map_coordinator = BotslabMapCoordinator(hass, self)

    # -- HA-driven routines (stored in entry.options; executed by HA) -------- #
    @property
    def routines(self) -> list[dict]:
        return list(self.entry.options.get("routines", []))

    async def save_routines(self, routines: list[dict]) -> None:
        self.hass.config_entries.async_update_entry(
            self.entry, options={**self.entry.options, "routines": routines}
        )
        self.async_update_listeners()  # refresh the schedules sensor

    def is_cleaning(self) -> bool:
        return self._is_cleaning(self.data or {})

    # Robot's inline zone cleaning caps the zone count (~20 accepted, 40 rejected).
    _MAX_TOTAL_ZONES = 16
    # Per-room rectangle budget when each room is its own job (sequential).
    _MAX_ROOM_ZONES = 12

    @staticmethod
    def _build_zones(selected: list, passes: int, budget: int) -> list:
        """Tile each room with maximal rectangles -> list of zone dicts.

        The robot's zone cleaning only accepts *rectangles*, not room outlines
        (it rejects a raw polygon with "can't clean this room"). Each room is
        covered with rectangles that stay inside it (``room_cover_rects``) so we
        clean the real room shape — no spill into neighbours — without the OSS
        partition-file upload that native area cleaning requires.
        """
        from . import map_render

        active = "depth" if passes == 2 else "normal"
        zones: list = []
        for r in selected:
            for x1, y1, x2, y2 in map_render.room_cover_rects(
                r.get("vertexs") or [], max_rects=budget
            ):
                zones.append({
                    "name": r.get("name", f"room{r.get('id')}"), "id": len(zones),
                    "tag": "", "active": active,
                    "vertexs": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                })
        return zones

    async def _set_suction(self, suction) -> None:
        if suction is not None:
            await self.api.invoke_service(
                SVC_SUCTION, {"Mode": SUCTION_MODE_DEFAULT, "Level": int(suction)}
            )

    async def async_clean_rooms(
        self, room_ids, passes=1, suction=None, sequential=None
    ) -> None:
        """Clean rooms by id via inline zone cleaning. suction=int|None.

        With several rooms, zone cleaning does not preserve the requested order
        (the robot path-optimises across all zones). To honour the order, clean
        each room as its own job in sequence (``sequential``); this runs in the
        background so the service/time-trigger returns immediately. A single
        room is always one combined job.
        """
        rooms = {r["id"]: r for r in self.map_coordinator.rooms}
        selected = [rooms[rid] for rid in room_ids if rid in rooms]
        if not selected:
            raise HomeAssistantError(
                "No valid rooms (map not loaded or unknown ids). See the vacuum "
                "'rooms' attribute for valid ids."
            )
        if sequential is None:
            sequential = len(selected) > 1

        if sequential and len(selected) > 1:
            if self._seq_task and not self._seq_task.done():
                raise HomeAssistantError("A sequential room clean is already running.")
            self._seq_task = self.hass.async_create_task(
                self._run_rooms_sequential(selected, passes, suction)
            )
            return

        budget = max(1, self._MAX_TOTAL_ZONES // len(selected))
        zones = self._build_zones(selected, passes, budget)
        if not zones:
            raise HomeAssistantError("Could not derive cleanable zones from rooms.")
        await self._set_suction(suction)
        await self.api.clean_zones(zones, self.map_coordinator.map_id)
        await self.async_request_refresh()

    async def _run_rooms_sequential(self, selected, passes, suction) -> None:
        """Clean rooms one at a time, in order, waiting for each to finish."""
        await self._set_suction(suction)
        map_id = self.map_coordinator.map_id
        for r in selected:
            zones = self._build_zones([r], passes, self._MAX_ROOM_ZONES)
            if not zones:
                _LOGGER.warning("Botslab: room %s has no cleanable zones, skipping",
                                r.get("id"))
                continue
            try:
                await self.api.clean_zones(zones, map_id)
            except BotslabError as err:
                _LOGGER.error("Botslab sequential clean of room %s failed: %s",
                              r.get("id"), err)
                break
            await self._wait_until_idle()
        await self.async_request_refresh()

    async def _wait_until_idle(self, start_grace=90, timeout=2400, poll=15) -> None:
        """Wait for a started clean to begin then finish (best-effort, bounded)."""
        t0 = time.monotonic()
        # let the clean actually start
        while time.monotonic() - t0 < start_grace:
            await self.async_request_refresh()
            if self.is_cleaning():
                break
            await asyncio.sleep(5)
        # then wait until it is no longer cleaning (2 confirming polls)
        idle = 0
        while time.monotonic() - t0 < timeout:
            await asyncio.sleep(poll)
            await self.async_request_refresh()
            if self.is_cleaning():
                idle = 0
            else:
                idle += 1
                if idle >= 2:
                    return

    async def async_clean_whole(self, passes=1, suction=None) -> None:
        if suction is not None:
            await self.api.invoke_service(
                SVC_SUCTION, {"Mode": SUCTION_MODE_DEFAULT, "Level": int(suction)}
            )
        await self.api.invoke_service(SVC_START, {"CleanTimes": int(passes)})
        await self.async_request_refresh()

    async def async_run_routine(self, routine: dict) -> None:
        """Execute one routine now (rooms+order+passes+suction, or whole-house)."""
        suction = SUCTION_LEVELS.get((routine.get("suction") or "").lower())
        passes = 2 if int(routine.get("passes") or 1) == 2 else 1
        room_ids = [int(x) for x in (routine.get("room_ids") or [])]
        if room_ids:
            await self.async_clean_rooms(room_ids, passes, suction)
        else:
            await self.async_clean_whole(passes, suction)

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
