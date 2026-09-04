"""Low-level wrapper around the buttplug library: connecting, sending
commands, reading capabilities and battery status.

This is a port of the logic proven out in the standalone
ha_buttplug_bridge project (a separate HTTP bridge), rewritten as free
functions operating on a device object rather than module-level globals,
so the owning coordinator (see coordinator.py) can hold its own
connection state per config entry.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from typing import Optional

_LOGGER = logging.getLogger(__name__)

try:
    from buttplug import ButtplugClient, DeviceOutputCommand, OutputType
except ImportError as err:  # pragma: no cover - import guard
    raise ImportError("The 'buttplug' package is required for this integration") from err

try:
    from buttplug import InputType
except ImportError:  # pragma: no cover - older library versions
    InputType = None


def _pascal_to_snake_upper(name: str) -> str:
    """"PositionWithDuration" -> "POSITION_WITH_DURATION". A naive
    name.upper() doesn't insert underscores between words, so multi-word
    enum names never matched a library that names its members
    UPPER_SNAKE_CASE — this bit was the source of a real bug during
    development, not a hypothetical edge case."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).upper()


def _enum(enum_cls, name: str):
    if enum_cls is None:
        return None
    if hasattr(enum_cls, name):
        return getattr(enum_cls, name)
    upper = name.upper()
    if hasattr(enum_cls, upper):
        return getattr(enum_cls, upper)
    snake_upper = _pascal_to_snake_upper(name)
    if hasattr(enum_cls, snake_upper):
        return getattr(enum_cls, snake_upper)
    return None


OSCILLATE = _enum(OutputType, "Oscillate")
VIBRATE = _enum(OutputType, "Vibrate")
ROTATE = _enum(OutputType, "Rotate")
CONSTRICT = _enum(OutputType, "Constrict")
POSITION = _enum(OutputType, "Position")
# Name varies between library versions (the Rust side calls this HwPositionWithDuration).
POSITION_WITH_DURATION = (
    _enum(OutputType, "PositionWithDuration") or _enum(OutputType, "HwPositionWithDuration")
)
BATTERY_INPUT = _enum(InputType, "Battery")

# Preference order for the generic 0-100% "intensity" concept: the first
# type in this list that a device actually supports (via has_output())
# gets used. New toys with any of these outputs work automatically, no
# brand/name-specific code needed.
INTENSITY_OUTPUT_PRIORITY = [t for t in (OSCILLATE, VIBRATE, ROTATE, CONSTRICT) if t is not None]

# Named convenience methods on the device object, as a fallback for when
# run_output() doesn't work or doesn't exist (older library versions).
_METHOD_NAMES = {
    ot: name for ot, name in {
        OSCILLATE: "oscillate",
        VIBRATE: "vibrate",
        ROTATE: "rotate",
        CONSTRICT: "constrict",
    }.items() if ot is not None
}


