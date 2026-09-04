"""End-to-end entity tests: sets up the full integration (coordinator +
all platforms) and checks actual Home Assistant entity states, rather
than just coordinator-internal data. Entity IDs used here match what was
empirically confirmed against a real Home Assistant instance during
development (e.g. binary_sensor.hismith_sex_machine_connected,
switch.stop_all_toys) — not guessed.
"""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.buttplug import client as bp
from custom_components.buttplug.const import CONF_FALLBACK_URL, CONF_URL, DOMAIN


class FakeButtplugClient:
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
async def setup_entry(hass, mock_bp_client):
    """Sets up the integration with zero devices initially (the fake
    client starts empty) — tests add devices afterwards and trigger a
    refresh, mirroring how a toy connecting after HA has already started
    works in real usage."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_URL: "ws://fake:12345", CONF_FALLBACK_URL: None}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.mark.asyncio
async def test_entities_created_match_device_capabilities(hass, setup_entry, fake_device) -> None:
    coordinator = hass.data[DOMAIN][setup_entry.entry_id]
    coordinator._bp_client.devices = {
        0: fake_device("Lovense Hush", outputs={bp.VIBRATE}, battery=0.9),
        1: fake_device("Simulated Stroker", outputs={bp.POSITION}),
    }
    await coordinator.async_request_refresh()
    await hass.async_block_till_done()

    # Hush: intensity + battery + connected, no position
    assert hass.states.get("number.lovense_hush_intensity") is not None
    assert hass.states.get("sensor.lovense_hush_battery") is not None
    assert hass.states.get("binary_sensor.lovense_hush_connected") is not None
    assert hass.states.get("number.lovense_hush_position") is None

    # Stroker: position, no intensity, no battery
    assert hass.states.get("number.simulated_stroker_position") is not None
    assert hass.states.get("number.simulated_stroker_intensity") is None
    assert hass.states.get("sensor.simulated_stroker_battery") is None


@pytest.mark.asyncio
async def test_offline_toy_goes_unavailable_but_entities_stay(hass, setup_entry, fake_device) -> None:
    coordinator = hass.data[DOMAIN][setup_entry.entry_id]
    coordinator._bp_client.devices = {0: fake_device("Lovense Hush", outputs={bp.VIBRATE})}
    await coordinator.async_request_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.lovense_hush_connected").state == "on"

    coordinator._bp_client.devices = {}
    await coordinator.async_request_refresh()
    await hass.async_block_till_done()

    # Entities still present — not removed — just reflecting offline.
    connected = hass.states.get("binary_sensor.lovense_hush_connected")
    assert connected is not None
    assert connected.state == "off"

    intensity = hass.states.get("number.lovense_hush_intensity")
    assert intensity is not None
    assert intensity.state == "unavailable"


@pytest.mark.asyncio
async def test_stop_switch_resets_slider_to_zero(hass, setup_entry, fake_device) -> None:
    coordinator = hass.data[DOMAIN][setup_entry.entry_id]
    coordinator._bp_client.devices = {0: fake_device("Lovense Hush", outputs={bp.VIBRATE})}
    await coordinator.async_request_refresh()
    await hass.async_block_till_done()

    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.lovense_hush_intensity", "value": 65},
        blocking=True,
    )
    assert hass.states.get("number.lovense_hush_intensity").state == "65.0"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.stop_all_toys"}, blocking=True
    )

    assert hass.states.get("number.lovense_hush_intensity").state == "0.0"
    assert hass.states.get("switch.stop_all_toys").state == "on"


@pytest.mark.asyncio
async def test_stop_switch_blocks_slider_commands_while_on(hass, setup_entry, fake_device) -> None:
    coordinator = hass.data[DOMAIN][setup_entry.entry_id]
    dev = fake_device("Lovense Hush", outputs={bp.VIBRATE})
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_request_refresh()
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.stop_all_toys"}, blocking=True
    )
    dev.sent.clear()

    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.lovense_hush_intensity", "value": 70},
        blocking=True,
    )
    assert dev.sent == [], "no command should reach the device while the stop switch is on"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.stop_all_toys"}, blocking=True
    )
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.lovense_hush_intensity", "value": 70},
        blocking=True,
    )
    assert dev.sent == [(bp.VIBRATE, (0.7,))], "command should work again once unstopped"
