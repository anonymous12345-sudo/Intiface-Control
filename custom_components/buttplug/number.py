"""Number entities: one per device for intensity (whichever of
vibrate/oscillate/rotate/constrict the device supports, picked
automatically) and one per device for position, for devices that support
it."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ButtplugCoordinator

_LOGGER = logging.getLogger(__name__)

INTENSITY_CAPS = {"oscillate", "vibrate", "rotate", "constrict"}


def _device_info(entry_id: str, slug: str, name: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_{slug}")},
        name=name,
        manufacturer="Buttplug.io",
    )


class ButtplugIntensityNumber(CoordinatorEntity[ButtplugCoordinator], NumberEntity):
    """Generic 0-100% intensity control — maps to whichever output type
    (vibrate/oscillate/rotate/constrict) the device actually supports."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_has_entity_name = True
    _attr_translation_key = "intensity"

    def __init__(self, coordinator: ButtplugCoordinator, entry_id: str, slug: str, name: str) -> None:
        super().__init__(coordinator)
        self._slug = slug
        self._attr_unique_id = f"{entry_id}_{slug}_intensity"
        self._attr_device_info = _device_info(entry_id, slug, name)
        self._attr_native_value = 0.0
        coordinator.add_stop_listener(self._on_stop)

    def _on_stop(self, affected_slug: str | None) -> None:
        """Called by the coordinator when a stop (global or per-device)
        engages — resets the displayed slider to 0 so it doesn't keep
        showing a stale value the device is no longer actually at.
        `affected_slug` is None for a global stop (always applies) or a
        specific slug for a per-device stop (only applies to that one)."""
        if affected_slug is not None and affected_slug != self._slug:
            return
        self._attr_native_value = 0.0
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return super().available and self._slug in (self.coordinator.data or {})

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
        await self.coordinator.async_apply_intensity(self._slug, value)


class ButtplugPositionNumber(CoordinatorEntity[ButtplugCoordinator], NumberEntity):
    """0-100% position control for linear devices (e.g. a stroker). Moves
    immediately — no duration control on this entity in this first
    version; the underlying coordinator method does support duration_ms
    if that's ever needed."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_has_entity_name = True
    _attr_translation_key = "position"

    def __init__(self, coordinator: ButtplugCoordinator, entry_id: str, slug: str, name: str) -> None:
        super().__init__(coordinator)
        self._slug = slug
        self._attr_unique_id = f"{entry_id}_{slug}_position"
        self._attr_device_info = _device_info(entry_id, slug, name)
        self._attr_native_value = 0.0
        coordinator.add_stop_listener(self._on_stop)

    def _on_stop(self, affected_slug: str | None) -> None:
        """See ButtplugIntensityNumber._on_stop() above."""
        if affected_slug is not None and affected_slug != self._slug:
            return
        self._attr_native_value = 0.0
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return super().available and self._slug in (self.coordinator.data or {})

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
        await self.coordinator.async_apply_position(self._slug, value)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: ButtplugCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _add_for_new_devices(new_devices) -> None:
        entities = []
        for slug, dev, caps in new_devices:
            name = getattr(dev, "name", slug)
            if INTENSITY_CAPS.intersection(caps):
                entities.append(ButtplugIntensityNumber(coordinator, entry.entry_id, slug, name))
            if "position" in caps or "position_with_duration" in caps:
                entities.append(ButtplugPositionNumber(coordinator, entry.entry_id, slug, name))
        if entities:
            async_add_entities(entities)

    # Devices already known by the time this platform is set up (the
    # coordinator's first refresh already ran in __init__.py, before any
    # listener could be registered) need to be seeded explicitly here.
    initial = [
        (slug, info["device"], info["capabilities"])
        for slug, info in (coordinator.data or {}).items()
    ]
    _add_for_new_devices(initial)

    coordinator.add_new_device_listener(_add_for_new_devices)
