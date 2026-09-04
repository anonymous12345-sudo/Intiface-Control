"""Connectivity binary_sensor — one per device.

This entity's *state* goes off when the device is offline; the entity
itself is never removed or marked unavailable for that reason. That's
deliberate: this is the "shows offline via an entity" mechanism, distinct
from the number/sensor entities elsewhere, which instead go 'unavailable'
(greyed out, not removed) while still existing on the dashboard.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IntifaceCoordinator


class IntifaceConnectivityBinarySensor(CoordinatorEntity[IntifaceCoordinator], BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_has_entity_name = True
    _attr_translation_key = "connected"
    # Deliberately no `available` override here: this entity always stays
    # available — its *state* (is_on) is what reflects online/offline.

    def __init__(self, coordinator: IntifaceCoordinator, entry_id: str, slug: str, name: str) -> None:
        super().__init__(coordinator)
        self._slug = slug
        self._attr_unique_id = f"{entry_id}_{slug}_connected"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{slug}")},
            name=name,
            manufacturer="Buttplug.io",
        )

    @property
    def is_on(self) -> bool:
        return self._slug in (self.coordinator.data or {})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: IntifaceCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _add_for_new_devices(new_devices) -> None:
        entities = [
            IntifaceConnectivityBinarySensor(coordinator, entry.entry_id, slug, getattr(dev, "name", slug))
            for slug, dev, caps in new_devices
        ]
        if entities:
            async_add_entities(entities)

    initial = [
        (slug, info["device"], info["capabilities"])
        for slug, info in (coordinator.data or {}).items()
    ]
    _add_for_new_devices(initial)
    coordinator.add_new_device_listener(_add_for_new_devices)
