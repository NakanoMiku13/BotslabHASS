"""The Botslab Vacuum integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import BotslabCoordinator

PLATFORMS = [
    Platform.VACUUM, Platform.SENSOR, Platform.CAMERA,
    Platform.NUMBER, Platform.SWITCH, Platform.SELECT,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = BotslabCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    # map is best-effort: never block setup if the render/download fails
    await coordinator.map_coordinator.async_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
