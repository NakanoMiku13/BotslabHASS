"""Select entities for Botslab Vacuum (mop mode, pad wetness)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_NAME, DOMAIN
from .coordinator import BotslabCoordinator

# identifier -> {int key: label}
MOP_MODES = {0: "DrySweep", 1: "MixedMode", 2: "MopMode"}
PAD_WETNESS = {1: "Low", 2: "Medium", 3: "High"}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BotslabCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        BotslabEnumSelect(coordinator, "MopModeSwitching", "Mop mode",
                          "mdi:format-list-bulleted", MOP_MODES),
        BotslabEnumSelect(coordinator, "PadWetness", "Pad wetness",
                          "mdi:water-percent", PAD_WETNESS),
    ])


class BotslabEnumSelect(CoordinatorEntity[BotslabCoordinator], SelectEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, identifier, name, icon, mapping) -> None:
        super().__init__(coordinator)
        self._id = identifier
        self._map = mapping
        self._rev = {v: k for k, v in mapping.items()}
        self._attr_name = name
        self._attr_icon = icon
        self._attr_options = list(mapping.values())
        dn = coordinator.entry.data[CONF_DEVICE_NAME]
        self._attr_unique_id = f"{dn}_{identifier.lower()}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, dn)})

    @property
    def current_option(self) -> str | None:
        s = self.coordinator.data or {}
        if self._id not in s:
            return None
        try:
            return self._map.get(int(s.get(self._id)))
        except (TypeError, ValueError):
            return None

    async def async_select_option(self, option: str) -> None:
        key = self._rev.get(option)
        if key is None:
            raise ValueError(f"unknown option {option}")
        await self.coordinator.api.set_desired_property(self._id, int(key))
        await self.coordinator.async_request_refresh()
