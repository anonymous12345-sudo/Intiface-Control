"""Config flow for the Intiface Control integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from . import client as bp
from .const import CLIENT_NAME, CONF_FALLBACK_URL, CONF_URL, DEFAULT_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL, default=DEFAULT_URL): str,
        vol.Optional(CONF_FALLBACK_URL): str,
    }
)


async def _try_connect(url: str) -> None:
    """Confirms `url` is a reachable Intiface server, without any side
    effect on whatever devices are currently running elsewhere.

    Deliberately does NOT call client.disconnect() — the real
    buttplug-py client's disconnect() unconditionally calls
    stop_all_devices() first, which sends a genuinely server-wide
    StopCmd (no device_index — every device on the server, not scoped
    to this client's own session). Confirmed directly against the
    library source:
    https://github.com/buttplugio/buttplug-py/blob/e21318f/src/buttplug/client.py#L294-L318
    Since Intiface manages device connections centrally (shared across
    every client connected to it), that would stop every toy currently
    running via this integration's own already-connected coordinator —
    a "just testing a URL" action should never be able to do that.

    Closes the underlying WebSocket connector directly instead — a
    private attribute, but the only way to close cleanly without that
    side effect with this library version. If that attribute isn't
    there (a future library version restructured internals), this
    leaves the test connection open rather than falling back to the
    public disconnect() — a lingering open connection is a far smaller
    problem than stopping someone's toy mid-use."""
    client = bp.ButtplugClient(CLIENT_NAME)
    await client.connect(url)
    connector = getattr(client, "_connector", None)
    if connector is not None and hasattr(connector, "disconnect"):
        try:
            await connector.disconnect()
        except Exception:
            _LOGGER.debug("Error closing test connection", exc_info=True)


async def _test_connection(url: str, fallback_url: str | None = None) -> None:
    """Tries `url` first, then `fallback_url` if one is given —
    matching IntifaceCoordinator._ensure_client()'s own runtime
    fallback behaviour exactly (see coordinator.py). Without this,
    setup could reject a primary/fallback pair that would actually work
    fine once the integration is running (primary temporarily down,
    fallback reachable) — an inconsistency between what setup allows
    and what runtime actually does. If both fail, raises the primary
    URL's own error, not the fallback's, since that's the field
    visibly showing the error in the form."""
    try:
        await _try_connect(url)
        return
    except Exception:
        if fallback_url:
            try:
                await _try_connect(fallback_url)
                return
            except Exception:
                _LOGGER.debug("Fallback connection test also failed", exc_info=True)
        raise


class IntifaceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Intiface Control."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_URL].strip()
            fallback = (user_input.get(CONF_FALLBACK_URL) or "").strip()

            try:
                await _test_connection(url, fallback or None)
            except Exception:
                _LOGGER.debug("Connection test failed", exc_info=True)
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(url)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Intiface Control",
                    data={CONF_URL: url, CONF_FALLBACK_URL: fallback or None},
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> IntifaceOptionsFlow:
        return IntifaceOptionsFlow()


class IntifaceOptionsFlow(config_entries.OptionsFlow):
    """Lets the user change the Intiface URL (or fallback URL) after
    initial setup, without removing and re-adding the integration —
    which would otherwise lose entity customizations, dashboard
    references, and area/device assignments tied to the old entry.

    Deliberately doesn't store `config_entry` in __init__: recent Home
    Assistant versions populate `self.config_entry` automatically once
    the flow starts, and manually assigning it is both unnecessary and
    soft-deprecated."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_URL].strip()
            fallback = (user_input.get(CONF_FALLBACK_URL) or "").strip()

            # A config entry's unique_id is the URL it was originally set
            # up with (see IntifaceConfigFlow.async_step_user above). If
            # we update this entry's data/URL without also updating its
            # unique_id, this entry keeps pointing at a URL that no
            # longer matches its own unique_id — meaning a *second*
            # config entry could later be added for that same new URL
            # without Home Assistant recognizing it as a duplicate, and
            # now two entries (two coordinators, two full sets of
            # entities) both talk to the same Intiface server. Guard
            # against that directly: refuse if some *other* entry
            # already claims this URL as its unique_id.
            for other_entry in self.hass.config_entries.async_entries(DOMAIN):
                if other_entry.entry_id != self.config_entry.entry_id and other_entry.unique_id == url:
                    errors["base"] = "already_configured"
                    break

            if not errors:
                try:
                    await _test_connection(url, fallback or None)
                except Exception:
                    _LOGGER.debug("Connection test failed", exc_info=True)
                    errors["base"] = "cannot_connect"
                else:
                    new_data = {
                        **self.config_entry.data,
                        CONF_URL: url,
                        CONF_FALLBACK_URL: fallback or None,
                    }
                    self.hass.config_entries.async_update_entry(
                        self.config_entry, data=new_data, unique_id=url
                    )
                    await self.hass.config_entries.async_reload(self.config_entry.entry_id)
                    # Completing an options flow REPLACES config_entry.options
                    # wholesale with whatever's passed here — not a merge.
                    # This flow only ever touches entry.data (the URL) and
                    # never reads or writes entry.options itself, but other
                    # state does live there (position-duration preferences,
                    # see IntifaceCoordinator.async_set_position_duration()).
                    # Passing {} here would silently wipe that out the next
                    # time anything reloads or restarts, even though this
                    # flow never touched it — echo back whatever's already
                    # there instead of blowing it away.
                    return self.async_create_entry(title="", data=dict(self.config_entry.options))

        current = self.config_entry.data
        schema = vol.Schema(
            {
                vol.Required(CONF_URL, default=current.get(CONF_URL, DEFAULT_URL)): str,
                vol.Optional(CONF_FALLBACK_URL, default=current.get(CONF_FALLBACK_URL) or ""): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

