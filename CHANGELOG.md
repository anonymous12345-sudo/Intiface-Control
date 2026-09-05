# Changelog

All notable changes to this project are documented here, per release.

## [Unreleased]

- Nothing yet.

## [0.2.3]

### Added

- **Position duration control**: a new "Position duration" number
  entity (0–10 seconds) alongside every stroker's existing Position
  slider. Set it once, and the Position slider automatically uses that
  duration for its next move (0/instant if never touched, matching the
  previous, only behaviour) — no need to supply a duration on every
  single move.

## [0.2.2]

### Changed

- **Battery is now polled at most once every 60 seconds per toy**,
  independently of the general 5-second device/capability refresh —
  a value that only moves over hours doesn't need a network
  round-trip every cycle. A newly connected (or reconnected) toy still
  always gets its first reading immediately, so it's never shown
  online without a battery value while the cache is warming up.

## [0.2.1]

### Added

- **Repair issue** under Settings → System → Repairs when Intiface has
  been unreachable for several consecutive refresh cycles in a row —
  purely a visibility aid (auto-reconnect behaviour is unchanged), and
  it clears itself automatically once the connection recovers.

### Fixed

- **Two toys sharing the same name used to silently collide.** Device
  identifiers are name-derived, so two toys of the same model (or any
  other name collision) previously overwrote each other in the
  coordinator's internal data — one of them would just vanish from Home
  Assistant with no error. They're now automatically disambiguated by
  device index (`lovense_hush_0`, `lovense_hush_1`) when a collision is
  detected; a lone device's slug is unaffected.
- Consolidated `self.entry`/`self.config_entry` on the coordinator down
  to just `self.config_entry` (the two were the same object under two
  different names since the earlier config_entry fix).
- The Home Assistant device_id → toy-slug lookup used for the pattern
  services now does an exact match against each known slug's expected
  identifier, instead of parsing a device's identifier string apart on
  an assumed prefix boundary.

### Changed

- The global "Stop all toys" switch is now a `CoordinatorEntity`, like
  the per-toy Enabled switch, with its `is_on` computed live from the
  coordinator's own gate state instead of a separately cached local
  flag. Purely an internal consistency clean-up — behaviour is
  unchanged (still always available regardless of connection state).

## [0.2.0]

### Changed — breaking changes

- **Domain renamed from `buttplug` to `intiface_control`**, matching
  the integration's display name everywhere, not just in the UI. This
  requires removing and re-adding the integration — Home Assistant
  identifies an installed integration by its domain, so there's no
  in-place migration path. You'll need to delete the old entities and
  update any dashboards/automations that reference them. Internal
  Python class names (`ButtplugCoordinator` → `IntifaceCoordinator`,
  etc.) were renamed to match; `manufacturer="Buttplug.io"` on each
  device was deliberately left as-is, since it refers to the underlying
  open-source protocol, not this integration's own name.
- **`buttplug.start_pattern` split into two services**:
  `intiface_control.start_wave_pattern` and
  `intiface_control.start_pulse_pattern`. Each now only exposes the
  fields relevant to its own pattern, instead of a shared schema with
  half the fields silently ignored depending on which pattern you
  picked.
- **Pattern services now target Home Assistant devices, not a typed
  slug string.** `target: lovense_hush` / `target: all` are gone;
  `intiface_control.start_wave_pattern`,
  `intiface_control.start_pulse_pattern`, and
  `intiface_control.stop_pattern` all use Home Assistant's own device
  selector instead, supporting multi-select natively. A toy disabled
  via its own Enabled switch now simply refuses to start a pattern,
  rather than being silently excluded from a running `all` pattern —
  there's no more `all`/`both` concept to be excluded from.

## [0.1.4]

### Added

- **Options flow**: change the Intiface WebSocket URL after initial
  setup via **Settings → Devices & services → Intiface Control →
  Configure**, without removing and re-adding the integration. The
  connection is tested the same way as during initial setup, and the
  integration reloads automatically once saved — no more losing entity
  customizations, dashboard references, or area assignments just to
  point at a new URL.
- `CHANGELOG.md`, tracking notable changes per release going forward
  (backfilled for 0.1.0–0.1.3 too).

### Removed

- The CI job validating against official HACS default-repository
  listing criteria. This project is staying a custom-repository
  integration for now, so that job was permanently-red noise rather
  than useful signal — removed rather than left "for later".

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
