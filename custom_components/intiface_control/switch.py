"""Two kinds of switch: one global "Stop all toys" per config entry, plus
one per-device "Enabled" switch for each connected toy.

The global switch is a one-way emergency stop: turning it ON immediately
stops every toy and blocks all further commands until turned back OFF.

The per-device switch is framed the other way round, as normal on/off
availability rather than a stop button: ON (the default for a newly
connected toy) means it responds normally; turning it OFF immediately
stops that one toy and blocks commands to it — same gate mechanism as
the global switch, just inverted so a toy defaults to usable the moment
it connects rather than needing to be un-stopped first.

The two gates are fully independent: disabling one toy doesn't affect
the others or the global switch, and the global switch stopping
everything doesn't change any individual toy's own Enabled state.
"""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IntifaceCoordinator


class IntifaceStopAllSwitch(CoordinatorEntity[IntifaceCoordinator], SwitchEntity):
    """Entry-wide emergency stop — affects every connected toy. `is_on`
    is computed live from the coordinator's own gate state (matching
    IntifaceEnableSwitch below) rather than cached locally. Always
    available regardless of connection state — a kill switch should
    stay usable even while Intiface itself is unreachable."""

    _attr_has_entity_name = True
    _attr_translation_key = "stop_all"
    _attr_icon = "mdi:stop-circle"

    def __init__(self, coordinator: IntifaceCoordinator, entry_id: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_stop_all"

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.stopped

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_stop_all()
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self.coordinator.async_clear_stop()
        self.async_write_ha_state()


class IntifaceEnableSwitch(CoordinatorEntity[IntifaceCoordinator], SwitchEntity):
    """Per-device enable/disable — ON (default) means this toy responds
    normally; OFF immediately stops it and refuses further commands to it
    until switched back on. `is_on` is always computed live from the
    coordinator's own gate state rather than cached locally, so it can
    never drift out of sync with what commands actually get accepted."""

    _attr_has_entity_name = True
    _attr_translation_key = "enabled"
    _attr_icon = "mdi:power"

    def __init__(self, coordinator: IntifaceCoordinator, entry_id: str, slug: str, name: str) -> None:
        super().__init__(coordinator)
        self._slug = slug
        self._attr_unique_id = f"{entry_id}_{slug}_enabled"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{slug}")},
            name=name,
            manufacturer="Buttplug.io",
        )

    @property
    def available(self) -> bool:
        return super().available and self._slug in (self.coordinator.data or {})

    @property
    def is_on(self) -> bool:
        return self._slug not in self.coordinator.per_slug_stopped

    async def async_turn_on(self, **kwargs) -> None:
        self.coordinator.async_clear_device_stop(self._slug)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_stop_device(self._slug)
        self.async_write_ha_state()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: IntifaceCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([IntifaceStopAllSwitch(coordinator, entry.entry_id)])

    def _add_for_new_devices(new_devices) -> None:
        entities = [
            IntifaceEnableSwitch(coordinator, entry.entry_id, slug, getattr(dev, "name", slug))
            for slug, dev, caps in new_devices
        ]
        if entities:
            async_add_entities(entities)

    # Devices already known by the time this platform is set up need to
    # be seeded explicitly here — see the identical pattern in number.py.
    initial = [
        (slug, info["device"], info["capabilities"])
        for slug, info in (coordinator.data or {}).items()
    ]
    _add_for_new_devices(initial)

    coordinator.add_new_device_listener(_add_for_new_devices)
