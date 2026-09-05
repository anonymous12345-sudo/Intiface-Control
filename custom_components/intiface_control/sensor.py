"""Sensors: battery percentage, Bluetooth signal strength (RSSI), and a
pressure reading — one of each per device that reports the matching
capability."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, SIGNAL_STRENGTH_DECIBELS_MILLIWATT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IntifaceCoordinator


def _device_info(entry_id: str, slug: str, name: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_{slug}")},
        name=name,
        manufacturer="Buttplug.io",
    )


class IntifaceBatterySensor(CoordinatorEntity[IntifaceCoordinator], SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_has_entity_name = True
    _attr_translation_key = "battery"

    def __init__(self, coordinator: IntifaceCoordinator, entry_id: str, slug: str, name: str) -> None:
        super().__init__(coordinator)
        self._slug = slug
        self._attr_unique_id = f"{entry_id}_{slug}_battery"
        self._attr_device_info = _device_info(entry_id, slug, name)

    @property
    def available(self) -> bool:
        return super().available and self._slug in (self.coordinator.data or {})

    @property
    def native_value(self):
        info = (self.coordinator.data or {}).get(self._slug)
        return info["battery"] if info else None


class IntifaceRssiSensor(CoordinatorEntity[IntifaceCoordinator], SensorEntity):
    """Bluetooth signal strength, in dBm."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_has_entity_name = True
    _attr_translation_key = "rssi"

    def __init__(self, coordinator: IntifaceCoordinator, entry_id: str, slug: str, name: str) -> None:
        super().__init__(coordinator)
        self._slug = slug
        self._attr_unique_id = f"{entry_id}_{slug}_rssi"
        self._attr_device_info = _device_info(entry_id, slug, name)

    @property
    def available(self) -> bool:
        return super().available and self._slug in (self.coordinator.data or {})

    @property
    def native_value(self):
        info = (self.coordinator.data or {}).get(self._slug)
        return info["rssi"] if info else None


class IntifacePressureSensor(CoordinatorEntity[IntifaceCoordinator], SensorEntity):
    """A raw pressure reading. Untested against real hardware — no
    device_class or unit is set, since buttplug's exact pressure scale
    isn't confirmed (see client.py's read_pressure() for the same
    caveat). Disabled by default until that's verified against a real
    pressure-sensing toy, so it doesn't show a possibly-mislabelled
    number to everyone by default."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_registry_enabled_default = False
    _attr_has_entity_name = True
    _attr_translation_key = "pressure"

    def __init__(self, coordinator: IntifaceCoordinator, entry_id: str, slug: str, name: str) -> None:
        super().__init__(coordinator)
        self._slug = slug
        self._attr_unique_id = f"{entry_id}_{slug}_pressure"
        self._attr_device_info = _device_info(entry_id, slug, name)

    @property
    def available(self) -> bool:
        return super().available and self._slug in (self.coordinator.data or {})

    @property
    def native_value(self):
        info = (self.coordinator.data or {}).get(self._slug)
        return info["pressure"] if info else None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: IntifaceCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _add_for_new_devices(new_devices) -> None:
        entities = []
        for slug, dev, caps in new_devices:
            name = getattr(dev, "name", slug)
            if "battery" in caps:
                entities.append(IntifaceBatterySensor(coordinator, entry.entry_id, slug, name))
            if "rssi" in caps:
                entities.append(IntifaceRssiSensor(coordinator, entry.entry_id, slug, name))
            if "pressure" in caps:
                entities.append(IntifacePressureSensor(coordinator, entry.entry_id, slug, name))
        if entities:
            async_add_entities(entities)

    initial = [
        (slug, info["device"], info["capabilities"])
        for slug, info in (coordinator.data or {}).items()
    ]
    _add_for_new_devices(initial)
    coordinator.add_new_device_listener(_add_for_new_devices)
