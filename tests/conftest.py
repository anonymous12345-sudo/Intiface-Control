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


class FakeDevice:
    """A minimal stand-in for a buttplug device object, exposing exactly
    the surface our code touches (has_output/has_input/run_output/stop/
    battery), so tests don't need a real Intiface server or real
    hardware. Uses the REAL OutputType/InputType enum values from the
    installed `buttplug` package (not re-invented fake ones) so tests
    also catch enum-name mismatches — the same class of bug that once
    broke PositionWithDuration detection."""

    def __init__(self, name: str, outputs=None, battery: float | None = None):
        self.name = name
        self.index = 0
        self._outputs = set(outputs or [])
        self._battery = battery
        self.sent: list = []

    def has_output(self, output_type) -> bool:
        return output_type in self._outputs

    def has_input(self, input_type) -> bool:
        from custom_components.buttplug import client as bp_client

        return input_type == bp_client.BATTERY_INPUT and self._battery is not None

    async def run_output(self, cmd) -> None:
        if cmd.output_type not in self._outputs:
            raise RuntimeError(f"{self.name} does not support {cmd.output_type}")
        self.sent.append((cmd.output_type, cmd.values))

    async def stop(self) -> None:
        self.sent.append(("STOP", None))

    async def battery(self):
        return self._battery


@pytest.fixture
def fake_device():
    """Factory fixture: fake_device("Name", outputs={...}, battery=0.9)."""
    return FakeDevice
