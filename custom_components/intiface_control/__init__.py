"""The Intiface Control integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN
from .coordinator import IntifaceCoordinator

PLATFORMS = ["number", "binary_sensor", "sensor", "switch"]

SERVICE_START_WAVE_PATTERN = "start_wave_pattern"
SERVICE_START_PULSE_PATTERN = "start_pulse_pattern"
SERVICE_STOP_PATTERN = "stop_pattern"

# Every service below targets one or more Home Assistant *devices* (picked
# via the UI's device selector, see services.yaml) rather than a typed-in
# slug string. HA merges the resolved selection into the call as a
# "device_id" list automatically — this is the only field every schema
# shares.
_DEVICE_ID_FIELD = {vol.Required("device_id"): vol.All(cv.ensure_list, [cv.string])}

START_WAVE_PATTERN_SCHEMA = vol.Schema(
    {
        **_DEVICE_ID_FIELD,
        vol.Optional("repeat", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
        vol.Optional("min_speed", default=0): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Optional("max_speed", default=50): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Optional("duration", default=3): vol.All(vol.Coerce(float), vol.Range(min=0.2, max=60)),
    }
)

START_PULSE_PATTERN_SCHEMA = vol.Schema(
    {
        **_DEVICE_ID_FIELD,
        vol.Optional("repeat", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
        vol.Optional("low_speed", default=0): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Optional("high_speed", default=80): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
        vol.Optional("low_duration", default=2): vol.All(vol.Coerce(float), vol.Range(min=0.2, max=60)),
        vol.Optional("high_duration", default=2): vol.All(vol.Coerce(float), vol.Range(min=0.2, max=60)),
    }
)

STOP_PATTERN_SCHEMA = vol.Schema({**_DEVICE_ID_FIELD})


def _resolve_device(hass: HomeAssistant, device_id: str) -> tuple[IntifaceCoordinator, str] | None:
    """Maps a Home Assistant device_id back to the (coordinator, slug)
    pair it represents. Every device we create has an identifier of the
    form (DOMAIN, f"{entry_id}_{slug}") — matching that prefix against
    each known config entry tells us both which coordinator owns the
    device and what its own slug is, correctly handling more than one
    Intiface server being configured at once. Returns None for a
    device_id that isn't one of ours (e.g. it belongs to a different
    integration, or was removed)."""
    registry = dr.async_get(hass)
    device_entry = registry.async_get(device_id)
    if device_entry is None:
        return None
    for coordinator in hass.data.get(DOMAIN, {}).values():
        prefix = f"{coordinator.entry.entry_id}_"
        for ident_domain, identifier in device_entry.identifiers:
            if ident_domain == DOMAIN and identifier.startswith(prefix):
                return coordinator, identifier[len(prefix):]
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = IntifaceCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.services.has_service(DOMAIN, SERVICE_START_WAVE_PATTERN):

        async def _async_handle_start_wave_pattern(call: ServiceCall) -> None:
            for device_id in call.data["device_id"]:
                resolved = _resolve_device(hass, device_id)
                if resolved is None:
                    continue
                target_coordinator, slug = resolved
                await target_coordinator.async_start_wave_pattern(
                    slug,
                    repeat=call.data["repeat"],
                    min_speed_percent=call.data["min_speed"],
                    max_speed_percent=call.data["max_speed"],
                    duration=call.data["duration"],
                )

        async def _async_handle_start_pulse_pattern(call: ServiceCall) -> None:
            for device_id in call.data["device_id"]:
                resolved = _resolve_device(hass, device_id)
                if resolved is None:
                    continue
                target_coordinator, slug = resolved
                await target_coordinator.async_start_pulse_pattern(
                    slug,
                    repeat=call.data["repeat"],
                    low_speed_percent=call.data["low_speed"],
                    high_speed_percent=call.data["high_speed"],
                    low_duration=call.data["low_duration"],
                    high_duration=call.data["high_duration"],
                )

        async def _async_handle_stop_pattern(call: ServiceCall) -> None:
            for device_id in call.data["device_id"]:
                resolved = _resolve_device(hass, device_id)
                if resolved is None:
                    continue
                target_coordinator, slug = resolved
                await target_coordinator.async_stop_pattern(slug)

        hass.services.async_register(
            DOMAIN, SERVICE_START_WAVE_PATTERN, _async_handle_start_wave_pattern, schema=START_WAVE_PATTERN_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, SERVICE_START_PULSE_PATTERN, _async_handle_start_pulse_pattern, schema=START_PULSE_PATTERN_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, SERVICE_STOP_PATTERN, _async_handle_stop_pattern, schema=STOP_PATTERN_SCHEMA
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: IntifaceCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown_client()

        if not hass.data.get(DOMAIN):
            hass.services.async_remove(DOMAIN, SERVICE_START_WAVE_PATTERN)
            hass.services.async_remove(DOMAIN, SERVICE_START_PULSE_PATTERN)
            hass.services.async_remove(DOMAIN, SERVICE_STOP_PATTERN)

    return unload_ok
