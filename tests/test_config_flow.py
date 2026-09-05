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


@pytest.mark.asyncio
async def test_options_flow_updates_unique_id_along_with_url(hass) -> None:
    """Regression test: the options flow used to update entry.data's URL
    without also updating the entry's own unique_id (which is set to the
    URL at initial setup). Left unfixed, a second config entry could
    later be created for that same new URL without Home Assistant
    recognizing it as a duplicate — two entries, two coordinators, both
    talking to the same Intiface server."""
    with patch(
        "custom_components.intiface_control.config_flow._test_connection",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: "ws://old:12345"}
        )
        entry = hass.config_entries.async_entries(DOMAIN)[0]
        await hass.async_block_till_done()
        assert entry.unique_id == "ws://old:12345"

        options_result = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            options_result["flow_id"], {CONF_URL: "ws://new:12345"}
        )
        await hass.async_block_till_done()

    assert entry.unique_id == "ws://new:12345", "unique_id must move with the URL, not stay pointed at the old one"

    # With unique_id correctly updated, trying to add a fresh entry for
    # the URL this one just gave up now succeeds normally (nothing else
    # claims it) — confirming the old URL was actually released, not
    # just duplicated.
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
    assert result["type"] == FlowResultType.CREATE_ENTRY


@pytest.mark.asyncio
async def test_options_flow_refuses_url_already_used_by_another_entry(hass) -> None:
    with patch(
        "custom_components.intiface_control.config_flow._test_connection",
        new=AsyncMock(return_value=None),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: "ws://first:12345"}
        )
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_URL: "ws://second:12345"}
        )
        await hass.async_block_till_done()

        entries = hass.config_entries.async_entries(DOMAIN)
        second_entry = next(e for e in entries if e.unique_id == "ws://second:12345")

        options_result = await hass.config_entries.options.async_init(second_entry.entry_id)
        # Trying to point the second entry at the URL the first entry
        # already owns must be refused, not silently create a duplicate.
        options_result = await hass.config_entries.options.async_configure(
            options_result["flow_id"], {CONF_URL: "ws://first:12345"}
        )

    assert options_result["type"] == FlowResultType.FORM
    assert options_result["errors"] == {"base": "already_configured"}
    assert second_entry.data[CONF_URL] == "ws://second:12345", "the refused change must not have been applied"


@pytest.mark.asyncio
async def test_setup_succeeds_if_only_the_fallback_url_is_reachable(hass) -> None:
    """Regression test: the config flow used to test only the primary
    URL, even though the coordinator's own runtime connection logic
    (IntifaceCoordinator._ensure_client()) tries the fallback URL too.
    A primary/fallback pair that would work fine once running used to
    be rejected at setup time — an inconsistency between what setup
    allows and what runtime actually does."""

    async def fake_test_connection(url, fallback_url=None):
        if "unreachable" in url:
            if fallback_url and "unreachable" not in fallback_url:
                return
            raise ConnectionError("nope")

    with patch(
        "custom_components.intiface_control.config_flow._test_connection",
        new=AsyncMock(side_effect=fake_test_connection),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_URL: "ws://unreachable:12345", "fallback_url": "ws://192.168.1.50:12345"},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
