"""Number entities for Botslab Vacuum (volume)."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_NAME, DOMAIN
from .coordinator import BotslabCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BotslabCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BotslabVolumeNumber(coordinator)])


class BotslabVolumeNumber(CoordinatorEntity[BotslabCoordinator], NumberEntity):
    _attr_has_entity_name = True
    _attr_name = "Volume"
    _attr_icon = "mdi:volume-high"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator: BotslabCoordinator) -> None:
        super().__init__(coordinator)
        dn = coordinator.entry.data[CONF_DEVICE_NAME]
        self._attr_unique_id = f"{dn}_volume"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, dn)})

    @property
    def native_value(self) -> float | None:
        try:
            return int((self.coordinator.data or {}).get("VolumeLevel"))
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.api.set_desired_property("VolumeLevel", int(value))
        await self.coordinator.async_request_refresh()
