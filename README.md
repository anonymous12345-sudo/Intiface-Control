# Intiface Control for Home Assistant

A Home Assistant custom integration that connects directly to an
[Intiface Central](https://intiface.com/central/) server (a
[buttplug.io](https://buttplug.io) implementation) and automatically
creates entities for every connected toy — no separate bridge process
to run, no per-brand configuration to write.

Point it at your Intiface WebSocket URL, and every toy you connect —
now or later, any brand the underlying `buttplug` library supports —
shows up in Home Assistant as its own device, with the right controls
already there.

---

## Table of contents

- [How it works](#how-it-works)
- [What gets created](#what-gets-created)
- [Installation](#installation)
  - [Via HACS (custom repository)](#via-hacs-custom-repository)
  - [Manual installation](#manual-installation)
- [Setup](#setup)
- [Using the entities](#using-the-entities)
- [The emergency stop switch](#the-emergency-stop-switch)
- [Patterns (wave / pulse)](#patterns-wave--pulse)
- [Adding new toys](#adding-new-toys)
- [How devices are identified](#how-devices-are-identified)
- [Troubleshooting](#troubleshooting)
- [Known limitations](#known-limitations)
- [Releasing updates (for maintainers)](#releasing-updates-for-maintainers)

---

## How it works

Home Assistant connects to Intiface's WebSocket server the same way
Intiface's own official apps do. A background coordinator keeps that
connection alive, polls every few seconds for the current device list,
and asks each connected device what it can actually do (vibrate,
oscillate, rotate, constrict, move to a position, report a battery
level) using the `buttplug` library's own capability introspection —
never a hardcoded list of device names or brands.

Whatever a device reports, that's what determines which Home Assistant
entities get created for it. A brand-new toy that nobody wrote any
code for still gets the right controls automatically, as long as the
`buttplug` library itself knows how to introspect it.

## What gets created

For every connected toy, grouped under one Home Assistant **device**:

| Entity | When it's created | What it does |
|---|---|---|
| `number` — Intensity | Device supports vibrate, oscillate, rotate, or constrict | 0–100% slider. Automatically uses whichever of those four output types the device actually supports. |
| `number` — Position | Device supports Position or PositionWithDuration | 0–100% slider for linear devices (e.g. a stroker). Moves immediately; no in-UI duration control in this version. |
| `binary_sensor` — Connected | Always | Reflects whether the toy is currently reachable through Intiface. |
| `sensor` — Battery | Device reports a battery level | Battery percentage. |

Plus, once per Home Assistant config entry (i.e. once per Intiface
server you've connected):

| Entity | What it does |
|---|---|
| `switch` — Stop all toys | See [The emergency stop switch](#the-emergency-stop-switch) below. |

## Installation

### Via HACS (custom repository)

1. Open HACS → the three-dot menu (top right) → **Custom repositories**.
2. Add this repository's URL: `https://github.com/anonymous12345-sudo/intiface-control`, category **Integration**.
3. Find "Intiface Control" in HACS and install it.
4. Restart Home Assistant.

Once installed this way, future updates you publish as GitHub Releases
will show up as a normal pending update in HACS, the same as any other
tracked integration — see [Releasing updates](#releasing-updates-for-maintainers)
if that's you.

### Manual installation

1. Download this repository (or just the `custom_components/buttplug`
   folder).
2. Copy the **entire `buttplug` folder** into your Home Assistant
   config directory, so you end up with:
   ```
   config/custom_components/buttplug/manifest.json
   config/custom_components/buttplug/__init__.py
   config/custom_components/buttplug/coordinator.py
   config/custom_components/buttplug/client.py
   config/custom_components/buttplug/config_flow.py
   config/custom_components/buttplug/const.py
   config/custom_components/buttplug/number.py
   config/custom_components/buttplug/binary_sensor.py
   config/custom_components/buttplug/sensor.py
   config/custom_components/buttplug/switch.py
   config/custom_components/buttplug/services.yaml
   config/custom_components/buttplug/strings.json
   config/custom_components/buttplug/translations/en.json
   ```
   (Not `config/custom_components/buttplug/custom_components/buttplug/...`
   — a common mistake if you copy the whole repo instead of just this
   folder's contents.)
3. Restart Home Assistant.

Manually installed copies don't get automatic update notifications;
you'll need to repeat this process to pick up a new version.

## Setup

1. Make sure Intiface Central is running on some device on your
   network, with its server started. The WebSocket URL is shown on
   its main screen — typically something like `ws://192.168.x.x:12345`.
2. In Home Assistant: **Settings → Devices & services → Add integration**,
   search for **"Intiface Control"**.
3. Enter the Intiface WebSocket URL. A fallback URL is optional, for
   setups where Intiface might be reachable at more than one address
   (e.g. it moves between networks).
4. Home Assistant tests the connection immediately — if it can't
   reach Intiface at that URL, you'll see an error before the entry is
   created.
5. Once added, connected toys appear as devices within a few seconds
   (the coordinator's first refresh runs immediately on setup).

You can add more than one config entry if you have more than one
Intiface server to connect to (e.g. two separate phones each running
Intiface Central).

## Using the entities

- **Intensity / Position sliders** — drag to set. This sends the
  command to the toy immediately (no separate "apply" step). Setting a
  value also cancels any pattern currently running on that same
  device.
- **Connected** — a `binary_sensor` with device class *connectivity*.
  This is the one entity that reflects "on/off" honestly when a toy
  disconnects: it turns off, and stays as a normal, present entity —
  see the next section for why that matters.
- **Battery** — a plain percentage sensor, only present for toys that
  actually report one.

## The emergency stop switch

There's one `switch` entity per config entry (not per toy):
**"Stop all toys."**

- **Turning it on** immediately stops every connected toy, cancels any
  running pattern, and — importantly — **blocks all further control
  commands** (intensity, position, and starting new patterns) until
  you turn it back off. It's a real gate, not a one-off action: you
  can't accidentally "un-stop" things by nudging a slider while it's
  engaged. All the intensity/position sliders also visually reset to
  0 the moment it's turned on, so the dashboard doesn't keep showing a
  stale value the toy isn't actually at anymore.
- **Turning it off** clears that block. It does not resume anything by
  itself — the next slider move or service call is what starts a toy
  again.

This is designed to be used the same way people commonly used an
`input_boolean` "abort" helper with the old REST-bridge-based setup:
as a state you can check in automation/script conditions (`state:
'off'` required to proceed) — except this one actually does something
when you flip it, instead of only being a flag other automations had
to react to.

## Patterns (wave / pulse)

Two services are available for background patterns, for any device
with an intensity capability (vibrate, oscillate, rotate, or
constrict):

### `buttplug.start_pattern`

| Field | Required | Description |
|---|---|---|
| `target` | Yes | A device slug (see [How devices are identified](#how-devices-are-identified)), or `all` / `both` for every connected toy. |
| `pattern` | Yes | `wave` (smooth sine oscillation) or `pulse` (2s on at max speed / 2s at a gentle 6% baseline). |
| `duration` | No, default 60 | How long to run, in seconds (1–3600). |
| `max_speed` | No, default 50 | Peak intensity, 0–100%. |

```yaml
action: buttplug.start_pattern
data:
  target: lovense_hush
  pattern: wave
  duration: 90
  max_speed: 42
```

### `buttplug.stop_pattern`

| Field | Required | Description |
|---|---|---|
| `target` | Yes | A device slug, or `all` / `both`. |

```yaml
action: buttplug.stop_pattern
data:
  target: lovense_hush
```

Notes:

- Starting a pattern cancels any pattern already running on that
  *exact* target string. `target: all` and `target: lovense_hush` are
  tracked separately — starting a pattern on `all` does not cancel one
  already running specifically on `lovense_hush`, and vice versa.
- Moving a device's intensity slider directly cancels any pattern
  running on that device's own slug.
- Both services are blocked while the emergency stop switch is on,
  same as direct intensity/position commands.

## Adding new toys

Nothing to configure. Connect a new toy to Intiface while Home
Assistant is running — the coordinator picks it up on its next refresh
(every few seconds) and creates the appropriate entities automatically,
grouped under a new device named after the toy.

If Intiface itself needs to actively re-scan to notice a brand-new
device (this is a WebSocket/Bluetooth-discovery detail on Intiface's
side, not something this integration controls), reconnecting or
restarting Intiface Central's own scan is what triggers that — the
Home Assistant side doesn't need a restart either way.

## How devices are identified

Every device gets a **slug** derived from its name as reported by
Intiface — lowercase, non-alphanumeric characters replaced with
underscores. For example:

| Device name | Slug |
|---|---|
| Lovense Hush | `lovense_hush` |
| Hismith Sex Machine | `hismith_sex_machine` |
| Simulated Stroker | `simulated_stroker` |

This slug is what you use as `target` in the pattern services. You can
find a device's exact slug in **Settings → Devices & services →
Intiface Control → *pick the device*** — it's embedded in each entity's
unique ID, or just check the entity ID Home Assistant assigned (e.g.
`number.lovense_hush_intensity`).

## Troubleshooting

**Integration doesn't show up when adding it**
Double-check the folder structure under `custom_components/buttplug/`
— see the note in [Manual installation](#manual-installation) about
avoiding an accidentally double-nested folder. Then check **Settings →
System → Logs** for an error mentioning `custom_components.buttplug`.

**"Could not connect to Intiface at that URL"**
Confirm Intiface Central is running and its server is actually
started (not just the app open), and that the URL is reachable from
your Home Assistant instance specifically — not just from your phone.
`ws://` (not `wss://`), and don't forget the port.

**A toy shows as disconnected, but it's still on**
Check Intiface Central directly first — if it doesn't see the toy
either, that's a Bluetooth/Intiface-side issue, not this integration.
If Intiface *does* see it but this integration doesn't, wait for the
next refresh cycle (a few seconds) or check the Home Assistant logs
for connection warnings.

**Entities never disappear even though a toy is gone for good**
This is intentional — see [The emergency stop switch](#the-emergency-stop-switch)
section's sibling behaviour: entities for a toy that goes offline
become *unavailable* (or, for the connectivity sensor, just switch to
"off"), but are never deleted. This keeps dashboards and automations
stable instead of silently losing entities. If you genuinely want to
remove a toy's entities (e.g. you sold it), delete its device manually
under **Settings → Devices & services → Intiface Control**.

## Known limitations

- `Constrict` and `PositionWithDuration` are implemented using the
  same capability-introspection approach as everything else, but
  haven't been exercised against real hardware of those specific kinds
  (e.g. a Lovense Max for Constrict) — only against Intiface's
  simulated devices. If something behaves oddly on real hardware of
  that type, check the Home Assistant logs at debug level; the
  underlying client code logs which specific attempt succeeded or
  failed.
- The position slider has no duration control in the UI. The
  coordinator supports it (`async_apply_position(slug, percent,
  duration_ms)`), it's just not exposed as a service or a second
  entity yet.
- One "stop all" switch per config entry, not per device. There's no
  per-toy stop button in this version — use the emergency stop switch
  (affects everything on that Intiface connection) or set a device's
  own intensity/position slider back to 0.

## Releasing updates (for maintainers)

If you're maintaining this repository and want HACS users to receive
proper update notifications (not just a raw commit-hash version), you
need to publish an actual **GitHub Release** — pushing commits or even
tags alone isn't enough; HACS specifically reads the tag name of the
latest *release* to determine the available version.

A minimal flow:

1. Bump `"version"` in `custom_components/buttplug/manifest.json`.
2. Commit and push.
3. On GitHub: **Releases → Draft a new release**, create a new tag
   matching the version (e.g. `v0.2.0`), publish it.

HACS-connected installs will then show this as a pending update, the
same as any other tracked integration.
