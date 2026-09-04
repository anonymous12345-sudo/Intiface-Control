"""Emergency-stop switch — one per config entry.

Turning it ON immediately stops every connected toy and cancels any
running pattern; turning it OFF just clears the stopped state (no toy
action) so normal operation can resume. Its on/off state can be used the
same way the old input_boolean-based "abort" helper was used, as a gate
condition in automations and scripts — but this one also actually stops
the toys itself, rather than only being a boolean flag other automations
had to act on.
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ButtplugCoordinator


class ButtplugStopSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "stop_all"
    _attr_icon = "mdi:stop-circle"

    def __init__(self, coordinator: ButtplugCoordinator, entry_id: str) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry_id}_stop_all"
        self._attr_is_on = False

    @property
    def is_on(self) -> bool:
        return self._attr_is_on

    async def async_turn_on(self, **kwargs) -> None:
        await self._coordinator.async_stop_all()
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._coordinator.async_clear_stop()
        self._attr_is_on = False
        self.async_write_ha_state()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: ButtplugCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ButtplugStopSwitch(coordinator, entry.entry_id)])