def device_slug(dev) -> str:
    """Stable, readable identifier based on the device name, e.g.
    "Lovense Hush" -> "lovense_hush". Used as the HA unique_id suffix and
    as the key into the coordinator's device data."""
    name = (getattr(dev, "name", "") or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    return slug or "device"


def _has_output(dev, output_type) -> Optional[bool]:
    """Asks the device itself whether it supports a given output type.
    Returns None if introspection isn't possible (older library without
    has_output()) — callers should treat that as 'unknown', not as 'no'."""
    if output_type is None:
        return False
    has_output_fn = getattr(dev, "has_output", None)
    if has_output_fn is None:
        return None
    try:
        return bool(has_output_fn(output_type))
    except Exception:  # noqa: BLE001
        _LOGGER.debug("has_output(%s) failed on %s", output_type, getattr(dev, "name", "?"), exc_info=True)
        return None


def has_battery(dev) -> bool:
    """Whether it makes sense to query this device's battery. Uses
    has_input() if the library provides it; falls back to
    hasattr(dev, "battery") on older libraries without it."""
    has_input_fn = getattr(dev, "has_input", None)
    if has_input_fn is not None and BATTERY_INPUT is not None:
        try:
            return bool(has_input_fn(BATTERY_INPUT))
        except Exception:  # noqa: BLE001
            _LOGGER.debug("has_input(Battery) failed on %s", getattr(dev, "name", "?"), exc_info=True)
    return hasattr(dev, "battery")


def intensity_output_type(dev):
    """Picks which output type is used for the generic 0-100% intensity
    concept: the first type from INTENSITY_OUTPUT_PRIORITY that this
    device actually supports (via has_output()). Falls back to a
    guess-and-try if introspection isn't possible at all."""
    introspectable = any(_has_output(dev, ot) is not None for ot in INTENSITY_OUTPUT_PRIORITY)
    if introspectable:
        for ot in INTENSITY_OUTPUT_PRIORITY:
            if _has_output(dev, ot):
                return ot
        return None
    return INTENSITY_OUTPUT_PRIORITY[0] if INTENSITY_OUTPUT_PRIORITY else None


def get_capabilities(dev) -> list[str]:
    """Public list of capability strings for `dev`, used by the platforms
    (number.py, sensor.py, ...) to decide which entities to create for
    this device automatically, without hardcoding anything by name or
    brand. Only lists a capability when introspection positively confirms
    it (True) — an unknown result (None) is treated as absent here."""
    caps: list[str] = []
    if _has_output(dev, OSCILLATE):
        caps.append("oscillate")
    if _has_output(dev, VIBRATE):
        caps.append("vibrate")
    if _has_output(dev, ROTATE):
        caps.append("rotate")
    if _has_output(dev, CONSTRICT):
        caps.append("constrict")
    if _has_output(dev, POSITION):
        caps.append("position")
    if _has_output(dev, POSITION_WITH_DURATION):
        caps.append("position_with_duration")
    if has_battery(dev):
        caps.append("battery")
    return caps


async def send(dev, output_type, intensity: float) -> bool:
    if output_type is None:
        return False
    try:
        if hasattr(dev, "run_output"):
            await dev.run_output(DeviceOutputCommand(output_type, intensity))
            return True
    except Exception:  # noqa: BLE001
        _LOGGER.warning("run_output failed on %s", getattr(dev, "name", "?"), exc_info=True)
    method_name = _METHOD_NAMES.get(output_type)
    if method_name:
        method = getattr(dev, method_name, None)
        if method is not None:
            try:
                await method(intensity)
                return True
            except Exception:  # noqa: BLE001
                _LOGGER.warning("%s() failed on %s", method_name, getattr(dev, "name", "?"), exc_info=True)
    return False


async def stop_device(dev) -> None:
    try:
        if hasattr(dev, "stop"):
            await dev.stop()
    except Exception:  # noqa: BLE001
        _LOGGER.warning("stop failed on %s", getattr(dev, "name", "?"), exc_info=True)


async def apply_intensity(dev, speed: float) -> bool:
    """speed is a 0.0-1.0 fraction; applies it via whichever intensity
    output type the device supports, or stops it if speed<=0."""
    if speed <= 0:
        await stop_device(dev)
        return True

    output_type = intensity_output_type(dev)
    ok = await send(dev, output_type, speed) if output_type is not None else False

    if not ok:
        for fallback_type in INTENSITY_OUTPUT_PRIORITY:
            if fallback_type != output_type and await send(dev, fallback_type, speed):
                ok = True
                break
    return ok


async def send_position(dev, position: float, duration_ms: int) -> bool:
    """Moves a linear device (e.g. a stroker) to `position` (0.0-1.0)
    within `duration_ms` milliseconds, or immediately if duration_ms is 0.

    Note: the exact way PositionWithDuration needs to be called differs
    between library versions. This tries a few plausible variants and
    logs which one succeeds."""
    name = getattr(dev, "name", "?")

    if duration_ms > 0 and POSITION_WITH_DURATION is not None and _has_output(dev, POSITION_WITH_DURATION) is not False:
        for description, attempt in (
            ("run_output(type, position, duration_ms)", lambda: dev.run_output(
                DeviceOutputCommand(POSITION_WITH_DURATION, position, duration_ms))),
            ("run_output(type, (position, duration_ms))", lambda: dev.run_output(
                DeviceOutputCommand(POSITION_WITH_DURATION, (position, duration_ms)))),
        ):
            try:
                await attempt()
                _LOGGER.debug("Position %s -> %.0f%% in %sms via %s", name, position * 100, duration_ms, description)
                return True
            except Exception:  # noqa: BLE001
                _LOGGER.debug("PositionWithDuration attempt (%s) failed on %s", description, name, exc_info=True)

        for method_name, args in (("linear", (duration_ms, position)), ("position", (position, duration_ms))):
            method = getattr(dev, method_name, None)
            if method is not None:
                try:
                    await method(*args)
                    _LOGGER.debug("Position %s -> %.0f%% in %sms via %s()", name, position * 100, duration_ms, method_name)
                    return True
                except Exception:  # noqa: BLE001
                    _LOGGER.debug("%s(%s) failed on %s", method_name, args, name, exc_info=True)

    if POSITION is not None:
        if await send(dev, POSITION, position):
            _LOGGER.debug("Position %s -> %.0f%% (direct, duration not supported)", name, position * 100)
            return True

    _LOGGER.warning("Could not send a position command to %s (no working method found)", name)
    return False


async def read_battery(dev) -> Optional[float]:
    try:
        if hasattr(dev, "battery"):
            raw = await dev.battery()
            if raw is None:
                return None
            val = float(raw)
            if val <= 1.0:
                val *= 100.0
            return max(0.0, min(100.0, val))
    except Exception:  # noqa: BLE001
        _LOGGER.warning("battery() failed on %s", getattr(dev, "name", "?"), exc_info=True)
    return None


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


async def run_pattern_loop(devs_getter, pattern_type: str, duration: int, max_speed: float, target_label: str) -> None:
    """Generates a tight, real-time wave or pulse motion in the background,
    on whatever devices `devs_getter()` returns each tick (a callable, not
    a fixed list, so it stays correct even if the device set changes
    mid-pattern — e.g. a toy reconnecting).

    Ported from the standalone ha_buttplug_bridge project's
    patterns.run_pulse_wave(), unchanged in behaviour: wave is a smooth
    sine oscillation, pulse is a 2s-on/2s-off square wave."""
    start_time = asyncio.get_event_loop().time()
    end_time = start_time + duration
    tick = 0

    try:
        while asyncio.get_event_loop().time() < end_time:
            for dev in devs_getter():
                dev_output = intensity_output_type(dev)
                if dev_output is None:
                    continue

                if pattern_type == "wave":
                    # Sine wave: smoothly oscillates between a comfortable baseline (5%) and max_speed
                    sin_val = (math.sin(tick * 0.4) + 1) / 2
                    current_speed = _clamp01(0.05 + (sin_val * (max_speed - 0.05)))
                    await send(dev, dev_output, current_speed)

                elif pattern_type == "pulse":
                    # Square wave / pulse: 2 seconds hard at max_speed, 2 seconds gentle at 6%
                    current_speed = _clamp01(max_speed if (tick % 20 < 10) else 0.06)
                    await send(dev, dev_output, current_speed)

            tick += 1
            await asyncio.sleep(0.2)

    except asyncio.CancelledError:
        _LOGGER.info("Pattern for %s was cancelled.", target_label)
    finally:
        for dev in devs_getter():
            await stop_device(dev)
