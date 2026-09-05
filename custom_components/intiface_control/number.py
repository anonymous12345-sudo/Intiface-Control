"""Number entities: one per device for intensity (whichever of
vibrate/oscillate/rotate/constrict/temperature/spray the device
supports, picked automatically), one per device for position, and one
per device for position duration, for devices that support it."""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IntifaceCoordinator

_LOGGER = logging.getLogger(__name__)

INTENSITY_CAPS = {"oscillate", "vibrate", "rotate", "constrict", "temperature", "spray"}


def _device_info(entry_id: str, slug: str, name: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_{slug}")},
        name=name,
        manufacturer="Buttplug.io",
    )


class IntifaceIntensityNumber(CoordinatorEntity[IntifaceCoordinator], NumberEntity):
    """Generic 0-100% intensity control — maps to whichever output type
    (vibrate/oscillate/rotate/constrict/temperature/spray) the device
    actually supports."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_has_entity_name = True
    _attr_translation_key = "intensity"

    def __init__(self, coordinator: IntifaceCoordinator, entry_id: str, slug: str, name: str) -> None:
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


class IntifacePositionNumber(CoordinatorEntity[IntifaceCoordinator], NumberEntity):
    """0-100% position control for linear devices (e.g. a stroker). Uses
    whatever duration is currently set on this device's companion
    "Position duration" entity (see IntifacePositionDurationNumber below)
    — 0/instant if that was never touched."""

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_has_entity_name = True
    _attr_translation_key = "position"

    def __init__(self, coordinator: IntifaceCoordinator, entry_id: str, slug: str, name: str) -> None:
        super().__init__(coordinator)
        self._slug = slug
        self._attr_unique_id = f"{entry_id}_{slug}_position"
        self._attr_device_info = _device_info(entry_id, slug, name)
        self._attr_native_value = 0.0
        coordinator.add_stop_listener(self._on_stop)

    def _on_stop(self, affected_slug: str | None) -> None:
        """See IntifaceIntensityNumber._on_stop() above."""
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


class IntifacePositionDurationNumber(CoordinatorEntity[IntifaceCoordinator], NumberEntity):
    """How long the companion position slider's next move should take,
    in seconds (0-10, matching the 0-10000ms range the standalone
    bridge project originally supported). A stored preference, not a
    live toy command by itself — moving this slider alone never sends
    anything to the device. Not reset by the emergency-stop gates: it
    doesn't move anything, and a stopped device already refuses
    position commands regardless of whatever duration is set here."""

    _attr_native_min_value = 0
    _attr_native_max_value = 10
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = "s"
    _attr_mode = NumberMode.SLIDER
    _attr_has_entity_name = True
    _attr_translation_key = "position_duration"
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: IntifaceCoordinator, entry_id: str, slug: str, name: str) -> None:
        super().__init__(coordinator)
        self._slug = slug
        self._attr_unique_id = f"{entry_id}_{slug}_position_duration"
        self._attr_device_info = _device_info(entry_id, slug, name)
        self._attr_native_value = 0.0

    @property
    def available(self) -> bool:
        return super().available and self._slug in (self.coordinator.data or {})

    async def async_set_native_value(self, value: float) -> None:
        self._attr_native_value = value
        self.async_write_ha_state()
        self.coordinator.async_set_position_duration(self._slug, int(value * 1000))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: IntifaceCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _add_for_new_devices(new_devices) -> None:
        entities = []
        for slug, dev, caps in new_devices:
            name = getattr(dev, "name", slug)
            if INTENSITY_CAPS.intersection(caps):
                entities.append(IntifaceIntensityNumber(coordinator, entry.entry_id, slug, name))
            if "position" in caps or "position_with_duration" in caps:
                entities.append(IntifacePositionNumber(coordinator, entry.entry_id, slug, name))
                entities.append(IntifacePositionDurationNumber(coordinator, entry.entry_id, slug, name))
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
