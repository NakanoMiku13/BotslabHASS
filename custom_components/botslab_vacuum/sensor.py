"""Sensor entities for Botslab Vacuum (battery, status, error, …)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, FAN_SPEEDS, WORKING_STATUS_NAME
from .coordinator import BotslabCoordinator


@dataclass(frozen=True, kw_only=True)
class BotslabSensor(SensorEntityDescription):
    value: Callable[[dict], object] | None = None


def _suction_name(s: dict):
    try:
        i = int(s.get("SuctionPowLevel"))
        return FAN_SPEEDS[i] if 0 <= i < len(FAN_SPEEDS) else i
    except (TypeError, ValueError):
        return None


def _status_name(s: dict):
    try:
        return WORKING_STATUS_NAME.get(int(s.get("WorkingStatus", s.get("TEST_WorkingStatus"))))
    except (TypeError, ValueError):
        return None


SENSORS: tuple[BotslabSensor, ...] = (
    BotslabSensor(
        key="battery", name="Battery", native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY, state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.get("BatteryLevel"),
    ),
    BotslabSensor(key="status", name="Status", value=_status_name),
    BotslabSensor(
        key="error", name="Error code", entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: s.get("ErrorCode"),
    ),
    BotslabSensor(key="suction", name="Suction", value=_suction_name),
    BotslabSensor(
        key="cleaning_area", name="Cleaning area",
        state_class=SensorStateClass.MEASUREMENT, value=lambda s: s.get("CleaningArea"),
    ),
    BotslabSensor(
        key="volume", name="Volume", native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC, value=lambda s: s.get("VolumeLevel"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BotslabCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list = [BotslabSensorEntity(coordinator, d) for d in SENSORS]
    entities.append(BotslabSchedulesSensor(coordinator))
    async_add_entities(entities)


def _secs_to_hhmm(sec) -> str:
    try:
        sec = int(sec) % 86400
        return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}"
    except (TypeError, ValueError):
        return "00:00"


class BotslabSchedulesSensor(CoordinatorEntity, SensorEntity):
    """Exposes the device's native cleaning routines for the routines card."""

    _attr_has_entity_name = True
    _attr_name = "Schedules"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, main: BotslabCoordinator) -> None:
        super().__init__(main.map_coordinator)
        self._main = main
        dn = main.entry.data["device_name"]
        self._attr_unique_id = f"{dn}_schedules"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, dn)})

    @property
    def native_value(self):
        return len(self.coordinator.schedules or [])

    @property
    def extra_state_attributes(self) -> dict:
        out = []
        for i, e in enumerate(self.coordinator.schedules or []):
            out.append({
                "slot": i,
                "time": _secs_to_hhmm(e.get("startTime")),
                "days": e.get("period") or [],
                "room_ids": e.get("smartAreaIds") or [],
                "suction": e.get("workNoisy"),
                "passes": e.get("cleanTimes"),
                "active": bool(e.get("active")),
                "order": bool(e.get("orderClean")),
            })
        return {
            "schedules": out,
            "rooms": {r["id"]: r["name"] for r in (self.coordinator.rooms or [])},
        }


class BotslabSensorEntity(CoordinatorEntity[BotslabCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: BotslabSensor

    def __init__(self, coordinator: BotslabCoordinator, desc: BotslabSensor) -> None:
        super().__init__(coordinator)
        self.entity_description = desc
        dn = coordinator.entry.data["device_name"]
        self._attr_unique_id = f"{dn}_{desc.key}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, dn)})

    @property
    def native_value(self):
        if self.entity_description.value is None:
            return None
        return self.entity_description.value(self.coordinator.data or {})
