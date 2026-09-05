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

_LOGGER = logging.getLogger(__name__)

try:
    # ButtplugClient is re-exported here (not used directly in this file)
    # so coordinator.py can reference it as bp.ButtplugClient(...).
    from buttplug import ButtplugClient, DeviceOutputCommand, OutputType  # noqa: F401
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
TEMPERATURE = _enum(OutputType, "Temperature")
SPRAY = _enum(OutputType, "Spray")
LED = _enum(OutputType, "Led")
POSITION = _enum(OutputType, "Position")
# Name varies between library versions (the Rust side calls this HwPositionWithDuration).
POSITION_WITH_DURATION = (
    _enum(OutputType, "PositionWithDuration") or _enum(OutputType, "HwPositionWithDuration")
)
BATTERY_INPUT = _enum(InputType, "Battery")
RSSI_INPUT = _enum(InputType, "Rssi")
PRESSURE_INPUT = _enum(InputType, "Pressure")

# Preference order for the generic 0-100% "intensity" concept: the first
# type in this list that a device actually supports (via has_output())
# gets used. New toys with any of these outputs work automatically, no
# brand/name-specific code needed. Temperature and Spray are structurally
# identical scalar (0-100%) actuators to the haptic ones, so they share
# the same generic treatment (and can run wave/pulse patterns too).
#
# Rotate is deliberately NOT here, even though it's structurally similar
# — buttplug documents Rotate's value range as *signed* specifically to
# represent direction (clockwise/counter-clockwise), which a 0-100%
# unsigned slider can't express at all. It gets its own dedicated
# -100..100 entity instead (see number.py's IntifaceRotationNumber).
# Led is also deliberately NOT here — it's exposed as its own `light`
# entity instead (see light.py), since that's the idiomatic Home
# Assistant model for a light, not another generic intensity slider.
INTENSITY_OUTPUT_PRIORITY = [
    t for t in (OSCILLATE, VIBRATE, CONSTRICT, TEMPERATURE, SPRAY) if t is not None
]

# Named convenience methods on the device object, as a fallback for when
# run_output() doesn't work or doesn't exist (older library versions).
# Rotate is included here even though it's not in INTENSITY_OUTPUT_PRIORITY
# above — apply_rotation() below still uses this same fallback via send().
_METHOD_NAMES = {
    ot: name for ot, name in {
        OSCILLATE: "oscillate",
        VIBRATE: "vibrate",
        ROTATE: "rotate",
        CONSTRICT: "constrict",
        TEMPERATURE: "temperature",
        SPRAY: "spray",
    }.items() if ot is not None
}


