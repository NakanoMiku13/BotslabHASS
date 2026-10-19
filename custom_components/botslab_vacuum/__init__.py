"""The Botslab Vacuum integration."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_change
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


async def _register_resources(hass: HomeAssistant, base: str, ver: str) -> bool:
    """Register the cards as Lovelace resources (eager load, no render race).

    ``add_extra_js_url`` loads modules asynchronously, so a dashboard can render
    a ``custom:`` card *before* its module is defined and get stuck on an
    "Error de configuración" until a manual refresh. Lovelace resources are
    loaded before the dashboard renders, which avoids the race. Only the
    storage-mode resource collection supports creating items; return False
    (→ caller falls back to ``add_extra_js_url``) for yaml-mode or if the
    Lovelace data is not ready.
    """
    data = hass.data.get("lovelace")
    resources = getattr(data, "resources", None)
    if resources is None and isinstance(data, dict):
        resources = data.get("resources")
    if resources is None or not hasattr(resources, "async_create_item"):
        return False
    try:
        if not getattr(resources, "loaded", True):
            await resources.async_load()
            resources.loaded = True
        by_base = {item["url"].split("?")[0]: item for item in resources.async_items()}
        for c in _CARDS:
            url = f"{base}/{c}"
            full = f"{url}?v={ver}"
            item = by_base.get(url)
            if item is None:
                await resources.async_create_item({"res_type": "module", "url": full})
            elif item.get("url") != full:  # bump cache-busting query on upgrade
                await resources.async_update_item(
                    item["id"], {"res_type": "module", "url": full}
                )
        return True
    except Exception as err:  # noqa: BLE001 - frontend is best-effort
        _LOGGER.warning("Botslab resource registration failed: %s", err)
        return False


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
    except Exception as err:  # noqa: BLE001 - frontend is best-effort
        _LOGGER.warning("Botslab static path registration failed: %s", err)
        return
    try:
        ver = (await async_get_integration(hass, DOMAIN)).version or "0"
    except Exception:  # noqa: BLE001
        ver = "0"
    if not await _register_resources(hass, base, ver):
        # Fallback for yaml-mode resources / Lovelace not ready.
        for c in _CARDS:
            add_extra_js_url(hass, f"{base}/{c}?v={ver}")
    _LOGGER.debug("Botslab frontend cards registered")


async def _run_due_routines(hass: HomeAssistant, coordinator, now) -> None:
    """Fire any HA-driven routines whose time/day matches `now` (local)."""
    day = (now.weekday() + 1) % 7            # 0=Sun .. 6=Sat (matches the card)
    hhmm = now.strftime("%H:%M")
    for r in coordinator.routines:
        if not r.get("active") or r.get("time") != hhmm:
            continue
        days = r.get("days") or []
        if days and day not in days:
            continue
        if coordinator.is_cleaning():
            _LOGGER.info("Botslab routine '%s' skipped: already cleaning", hhmm)
            continue
        try:
            await coordinator.async_run_routine(r)
            _LOGGER.info("Botslab routine fired at %s (rooms=%s)", hhmm, r.get("room_ids"))
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Botslab routine run failed: %s", err)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await _register_frontend(hass)
    coordinator = BotslabCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    # map is best-effort: never block setup if the render/download fails
    await coordinator.map_coordinator.async_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    @callback
    def _tick(now):
        hass.async_create_task(_run_due_routines(hass, coordinator, now))

    # HA-driven routine engine: evaluate once a minute (native schedules on this
    # robot don't fire, so HA drives them).
    entry.async_on_unload(async_track_time_change(hass, _tick, second=0))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
