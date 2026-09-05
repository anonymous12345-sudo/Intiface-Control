"""Shared pytest fixtures for the Intiface Control test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def enable_custom_integrations(enable_custom_integrations):
    """Required by pytest-homeassistant-custom-component: Home Assistant
    refuses to load custom_components during tests unless this fixture is
    active. It's provided by the plugin itself; re-declaring it here as
    autouse just saves repeating `enable_custom_integrations` as an
    argument in every single test function."""
    yield


class FakeDeviceOutputCommand:
    """Stand-in for buttplug's real DeviceOutputCommand, used only so
    FakeDevice.run_output() below can inspect what was sent.

    Deliberately NOT the real class: our own client.py code never reads
    attributes back off a DeviceOutputCommand instance (it just builds one
    and hands it to dev.run_output()), so there was never a real
    contract to test against there — .output_type/.values here are purely
    our own test-observability convention, unrelated to whatever
    attribute names the actual buttplug library happens to use
    internally."""

    def __init__(self, output_type, *values):
        self.output_type = output_type
        self.values = values


@pytest.fixture(autouse=True)
def _patch_device_output_command(monkeypatch):
    """Every test that constructs a DeviceOutputCommand goes through
    client.py's own reference to it, so patching it there is enough —
    other modules never import DeviceOutputCommand directly."""
    from custom_components.intiface_control import client as bp_client

    monkeypatch.setattr(bp_client, "DeviceOutputCommand", FakeDeviceOutputCommand)


class FakeDevice:
    """A minimal stand-in for a buttplug device object, exposing exactly
    the surface our code touches (has_output/has_input/run_output/stop/
    battery/rssi/pressure/message_timing_gap), so tests don't need a
    real Intiface server or real hardware. Uses the REAL
    OutputType/InputType enum values from the installed `buttplug`
    package (not re-invented fake ones) so tests also catch enum-name
    mismatches — the same class of bug that once broke
    PositionWithDuration detection."""

    def __init__(
        self,
        name: str,
        outputs=None,
        battery: float | None = None,
        rssi: float | None = None,
        pressure: float | None = None,
        message_timing_gap: int | None = None,
    ):
        self.name = name
        self.index = 0
        self._outputs = set(outputs or [])
        self._battery = battery
        self._rssi = rssi
        self._pressure = pressure
        self.message_timing_gap = message_timing_gap
        self.sent: list = []

    def has_output(self, output_type) -> bool:
        return output_type in self._outputs

    def has_input(self, input_type) -> bool:
        from custom_components.intiface_control import client as bp_client

        if input_type == bp_client.BATTERY_INPUT:
            return self._battery is not None
        if input_type == bp_client.RSSI_INPUT:
            return self._rssi is not None
        if input_type == bp_client.PRESSURE_INPUT:
            return self._pressure is not None
        return False

    async def run_output(self, cmd) -> None:
        if cmd.output_type not in self._outputs:
            raise RuntimeError(f"{self.name} does not support {cmd.output_type}")
        self.sent.append((cmd.output_type, cmd.values))

    async def stop(self) -> None:
        self.sent.append(("STOP", None))

    async def battery(self):
        return self._battery

    async def rssi(self):
        return self._rssi

    async def pressure(self):
        return self._pressure


@pytest.fixture
def fake_device():
    """Factory fixture: fake_device("Name", outputs={...}, battery=0.9)."""
    return FakeDevice