def device_slug(dev) -> str:
    """Stable, readable identifier based on the device name, e.g.
    "Lovense Hush" -> "lovense_hush". Used as the HA unique_id suffix and
    as the key into the coordinator's device data."""
    name = (getattr(dev, "name", "") or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    return slug or "device"


def _has_output(dev, output_type) -> bool | None:
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
    except Exception:
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
        except Exception:
            _LOGGER.debug("has_input(Battery) failed on %s", getattr(dev, "name", "?"), exc_info=True)
    return hasattr(dev, "battery")


def has_rssi(dev) -> bool:
    """Whether this device reports Bluetooth signal strength. Same
    has_input()-with-hasattr-fallback pattern as has_battery() above."""
    has_input_fn = getattr(dev, "has_input", None)
    if has_input_fn is not None and RSSI_INPUT is not None:
        try:
            return bool(has_input_fn(RSSI_INPUT))
        except Exception:
            _LOGGER.debug("has_input(Rssi) failed on %s", getattr(dev, "name", "?"), exc_info=True)
    return hasattr(dev, "rssi")


def has_pressure(dev) -> bool:
    """Whether this device reports a pressure reading. Untested against
    real hardware (no pressure-sensing toy was available during
    development) — the read side (read_pressure() below) may need
    adjusting once someone can verify it against the real thing."""
    has_input_fn = getattr(dev, "has_input", None)
    if has_input_fn is not None and PRESSURE_INPUT is not None:
        try:
            return bool(has_input_fn(PRESSURE_INPUT))
        except Exception:
            _LOGGER.debug("has_input(Pressure) failed on %s", getattr(dev, "name", "?"), exc_info=True)
    return hasattr(dev, "pressure")


def has_led(dev) -> bool:
    return _has_output(dev, LED) is True


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
    if _has_output(dev, TEMPERATURE):
        caps.append("temperature")
    if _has_output(dev, SPRAY):
        caps.append("spray")
    if has_led(dev):
        caps.append("led")
    if _has_output(dev, POSITION):
        caps.append("position")
    if _has_output(dev, POSITION_WITH_DURATION):
        caps.append("position_with_duration")
    if has_battery(dev):
        caps.append("battery")
    if has_rssi(dev):
        caps.append("rssi")
    if has_pressure(dev):
        caps.append("pressure")
    return caps


async def send(dev, output_type, intensity: float) -> bool:
    if output_type is None:
        return False
    try:
        if hasattr(dev, "run_output"):
            await dev.run_output(DeviceOutputCommand(output_type, intensity))
            return True
    except Exception:
        _LOGGER.warning("run_output failed on %s", getattr(dev, "name", "?"), exc_info=True)
    method_name = _METHOD_NAMES.get(output_type)
    if method_name:
        method = getattr(dev, method_name, None)
        if method is not None:
            try:
                await method(intensity)
                return True
            except Exception:
                _LOGGER.warning("%s() failed on %s", method_name, getattr(dev, "name", "?"), exc_info=True)
    return False


async def stop_device(dev) -> None:
    try:
        if hasattr(dev, "stop"):
            await dev.stop()
    except Exception:
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


async def apply_rotation(dev, signed_speed: float) -> bool:
    """signed_speed is a -1.0..1.0 fraction — positive is clockwise,
    negative counter-clockwise, matching how buttplug documents Rotate's
    value range as signed specifically to represent direction. Unlike
    apply_intensity() above, only an EXACT zero stops the device — a
    negative value is a real, valid command here, not "off", so the
    same "speed<=0 means stop" shortcut would be wrong for this one."""
    if ROTATE is None:
        return False
    if signed_speed == 0:
        await stop_device(dev)
        return True
    return await send(dev, ROTATE, signed_speed)


async def apply_led(dev, brightness: float) -> bool:
    """brightness is a 0.0-1.0 fraction. Turning fully off (brightness<=0)
    still goes through send() rather than stop_device() — LED brightness
    isn't a haptic motor, stopping the device wholesale doesn't apply to
    it the way it does for vibrate/rotate/etc."""
    if LED is None:
        return False
    return await send(dev, LED, max(0.0, brightness))


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
            except Exception:
                _LOGGER.debug("PositionWithDuration attempt (%s) failed on %s", description, name, exc_info=True)

        for method_name, args in (("linear", (duration_ms, position)), ("position", (position, duration_ms))):
            method = getattr(dev, method_name, None)
            if method is not None:
                try:
                    await method(*args)
                    _LOGGER.debug("Position %s -> %.0f%% in %sms via %s()", name, position * 100, duration_ms, method_name)
                    return True
                except Exception:
                    _LOGGER.debug("%s(%s) failed on %s", method_name, args, name, exc_info=True)

    if POSITION is not None and await send(dev, POSITION, position):
        _LOGGER.debug("Position %s -> %.0f%% (direct, duration not supported)", name, position * 100)
        return True

    _LOGGER.warning("Could not send a position command to %s (no working method found)", name)
    return False


async def read_battery(dev) -> float | None:
    try:
        if hasattr(dev, "battery"):
            raw = await dev.battery()
            if raw is None:
                return None
            val = float(raw)
            if val <= 1.0:
                val *= 100.0
            return max(0.0, min(100.0, val))
    except Exception:
        _LOGGER.warning("battery() failed on %s", getattr(dev, "name", "?"), exc_info=True)
    return None


async def read_rssi(dev) -> float | None:
    """Returns Bluetooth signal strength in dBm (typically a negative
    number, e.g. -60), or None if unavailable."""
    try:
        if hasattr(dev, "rssi"):
            raw = await dev.rssi()
            if raw is None:
                return None
            return float(raw)
    except Exception:
        _LOGGER.warning("rssi() failed on %s", getattr(dev, "name", "?"), exc_info=True)
    return None


async def read_pressure(dev) -> float | None:
    """Returns a raw pressure reading, or None if unavailable.

    Untested against real hardware — no pressure-sensing toy was
    available during development. The buttplug library's exact
    pressure-value scale/units aren't confirmed here; this currently
    just passes the raw value through unmodified (unlike read_battery(),
    which normalizes a 0.0-1.0 fraction up to a percentage — pressure
    may need the same kind of normalization once it's actually been
    tested against a real device)."""
    try:
        if hasattr(dev, "pressure"):
            raw = await dev.pressure()
            if raw is None:
                return None
            return float(raw)
    except Exception:
        _LOGGER.warning("pressure() failed on %s", getattr(dev, "name", "?"), exc_info=True)
    return None


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


TICK_SECONDS = 0.2


async def _run_wave_cycle(devs_getter, min_speed: float, max_speed: float, duration: float) -> None:
    """One single wave: smoothly rises from min_speed to max_speed and
    back down to min_speed over `duration` seconds — a single sine hump
    (0 -> 1 -> 0 mapped across the full duration), not a repeating
    oscillation. Call this once per repeat from run_pattern_loop()."""
    elapsed = 0.0
    while elapsed < duration:
        progress = elapsed / duration if duration > 0 else 1.0
        sin_val = math.sin(progress * math.pi)  # 0 at start/end, 1 at the midpoint
        current_speed = _clamp01(min_speed + sin_val * (max_speed - min_speed))
        for dev in devs_getter():
            dev_output = intensity_output_type(dev)
            if dev_output is None:
                continue
            await send(dev, dev_output, current_speed)
        await asyncio.sleep(TICK_SECONDS)
        elapsed += TICK_SECONDS


async def _run_pulse_cycle(
    devs_getter, low_speed: float, high_speed: float, low_duration: float, high_duration: float
) -> None:
    """One single pulse: low_speed held for low_duration seconds, then
    high_speed held for high_duration seconds. Call this once per repeat
    from run_pattern_loop()."""
    for speed, phase_duration in ((low_speed, low_duration), (high_speed, high_duration)):
        elapsed = 0.0
        while elapsed < phase_duration:
            for dev in devs_getter():
                dev_output = intensity_output_type(dev)
                if dev_output is None:
                    continue
                await send(dev, dev_output, _clamp01(speed))
            await asyncio.sleep(TICK_SECONDS)
            elapsed += TICK_SECONDS


async def run_wave_pattern(devs_getter, target_label: str, repeat: int, min_speed: float, max_speed: float, duration: float) -> None:
    """Runs a single wave cycle `repeat` times back to back, on whatever
    devices `devs_getter()` returns on each tick (a callable, not a fixed
    list, so it stays correct even if the device set changes mid-pattern
    — e.g. a toy reconnecting, or getting disabled via its own Enabled
    switch mid-run). min_speed/max_speed are 0.0-1.0 fractions."""
    try:
        for _rep in range(repeat):
            await _run_wave_cycle(devs_getter, min_speed, max_speed, duration)
    except asyncio.CancelledError:
        _LOGGER.info("Wave pattern for %s was cancelled.", target_label)
    finally:
        for dev in devs_getter():
            await stop_device(dev)


async def run_pulse_pattern(
    devs_getter, target_label: str, repeat: int, low_speed: float, high_speed: float, low_duration: float, high_duration: float
) -> None:
    """Runs a single pulse cycle (low phase then high phase) `repeat`
    times back to back. See run_wave_pattern() above for the shared
    devs_getter()/cancellation/cleanup behaviour. low_speed/high_speed
    are 0.0-1.0 fractions."""
    try:
        for _rep in range(repeat):
            await _run_pulse_cycle(devs_getter, low_speed, high_speed, low_duration, high_duration)
    except asyncio.CancelledError:
        _LOGGER.info("Pulse pattern for %s was cancelled.", target_label)
    finally:
        for dev in devs_getter():
            await stop_device(dev)
