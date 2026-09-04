"""The Intiface Control integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import ButtplugCoordinator

PLATFORMS = ["number", "binary_sensor", "sensor", "switch"]

SERVICE_START_PATTERN = "start_pattern"
SERVICE_STOP_PATTERN = "stop_pattern"

START_PATTERN_SCHEMA = vol.Schema(
    {
        vol.Required("target"): cv.string,
        vol.Required("pattern"): vol.In(["wave", "pulse"]),
        vol.Optional("duration", default=60): vol.All(vol.Coerce(int), vol.Range(min=1, max=3600)),
        vol.Optional("max_speed", default=50): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
    }
)

STOP_PATTERN_SCHEMA = vol.Schema(
    {
        vol.Required("target"): cv.string,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = ButtplugCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.services.has_service(DOMAIN, SERVICE_START_PATTERN):

        async def _async_handle_start_pattern(call: ServiceCall) -> None:
            for coord in hass.data.get(DOMAIN, {}).values():
                await coord.async_start_pattern(
                    call.data["target"],
                    call.data["pattern"],
                    call.data["duration"],
                    call.data["max_speed"],
                )

        async def _async_handle_stop_pattern(call: ServiceCall) -> None:
            for coord in hass.data.get(DOMAIN, {}).values():
                await coord.async_stop_pattern(call.data["target"])

        hass.services.async_register(
            DOMAIN, SERVICE_START_PATTERN, _async_handle_start_pattern, schema=START_PATTERN_SCHEMA
        )
        hass.services.async_register(
            DOMAIN, SERVICE_STOP_PATTERN, _async_handle_stop_pattern, schema=STOP_PATTERN_SCHEMA
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: ButtplugCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown_client()

        if not hass.data.get(DOMAIN):
            hass.services.async_remove(DOMAIN, SERVICE_START_PATTERN)
            hass.services.async_remove(DOMAIN, SERVICE_STOP_PATTERN)

    return unload_ok
