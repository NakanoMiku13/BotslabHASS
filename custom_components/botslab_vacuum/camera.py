"""Rendered live map camera for the Botslab vacuum.

Serves a PNG composed live (robot + heading + path over a cached base) and
exposes calibration + room outlines as attributes so an interactive Lovelace
map card (xiaomi-vacuum-map-card) can overlay zone/room/no-go selection.
"""
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
    _attr_has_entity_name = True
    _attr_name = "Map"

    def __init__(self, main: BotslabCoordinator) -> None:
        CoordinatorEntity.__init__(self, main.map_coordinator)
        Camera.__init__(self)
        self._main = main
        dn = main.entry.data[CONF_DEVICE_NAME]
        self._attr_unique_id = f"{dn}_map"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, dn)})
        self.content_type = "image/png"

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        return await self.hass.async_add_executor_job(self.coordinator.current_png)

    @property
    def available(self) -> bool:
        return self.coordinator.rendered is not None

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {}
        rendered = self.coordinator.rendered
        if rendered is not None:
            attrs["calibration_points"] = rendered.geom.calibration_points()
            attrs["rooms"] = [
                {"id": r["id"], "name": r["name"], "outline": r.get("vertexs") or []}
                for r in self.coordinator.rooms
            ]
        if self._main.robot_pose:
            p = self._main.robot_pose
            attrs["robot_position"] = {
                "x": p[0], "y": p[1], "angle": p[2] if len(p) > 2 else None,
            }
        return attrs
