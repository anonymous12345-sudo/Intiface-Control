# Changelog

All notable changes to this project are documented here, per release.

## [Unreleased]

- Nothing yet.

## [0.1.3]

### Changed — breaking change to `buttplug.start_pattern`

Wave and pulse patterns are now a **single repeatable cycle** instead of
a continuous effect running for a fixed total duration.

- **Wave** — one smooth rise-and-fall (`min_speed` → `max_speed` →
  `min_speed`) over `duration` seconds, instead of a continuous sine
  oscillation for the whole run.
- **Pulse** — one low phase followed by one high phase, each with its
  own speed and duration, instead of a repeating 2s-on/2s-off square
  wave.
- New `repeat` field (default 1) on both patterns, to run the same
  cycle multiple times back to back.
- New/changed fields: `repeat`, `min_speed` (wave), `low_speed`,
  `high_speed`, `low_duration`, `high_duration` (pulse). `max_speed` and
  `duration` are now wave-only; pulse no longer uses them.

## [0.1.2]

### Added

- **Per-toy `Enabled` switch**: one per connected toy, on by default.
  Turning it off immediately stops that one toy and refuses further
  commands to it — intensity, position, and patterns — until switched
  back on. Independent from the global "Stop all toys" switch: disabling
  one toy never affects any other toy or the global switch, and vice
  versa.
- A toy that gets disabled while a pattern is running on `all`/`both` is
  silently excluded from it going forward — the pattern keeps running
  normally for every other toy, no restart needed.

## [0.1.1]

### Added

- **Pattern services**: `buttplug.start_pattern` and
  `buttplug.stop_pattern` for wave/pulse background patterns, on any
  device or on all connected toys at once.
- Full test suite (28 tests) and CI pipeline: hassfest validation, ruff
  linting, and pytest on Python 3.12 and 3.13, running automatically on
  every push.
- `LICENSE` (MIT).

### Changed

- The emergency-stop entity is now a **switch**, not a button. Turning
  it on immediately stops every toy, cancels any running pattern, and
  blocks all further commands until it's turned off again — a real
  safety gate, not just a one-off action. All intensity/position
  sliders also visually reset to 0 the moment it engages.
- Display name is now **"Intiface Control"** (internal domain/entity
  IDs are unchanged, so existing installs and dashboards keep working).

### Fixed

- Coordinator now correctly links itself to its config entry, which
  newer Home Assistant versions require for
  `async_config_entry_first_refresh()` to work at all — a real
  forward-compatibility bug, not just a test issue.
- Various lint issues (import ordering, `Optional[X]` → `X | None`, an
  unnecessary nested `if`).

## [0.1.0]

Initial release.

### Added

- Direct connection to Intiface Central over its WebSocket API — no
  separate bridge process required.
- Automatic entity creation per connected toy, based on live capability
  introspection (no hardcoded device names or brands): a `number` for
  intensity (vibrate/oscillate/rotate/constrict), a `number` for
  position (linear devices), a `binary_sensor` for connectivity, and a
  `sensor` for battery level where reported.
- Config flow: enter the Intiface WebSocket URL, connection tested
  before the entry is created.
- Devices that go offline are never removed from the entity registry —
  entities become unavailable (or, for connectivity, switch to "off")
  instead, so dashboards and automations stay stable.
