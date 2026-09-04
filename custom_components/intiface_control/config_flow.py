"""Config flow for the Intiface Control integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
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


async def _test_connection(url: str) -> None:
    client = bp.ButtplugClient(CLIENT_NAME)
    await client.connect(url)
    if hasattr(client, "disconnect"):
        await client.disconnect()


class ButtplugConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Intiface Control."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_URL].strip()
            fallback = (user_input.get(CONF_FALLBACK_URL) or "").strip()

            try:
                await _test_connection(url)
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
