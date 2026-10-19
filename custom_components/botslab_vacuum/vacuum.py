"""Vacuum entity for Botslab Vacuum."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_STATUS, ATTR_STATUS_TEST, ATTR_SUCTION, DOMAIN, FAN_SPEEDS,
    SUCTION_MODE_DEFAULT, SVC_LOCATE, SVC_PAUSE, SVC_RESUME, SVC_RETURN,
    SVC_START, SVC_SUCTION, WORKING_STATUS, WORKING_STATUS_NAME,
)
from .coordinator import BotslabCoordinator

_LOGGER = logging.getLogger(__name__)

_ACTIVITY = {
    "cleaning": VacuumActivity.CLEANING,
    "paused": VacuumActivity.PAUSED,
    "docked": VacuumActivity.DOCKED,
    "returning": VacuumActivity.RETURNING,
    "idle": VacuumActivity.IDLE,
    "error": VacuumActivity.ERROR,
}

SUPPORT = (
    VacuumEntityFeature.START
    | VacuumEntityFeature.PAUSE
    | VacuumEntityFeature.STOP
    | VacuumEntityFeature.RETURN_HOME
    | VacuumEntityFeature.FAN_SPEED
    | VacuumEntityFeature.STATE
    | VacuumEntityFeature.LOCATE
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BotslabCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BotslabVacuum(coordinator)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "clean_rooms",
        {
            vol.Required("room_ids"): vol.All(cv.ensure_list, [vol.Coerce(int)]),
            vol.Optional("passes", default=1): vol.In([1, 2]),
            vol.Optional("suction"): vol.In([0, 1, 2, 3]),
        },
        "async_clean_rooms",
    )
    platform.async_register_entity_service(
        "set_schedule",
        {
            vol.Required("time"): str,                       # "HH:MM"
            vol.Required("days"): vol.All(cv.ensure_list, [vol.All(vol.Coerce(int), vol.Range(0, 6))]),
            vol.Optional("room_ids", default=[]): vol.All(cv.ensure_list, [vol.Coerce(int)]),
            vol.Optional("suction", default="auto"): vol.In(
                ["quiet", "normal", "strong", "max", "auto"]),
            vol.Optional("passes", default=1): vol.In([1, 2]),
            vol.Optional("active", default=True): cv.boolean,
            vol.Optional("order", default=True): cv.boolean,
            vol.Optional("slot", default=-1): vol.Coerce(int),
        },
        "async_set_schedule",
    )
    platform.async_register_entity_service(
        "clean_zones",
        {
            vol.Required("zones"): vol.All(cv.ensure_list, [[vol.Coerce(float)]]),
            vol.Optional("repeats", default=1): vol.In([1, 2]),
            vol.Optional("suction"): vol.In([0, 1, 2, 3]),
        },
        "async_clean_zones",
    )
    platform.async_register_entity_service(
        "set_no_go_zones",
        {
            vol.Optional("zones", default=[]): vol.All(cv.ensure_list, [[vol.Coerce(float)]]),
            vol.Optional("mode", default="replace"): vol.In(["replace", "add", "clear"]),
        },
        "async_set_no_go_zones",
    )
    platform.async_register_entity_service(
        "delete_schedule",
        {vol.Required("slot"): vol.Coerce(int)},
        "async_delete_schedule",
    )
    platform.async_register_entity_service(
        "run_schedule",
        {vol.Required("slot"): vol.Coerce(int)},
        "async_run_schedule",
    )


class BotslabVacuum(CoordinatorEntity[BotslabCoordinator], StateVacuumEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_supported_features = SUPPORT
    _attr_fan_speed_list = FAN_SPEEDS

    def __init__(self, coordinator: BotslabCoordinator) -> None:
        super().__init__(coordinator)
        dn = coordinator.entry.data["device_name"]
        self._attr_unique_id = f"{dn}_vacuum"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, dn)},
            name=coordinator.device_title,
            manufacturer="Botslab (360/Qihoo)",
            model="Botslab S8",
        )

    # -- helpers ----------------------------------------------------------- #
    def _status_int(self) -> int | None:
        s = self.coordinator.data or {}
        raw = s.get(ATTR_STATUS, s.get(ATTR_STATUS_TEST))
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    @property
    def activity(self) -> VacuumActivity | None:
        st = self._status_int()
        if st is None:
            return None
        return _ACTIVITY.get(WORKING_STATUS.get(st, "idle"), VacuumActivity.IDLE)

    @property
    def fan_speed(self) -> str | None:
        s = self.coordinator.data or {}
        try:
            idx = int(s.get(ATTR_SUCTION))
        except (TypeError, ValueError):
            return None
        return FAN_SPEEDS[idx] if 0 <= idx < len(FAN_SPEEDS) else None

    @property
    def extra_state_attributes(self) -> dict:
        s = self.coordinator.data or {}
        st = self._status_int()
        attrs = {"working_status": WORKING_STATUS_NAME.get(st, st)}
        for k in ("BatteryLevel", "ErrorCode", "CleaningArea", "MopModeSwitching",
                  "PadWetness", "VolumeLevel", "MoppingPadInstalled"):
            if k in s:
                attrs[k] = s[k]
        rooms = getattr(self.coordinator, "map_coordinator", None)
        if rooms and rooms.rooms:
            attrs["rooms"] = {r["id"]: r["name"] for r in rooms.rooms}
        return attrs

    # -- commands ---------------------------------------------------------- #
    async def _after(self) -> None:
        await self.coordinator.async_request_refresh()

    async def async_start(self) -> None:
        if self.activity == VacuumActivity.PAUSED:
            await self.coordinator.api.invoke_service(SVC_RESUME)
        else:
            await self.coordinator.api.invoke_service(SVC_START, {"CleanTimes": 1})
        await self._after()

    async def async_pause(self) -> None:
        await self.coordinator.api.invoke_service(SVC_PAUSE, {"TargetAction": 0})
        await self._after()

    async def async_stop(self, **kwargs) -> None:
        # No dedicated StopClean service; TargetAction 2 = "Others" (generic stop).
        await self.coordinator.api.invoke_service(SVC_PAUSE, {"TargetAction": 2})
        await self._after()

    async def async_return_to_base(self, **kwargs) -> None:
        await self.coordinator.api.invoke_service(SVC_RETURN)
        await self._after()

    async def async_locate(self, **kwargs) -> None:
        await self.coordinator.api.invoke_service(SVC_LOCATE)
        await self._after()

    async def async_set_fan_speed(self, fan_speed: str, **kwargs) -> None:
        if fan_speed not in FAN_SPEEDS:
            raise ValueError(f"unknown fan_speed {fan_speed}")
        level = FAN_SPEEDS.index(fan_speed)
        await self.coordinator.api.invoke_service(
            SVC_SUCTION, {"Mode": SUCTION_MODE_DEFAULT, "Level": level}
        )
        await self._after()

    # -- custom services --------------------------------------------------- #
    async def async_clean_rooms(self, room_ids, passes=1, suction=None) -> None:
        """Clean the given rooms (by id, in order) via inline zone cleaning."""
        await self.coordinator.async_clean_rooms(room_ids, passes, suction)

    # -- HA-driven routines (stored + executed by HA; native ones don't fire) -- #
    async def async_set_schedule(
        self, time, days, room_ids=None, suction="auto", passes=1,
        active=True, order=True, slot=-1,
    ) -> None:
        """Create/replace an HA-driven routine (stored in HA, run by HA)."""
        routine = {
            "time": time,
            "days": [int(d) for d in (days or [])],
            "room_ids": [int(r) for r in (room_ids or [])],
            "suction": suction,
            "passes": 2 if int(passes) == 2 else 1,
            "active": bool(active),
        }
        routines = self.coordinator.routines
        if 0 <= slot < len(routines):
            routines[slot] = routine
        else:
            routines.append(routine)
        await self.coordinator.save_routines(routines)

    async def async_delete_schedule(self, slot: int) -> None:
        routines = self.coordinator.routines
        if not (0 <= slot < len(routines)):
            raise HomeAssistantError(f"routine slot {slot} out of range")
        routines.pop(slot)
        await self.coordinator.save_routines(routines)

    async def async_run_schedule(self, slot: int) -> None:
        """Run a stored routine immediately (manual trigger)."""
        routines = self.coordinator.routines
        if not (0 <= slot < len(routines)):
            raise HomeAssistantError(f"routine slot {slot} out of range")
        await self.coordinator.async_run_routine(routines[slot])

    @staticmethod
    def _rect_vertexs(z) -> list:
        """[x1,y1,x2,y2] (mm) -> closed polygon vertexs."""
        x1, y1, x2, y2 = (int(round(v)) for v in z[:4])
        return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]

    async def async_clean_zones(self, zones, repeats=1, suction=None) -> None:
        """Clean free-drawn rectangles (vacuum mm coords from the map card)."""
        map_id = self.coordinator.map_coordinator.map_id
        out = []
        for i, z in enumerate(zones):
            if len(z) < 4:
                continue
            out.append({
                "name": f"zone{i}", "id": i, "tag": "",
                "active": "depth" if repeats == 2 else "normal",
                "vertexs": self._rect_vertexs(z),
            })
        if not out:
            raise HomeAssistantError("no valid zones supplied")
        if suction is not None:
            await self.coordinator.api.invoke_service(
                SVC_SUCTION, {"Mode": SUCTION_MODE_DEFAULT, "Level": int(suction)}
            )
        await self.coordinator.api.clean_zones(out, map_id)
        await self._after()

    async def async_set_no_go_zones(self, zones=None, mode="replace") -> None:
        """Create/replace/clear no-go (forbidden) zones."""
        mc = self.coordinator.map_coordinator
        map_id = mc.map_id
        if map_id is None:
            raise HomeAssistantError("map not loaded yet")
        existing = list(mc.areas or [])
        new = []
        for z in (zones or []):
            if len(z) >= 4:
                new.append({"vertexs": self._rect_vertexs(z), "active": "forbid",
                            "forbidType": "all", "tag": ""})
        if mode == "clear":
            result = []
        elif mode == "add":
            result = existing + new
        else:  # replace
            result = new
        # (re)assign ids/names
        for i, a in enumerate(result):
            a["id"] = i
            a.setdefault("name", f"rect{i}")
        await self.coordinator.api.set_fbd_places(map_id, result)
        await self._after()
