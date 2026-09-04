"""Tests for custom_components/buttplug/client.py — the buttplug-library
wrapper (capability detection, sending commands, battery reads).

These import the REAL `buttplug` package (installed as a normal test
dependency, see requirements_test.txt) rather than a hand-rolled fake
module. That matters: the whole point of several of these tests is to
catch a mismatch between our enum-name-guessing logic and whatever the
actual installed library calls things — exactly the class of bug that
once broke PositionWithDuration detection (see test_enum_resolution
below). A fake module with names we invented ourselves could never have
caught that.
"""

from __future__ import annotations

import pytest

from custom_components.buttplug import client as bp


def test_enum_resolution_finds_every_known_output_type() -> None:
    """Regression test for the original bug: _enum() used to do a naive
    name.upper() to match library member names, which silently failed for
    multi-word names (e.g. "PositionWithDuration" -> "POSITIONWITHDURATION"
    instead of "POSITION_WITH_DURATION"), so POSITION_WITH_DURATION ended
    up None even though the library supported it fine. This asserts every
    output type we know about actually resolves to something."""
    assert bp.OSCILLATE is not None
    assert bp.VIBRATE is not None
    assert bp.ROTATE is not None
    assert bp.CONSTRICT is not None
    assert bp.POSITION is not None
    assert bp.POSITION_WITH_DURATION is not None, (
        "POSITION_WITH_DURATION resolved to None — this is exactly the "
        "regression that once silently broke timed stroker moves."
    )


def test_device_slug_basic() -> None:
    dev = type("Dev", (), {"name": "Lovense Hush"})()
    assert bp.device_slug(dev) == "lovense_hush"


def test_device_slug_handles_punctuation_and_case() -> None:
    dev = type("Dev", (), {"name": "Hismith Sex Machine!!"})()
    assert bp.device_slug(dev) == "hismith_sex_machine"


def test_device_slug_falls_back_for_empty_name() -> None:
    dev = type("Dev", (), {"name": ""})()
    assert bp.device_slug(dev) == "device"


def test_get_capabilities_for_unknown_brand_new_device(fake_device) -> None:
    """A device this codebase has never heard of, exposing only Rotate,
    should still be correctly identified — this is the entire point of
    doing capability introspection instead of hardcoding brand names."""
    dev = fake_device("Totally New Toy Brand", outputs={bp.ROTATE})
    assert bp.get_capabilities(dev) == ["rotate"]


def test_get_capabilities_includes_battery_when_present(fake_device) -> None:
    dev = fake_device("Lovense Hush", outputs={bp.VIBRATE}, battery=0.9)
    caps = bp.get_capabilities(dev)
    assert "vibrate" in caps
    assert "battery" in caps


def test_get_capabilities_excludes_battery_when_absent(fake_device) -> None:
    dev = fake_device("Hismith Sex Machine", outputs={bp.OSCILLATE}, battery=None)
    caps = bp.get_capabilities(dev)
    assert "oscillate" in caps
    assert "battery" not in caps


def test_intensity_output_type_picks_supported_type(fake_device) -> None:
    dev = fake_device("Rotator", outputs={bp.ROTATE})
    assert bp.intensity_output_type(dev) == bp.ROTATE


@pytest.mark.asyncio
async def test_apply_intensity_routes_to_the_right_output(fake_device) -> None:
    dev = fake_device("Rotator", outputs={bp.ROTATE})
    ok = await bp.apply_intensity(dev, 0.42)
    assert ok is True
    assert dev.sent == [(bp.ROTATE, (0.42,))]


@pytest.mark.asyncio
async def test_apply_intensity_zero_stops_device(fake_device) -> None:
    dev = fake_device("Hush", outputs={bp.VIBRATE})
    ok = await bp.apply_intensity(dev, 0.0)
    assert ok is True
    assert dev.sent == [("STOP", None)]


@pytest.mark.asyncio
async def test_send_position_prefers_position_with_duration(fake_device) -> None:
    dev = fake_device("Stroker", outputs={bp.POSITION, bp.POSITION_WITH_DURATION})
    ok = await bp.send_position(dev, 0.75, 500)
    assert ok is True
    assert dev.sent[0][0] == bp.POSITION_WITH_DURATION


@pytest.mark.asyncio
async def test_send_position_falls_back_to_plain_position_without_duration(fake_device) -> None:
    dev = fake_device("Stroker", outputs={bp.POSITION})
    ok = await bp.send_position(dev, 0.75, 0)
    assert ok is True
    assert dev.sent[0][0] == bp.POSITION


@pytest.mark.asyncio
async def test_read_battery_normalizes_fraction_to_percent(fake_device) -> None:
    dev = fake_device("Hush", outputs=set(), battery=0.85)
    pct = await bp.read_battery(dev)
    assert pct == 85.0
