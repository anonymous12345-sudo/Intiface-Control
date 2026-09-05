"""End-to-end entity tests: sets up the full integration (coordinator +
all platforms) and checks actual Home Assistant entity states, rather
than just coordinator-internal data. Entity IDs used here match what was
empirically confirmed against a real Home Assistant instance during
development (e.g. binary_sensor.hismith_sex_machine_connected,
switch.stop_all_toys) — not guessed.
"""

from __future__ import annotations

import asyncio

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.intiface_control import client as bp
from custom_components.intiface_control.const import CONF_FALLBACK_URL, CONF_URL, DOMAIN


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
    await coordinator.async_refresh()
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
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.lovense_hush_connected").state == "on"

    coordinator._bp_client.devices = {}
    await coordinator.async_refresh()
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
    await coordinator.async_refresh()
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
    await coordinator.async_refresh()
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


@pytest.mark.asyncio
async def test_enable_switch_defaults_on(hass, setup_entry, fake_device) -> None:
    coordinator = hass.data[DOMAIN][setup_entry.entry_id]
    coordinator._bp_client.devices = {0: fake_device("Lovense Hush", outputs={bp.VIBRATE})}
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("switch.lovense_hush_enabled").state == "on"


@pytest.mark.asyncio
async def test_disabling_one_device_does_not_affect_another(hass, setup_entry, fake_device) -> None:
    coordinator = hass.data[DOMAIN][setup_entry.entry_id]
    hush = fake_device("Lovense Hush", outputs={bp.VIBRATE})
    hismith = fake_device("Hismith Sex Machine", outputs={bp.OSCILLATE})
    coordinator._bp_client.devices = {0: hush, 1: hismith}
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.lovense_hush_intensity", "value": 60},
        blocking=True,
    )
    hush.sent.clear()

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.lovense_hush_enabled"}, blocking=True
    )

    assert hass.states.get("switch.lovense_hush_enabled").state == "off"
    assert hush.sent[-1] == ("STOP", None)
    assert hass.states.get("number.lovense_hush_intensity").state == "0.0"

    # The other device, and the global stop switch, are untouched.
    assert hass.states.get("switch.hismith_sex_machine_enabled").state == "on"
    assert hass.states.get("switch.stop_all_toys").state == "off"

    # The disabled device refuses commands...
    hush.sent.clear()
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.lovense_hush_intensity", "value": 80},
        blocking=True,
    )
    assert hush.sent == []

    # ...while the other device keeps working normally.
    ok = await coordinator.async_apply_intensity("hismith_sex_machine", 45)
    assert ok is True

    # Re-enabling restores normal operation.
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.lovense_hush_enabled"}, blocking=True
    )
    assert hass.states.get("switch.lovense_hush_enabled").state == "on"
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.lovense_hush_intensity", "value": 30},
        blocking=True,
    )
    assert hush.sent[-1] == (bp.VIBRATE, (0.3,))


@pytest.mark.asyncio
async def test_wave_pattern_service_via_real_device_registry(hass, setup_entry, fake_device) -> None:
    """End-to-end confirmation of the device-target mechanism (not just
    the coordinator-level tests in test_coordinator.py): looks the real
    device_id up from Home Assistant's own device registry — the same
    way the frontend's device picker would supply it — and calls the
    service exactly as a user/automation would, with no direct
    coordinator access at all."""
    from homeassistant.helpers import device_registry as dr

    coordinator = hass.data[DOMAIN][setup_entry.entry_id]
    hush = fake_device("Lovense Hush", outputs={bp.VIBRATE})
    coordinator._bp_client.devices = {0: hush}
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    registry = dr.async_get(hass)
    device_entry = registry.async_get_device(identifiers={(DOMAIN, f"{setup_entry.entry_id}_lovense_hush")})
    assert device_entry is not None, "the device registry should already have this toy's device"

    await hass.services.async_call(
        DOMAIN, "start_wave_pattern",
        {"device_id": [device_entry.id], "repeat": 1, "min_speed": 10, "max_speed": 90, "duration": 1},
        blocking=True,
    )
    await asyncio.sleep(1.3)

    speeds = [v[0] for _, v in hush.sent[:-1]]
    assert speeds[0] == pytest.approx(0.1, abs=0.01)
    assert max(speeds) > 0.85
    assert hush.sent[-1] == ("STOP", None)


@pytest.mark.asyncio
async def test_position_duration_slider_is_used_by_position_slider(hass, setup_entry, fake_device) -> None:
    coordinator = hass.data[DOMAIN][setup_entry.entry_id]
    dev = fake_device("Simulated Stroker", outputs={bp.POSITION, bp.POSITION_WITH_DURATION})
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get("number.simulated_stroker_position_duration").state == "0.0"

    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.simulated_stroker_position_duration", "value": 1.5},
        blocking=True,
    )
    assert hass.states.get("number.simulated_stroker_position_duration").state == "1.5"

    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": "number.simulated_stroker_position", "value": 70},
        blocking=True,
    )

    assert dev.sent[-1] == (bp.POSITION_WITH_DURATION, (0.7, 1500))


@pytest.mark.asyncio
async def test_led_light_entity_end_to_end(hass, setup_entry, fake_device) -> None:
    if bp.LED is None:
        pytest.skip("installed buttplug library doesn't expose Led yet")
    coordinator = hass.data[DOMAIN][setup_entry.entry_id]
    dev = fake_device("LED Toy", outputs={bp.LED})
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get("light.led_toy_led")
    assert state is not None
    assert state.state == "off"

    await hass.services.async_call(
        "light", "turn_on",
        {"entity_id": "light.led_toy_led", "brightness": 200},
        blocking=True,
    )
    assert hass.states.get("light.led_toy_led").state == "on"
    assert dev.sent[-1][0] == bp.LED

    await hass.services.async_call(
        "light", "turn_off",
        {"entity_id": "light.led_toy_led"},
        blocking=True,
    )
    assert hass.states.get("light.led_toy_led").state == "off"
