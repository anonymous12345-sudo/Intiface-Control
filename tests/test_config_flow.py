"""Tests for the config flow: entering an Intiface URL, connection
testing, and duplicate-entry prevention."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.buttplug.const import CONF_URL, DOMAIN


@pytest.mark.asyncio
async def test_successful_setup_creates_entry(hass) -> None:
    with patch(
        "custom_components.buttplug.config_flow._test_connection",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: "ws://192.168.1.50:12345"}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_URL] == "ws://192.168.1.50:12345"


@pytest.mark.asyncio
async def test_connection_failure_shows_error(hass) -> None:
    with patch(
        "custom_components.buttplug.config_flow._test_connection",
        new=AsyncMock(side_effect=ConnectionError("nope")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: "ws://unreachable:12345"}
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


@pytest.mark.asyncio
async def test_duplicate_url_is_rejected(hass) -> None:
    with patch(
        "custom_components.buttplug.config_flow._test_connection",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: "ws://192.168.1.50:12345"}
        )

        # Same URL again — should abort as already configured.
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: "ws://192.168.1.50:12345"}
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
