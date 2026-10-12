"""The Botslab Vacuum integration."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import DOMAIN
from .coordinator import BotslabCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.VACUUM, Platform.SENSOR, Platform.CAMERA,
    Platform.NUMBER, Platform.SWITCH, Platform.SELECT,
]

# Frontend cards bundled in ./www and auto-registered so the dashboard works
# with zero manual resource setup.
_CARDS = ["botslab-routines-card.js", "xiaomi-vacuum-map-card.js"]


async def _register_frontend(hass: HomeAssistant) -> None:
    store = hass.data.setdefault(DOMAIN, {})
    if store.get("_frontend_registered"):
        return
    store["_frontend_registered"] = True
    www = Path(__file__).parent / "www"
    base = f"/{DOMAIN}/www"
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(f"{base}/{c}", str(www / c), True) for c in _CARDS]
        )
        try:
            ver = (await async_get_integration(hass, DOMAIN)).version or "0"
        except Exception:  # noqa: BLE001
            ver = "0"
        for c in _CARDS:
            add_extra_js_url(hass, f"{base}/{c}?v={ver}")
        _LOGGER.debug("Botslab frontend cards registered")
    except Exception as err:  # noqa: BLE001 - frontend is best-effort
        _LOGGER.warning("Botslab frontend registration failed: %s", err)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await _register_frontend(hass)
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
