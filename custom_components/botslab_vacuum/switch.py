"""Switch entities for Botslab Vacuum (auto boost)."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
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
    async_add_entities([BotslabAutoBoostSwitch(coordinator)])


def _truthy(v) -> bool:
    return v in (True, 1, "1", "true", "True", "on", "开")


class BotslabAutoBoostSwitch(CoordinatorEntity[BotslabCoordinator], SwitchEntity):
    _attr_has_entity_name = True
    _attr_name = "Auto boost"
    _attr_icon = "mdi:fan-auto"

    def __init__(self, coordinator: BotslabCoordinator) -> None:
        super().__init__(coordinator)
        dn = coordinator.entry.data[CONF_DEVICE_NAME]
        self._attr_unique_id = f"{dn}_auto_boost"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, dn)})

    @property
    def is_on(self) -> bool | None:
        s = self.coordinator.data or {}
        if "AutoBoost" not in s:
            return None
        return _truthy(s.get("AutoBoost"))

    async def _set(self, on: bool) -> None:
        await self.coordinator.api.set_desired_property("AutoBoost", on)
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)
