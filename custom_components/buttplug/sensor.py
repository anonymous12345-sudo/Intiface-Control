"""Battery sensor — one per device that reports a battery capability."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ButtplugCoordinator


class ButtplugBatterySensor(CoordinatorEntity[ButtplugCoordinator], SensorEntity):
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_has_entity_name = True
    _attr_translation_key = "battery"

    def __init__(self, coordinator: ButtplugCoordinator, entry_id: str, slug: str, name: str) -> None:
        super().__init__(coordinator)
        self._slug = slug
        self._attr_unique_id = f"{entry_id}_{slug}_battery"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{slug}")},
            name=name,
            manufacturer="Buttplug.io",
        )

    @property
    def available(self) -> bool:
        return super().available and self._slug in (self.coordinator.data or {})

    @property
    def native_value(self):
        info = (self.coordinator.data or {}).get(self._slug)
        return info["battery"] if info else None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: ButtplugCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _add_for_new_devices(new_devices) -> None:
        entities = [
            ButtplugBatterySensor(coordinator, entry.entry_id, slug, getattr(dev, "name", slug))
            for slug, dev, caps in new_devices
            if "battery" in caps
        ]
        if entities:
            async_add_entities(entities)

    initial = [
        (slug, info["device"], info["capabilities"])
        for slug, info in (coordinator.data or {}).items()
    ]
    _add_for_new_devices(initial)
    coordinator.add_new_device_listener(_add_for_new_devices)
