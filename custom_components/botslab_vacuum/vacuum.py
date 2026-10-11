"""Vacuum entity for Botslab Vacuum."""
from __future__ import annotations

import logging

from homeassistant.components.vacuum import (
    StateVacuumEntity,
    VacuumActivity,
    VacuumEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
