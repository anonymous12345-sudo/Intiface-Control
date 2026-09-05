"""LED light control — one per device that reports an LED capability.

Modeled as a real Home Assistant `light` entity (brightness only, no
color modes) rather than another generic `number` slider — that's the
idiomatic Home Assistant representation for a light, and gets the
matching UI treatment (light-bulb icon, brightness slider in the
"more info" dialog, works with light-specific automations/scripts) for
free.
"""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IntifaceCoordinator


class IntifaceLed(CoordinatorEntity[IntifaceCoordinator], LightEntity):
    """Brightness-only light — no color support (buttplug's Led output
    is a single 0-100% scalar, same shape as vibrate/rotate/etc, not an
    RGB value)."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.BRIGHTNESS}
    _attr_has_entity_name = True
    _attr_translation_key = "led"

    def __init__(self, coordinator: IntifaceCoordinator, entry_id: str, slug: str, name: str) -> None:
        super().__init__(coordinator)
        self._slug = slug
        self._attr_unique_id = f"{entry_id}_{slug}_led"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{slug}")},
            name=name,
            manufacturer="Buttplug.io",
        )
        self._attr_is_on = False
        self._attr_brightness = 255

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.add_stop_listener(self._on_stop))

    def _on_stop(self, affected_slug: str | None) -> None:
        """Same reasoning as the number entities' _on_stop(): don't keep
        showing a stale "on" state the device isn't actually doing
        anymore once a stop (global or per-device) engages."""
        if affected_slug is not None and affected_slug != self._slug:
            return
        self._attr_is_on = False
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return super().available and self._slug in (self.coordinator.data or {})

    async def async_turn_on(self, **kwargs) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS, self._attr_brightness or 255)
        ok = await self.coordinator.async_apply_led(self._slug, (brightness / 255) * 100)
        if ok:
            self._attr_is_on = True
            self._attr_brightness = brightness
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        ok = await self.coordinator.async_apply_led(self._slug, 0)
        if ok:
            self._attr_is_on = False
            self.async_write_ha_state()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: IntifaceCoordinator = hass.data[DOMAIN][entry.entry_id]

    def _add_for_new_devices(new_devices) -> None:
        entities = [
            IntifaceLed(coordinator, entry.entry_id, slug, getattr(dev, "name", slug))
            for slug, dev, caps in new_devices
            if "led" in caps
        ]
        if entities:
            async_add_entities(entities)

    initial = [
        (slug, info["device"], info["capabilities"])
        for slug, info in (coordinator.data or {}).items()
    ]
    _add_for_new_devices(initial)
    coordinator.add_new_device_listener(_add_for_new_devices)
