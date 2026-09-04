"""Tests for the config flow: entering an Intiface URL, connection
testing, and duplicate-entry prevention."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.intiface_control.const import CONF_URL, DOMAIN


@pytest.mark.asyncio
async def test_successful_setup_creates_entry(hass) -> None:
    with patch(
        "custom_components.intiface_control.config_flow._test_connection",
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
        "custom_components.intiface_control.config_flow._test_connection",
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
        "custom_components.intiface_control.config_flow._test_connection",
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


@pytest.mark.asyncio
async def test_options_flow_updates_url_and_reloads(hass) -> None:
    """The options flow (Settings → Devices & services → Configure)
    lets the URL be changed after initial setup without removing and
    re-adding the integration."""
    with patch(
        "custom_components.intiface_control.config_flow._test_connection",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: "ws://old:12345"}
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        # The flow already auto-sets-up a newly created entry — an
        # explicit async_setup() call here would hit HA's own
        # OperationNotAllowed guard against setting up an already-loaded
        # entry, so just wait for that automatic setup to finish.
        await hass.async_block_till_done()

        options_result = await hass.config_entries.options.async_init(entry.entry_id)
        assert options_result["type"] == FlowResultType.FORM
        assert options_result["step_id"] == "init"

        options_result = await hass.config_entries.options.async_configure(
            options_result["flow_id"], {CONF_URL: "ws://new:12345"}
        )
        await hass.async_block_till_done()

    assert entry.data[CONF_URL] == "ws://new:12345"


@pytest.mark.asyncio
async def test_options_flow_connection_failure_leaves_url_unchanged(hass) -> None:
    with patch(
        "custom_components.intiface_control.config_flow._test_connection",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: "ws://old:12345"}
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        # The flow already auto-sets-up a newly created entry — an
        # explicit async_setup() call here would hit HA's own
        # OperationNotAllowed guard against setting up an already-loaded
        # entry, so just wait for that automatic setup to finish.
        await hass.async_block_till_done()

        options_result = await hass.config_entries.options.async_init(entry.entry_id)

    with patch(
        "custom_components.intiface_control.config_flow._test_connection",
        new=AsyncMock(side_effect=ConnectionError("nope")),
    ):
        options_result = await hass.config_entries.options.async_configure(
            options_result["flow_id"], {CONF_URL: "ws://bad:12345"}
        )

    assert options_result["type"] == FlowResultType.FORM
    assert options_result["errors"] == {"base": "cannot_connect"}
    assert entry.data[CONF_URL] == "ws://old:12345", "a failed connection test must not change the stored URL"
