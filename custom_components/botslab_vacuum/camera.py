"""Rendered live map camera for the Botslab vacuum."""
from __future__ import annotations

from homeassistant.components.camera import Camera
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
    main: BotslabCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BotslabMapCamera(main)])


class BotslabMapCamera(CoordinatorEntity, Camera):
    """Serves the PNG rendered by the map coordinator."""

    _attr_has_entity_name = True
    _attr_name = "Map"

    def __init__(self, main: BotslabCoordinator) -> None:
        CoordinatorEntity.__init__(self, main.map_coordinator)
        Camera.__init__(self)
        dn = main.entry.data[CONF_DEVICE_NAME]
        self._attr_unique_id = f"{dn}_map"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, dn)})
        self.content_type = "image/png"

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        return self.coordinator.data

    @property
    def available(self) -> bool:
        return self.coordinator.data is not None
