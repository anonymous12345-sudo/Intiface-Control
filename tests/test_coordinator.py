"""Tests for ButtplugCoordinator: device discovery, the emergency-stop
gate, pattern handling, and the cancellation race-condition fix.

The real `buttplug.ButtplugClient` tries to open an actual WebSocket
connection on .connect() — no good for unit tests. We monkeypatch it
with a FakeButtplugClient here, and pre-populate `coordinator._bp_client`
directly (bypassing the connect/scan flow, including its real 2-second
scan-window sleep) so tests stay fast.
"""

from __future__ import annotations

import asyncio

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.buttplug import client as bp
from custom_components.buttplug.const import CONF_FALLBACK_URL, CONF_URL, DOMAIN
from custom_components.buttplug.coordinator import ButtplugCoordinator


class FakeButtplugClient:
    """Replaces the real ButtplugClient for tests: no real connection,
    devices are whatever the test puts in `.devices`."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.devices: dict = {}

    async def connect(self, url: str) -> None:
        pass

    async def start_scanning(self) -> None:
        pass

    async def stop_scanning(self) -> None:
        pass


@pytest.fixture
def mock_bp_client(monkeypatch):
    monkeypatch.setattr(bp, "ButtplugClient", FakeButtplugClient)


@pytest.fixture
async def coordinator(hass, mock_bp_client):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: "ws://fake:12345", CONF_FALLBACK_URL: None},
    )
    entry.add_to_hass(hass)
    coord = ButtplugCoordinator(hass, entry)
    # Bypass the real connect/scan flow (and its 2s scan-window sleep) —
    # tests only care about behaviour once a connection already exists.
    coord._bp_client = FakeButtplugClient("test")
    return coord


@pytest.mark.asyncio
async def test_first_refresh_discovers_devices_and_capabilities(coordinator, fake_device) -> None:
    coordinator._bp_client.devices = {
        0: fake_device("Lovense Hush", outputs={bp.VIBRATE}, battery=0.9),
    }
    await coordinator.async_config_entry_first_refresh()

    assert "lovense_hush" in coordinator.data
    assert coordinator.data["lovense_hush"]["capabilities"] == ["vibrate", "battery"]
    assert coordinator.data["lovense_hush"]["battery"] == 90.0


@pytest.mark.asyncio
async def test_new_device_listener_fires_for_devices_added_later(coordinator, fake_device) -> None:
    await coordinator.async_config_entry_first_refresh()

    seen: list = []
    coordinator.add_new_device_listener(lambda batch: seen.append(batch))

    coordinator._bp_client.devices[1] = fake_device("Brand New Toy", outputs={bp.ROTATE})
    await coordinator.async_request_refresh()

    assert len(seen) == 1
    assert seen[0][0][0] == "brand_new_toy"


@pytest.mark.asyncio
async def test_offline_device_disappears_from_data_but_isnt_an_error(coordinator, fake_device) -> None:
    coordinator._bp_client.devices = {0: fake_device("Lovense Hush", outputs={bp.VIBRATE})}
    await coordinator.async_config_entry_first_refresh()
    assert "lovense_hush" in coordinator.data

    coordinator._bp_client.devices = {}
    await coordinator.async_request_refresh()
    assert "lovense_hush" not in coordinator.data
    # A device disappearing is a legitimate "toy went offline" state —
    # entities should show unavailable, this is not treated as an error.
    assert coordinator.get_device("lovense_hush") is None


@pytest.mark.asyncio
async def test_apply_intensity_on_offline_device_returns_false_not_error(coordinator) -> None:
    await coordinator.async_config_entry_first_refresh()
    ok = await coordinator.async_apply_intensity("does_not_exist", 50)
    assert ok is False


@pytest.mark.asyncio
async def test_emergency_stop_blocks_further_commands(coordinator, fake_device) -> None:
    """The actual bug reported during development: the stop switch used
    to only perform a one-off stop, without blocking anything sent
    afterwards. This is the regression test for that fix."""
    coordinator._bp_client.devices = {0: fake_device("Lovense Hush", outputs={bp.VIBRATE})}
    await coordinator.async_config_entry_first_refresh()

    await coordinator.async_stop_all()
    assert coordinator.stopped is True

    ok = await coordinator.async_apply_intensity("lovense_hush", 55)
    assert ok is False, "command should have been refused while stopped"

    coordinator.async_clear_stop()
    assert coordinator.stopped is False

    ok = await coordinator.async_apply_intensity("lovense_hush", 55)
    assert ok is True, "command should work again once the gate is cleared"


@pytest.mark.asyncio
async def test_stop_all_notifies_stop_listeners(coordinator, fake_device) -> None:
    coordinator._bp_client.devices = {0: fake_device("Lovense Hush", outputs={bp.VIBRATE})}
    await coordinator.async_config_entry_first_refresh()

    calls = []
    coordinator.add_stop_listener(lambda: calls.append(True))
    await coordinator.async_stop_all()
    assert calls == [True]


@pytest.mark.asyncio
async def test_pattern_cancellation_completes_before_next_command_is_sent(coordinator, fake_device) -> None:
    """Regression test for a real race condition found during
    development: cancelling a running pattern only *requests*
    cancellation — the pattern's own cleanup (which stops the device)
    runs asynchronously afterwards. Without waiting for that cleanup to
    finish before sending a new command, a directly-set intensity value
    could get silently overwritten by the old pattern's delayed stop.
    This asserts the fix: the explicit command is always the last thing
    sent, with nothing arriving after it."""
    dev = fake_device("Lovense Hush", outputs={bp.VIBRATE})
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_config_entry_first_refresh()

    await coordinator.async_start_pattern("lovense_hush", "wave", duration=10, max_speed_percent=90)
    await asyncio.sleep(0.3)

    await coordinator.async_apply_intensity("lovense_hush", 42)
    assert dev.sent[-1] == (bp.VIBRATE, (0.42,))

    n_after = len(dev.sent)
    await asyncio.sleep(0.5)
    assert len(dev.sent) == n_after, "nothing should have been sent after the explicit command"


@pytest.mark.asyncio
async def test_pattern_start_and_stop(coordinator, fake_device) -> None:
    dev = fake_device("Lovense Hush", outputs={bp.VIBRATE})
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_config_entry_first_refresh()

    await coordinator.async_start_pattern("lovense_hush", "wave", duration=1, max_speed_percent=80)
    await asyncio.sleep(1.3)

    assert len(dev.sent) > 3, "expected several commands during a 1s wave pattern"
    assert dev.sent[-1] == ("STOP", None), "pattern should stop the device when it finishes"
