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

See [CHANGELOG.md](CHANGELOG.md) for what's changed in each release.

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
| `number` — Intensity | Device supports vibrate, oscillate, constrict, temperature, or spray | 0–100% slider. Automatically uses whichever of those output types the device actually supports. |
| `number` — Rotation | Device supports rotate | -100–100 signed slider — positive is clockwise, negative counter-clockwise, 0 is stopped. Kept separate from Intensity above since buttplug documents Rotate's range as signed specifically to represent direction, which a 0-100% slider can't express. |
| `number` — Position | Device supports Position or PositionWithDuration | 0–100% slider for linear devices (e.g. a stroker). Uses whatever duration is set on the companion "Position duration" entity below (0/instant if that's never been touched). |
| `number` — Position duration | Same as Position above | 0–10 second slider controlling how long the Position slider's *next* move takes. A stored preference, not a toy command by itself — moving only this slider never sends anything to the device. |
| `light` — LED | Device supports Led | Brightness-only light control. Modeled as a real Home Assistant light (not another generic slider), since that's the idiomatic representation for a light. |
| `binary_sensor` — Connected | Always | Reflects whether the toy is currently reachable through Intiface. |
| `sensor` — Battery | Device reports a battery level | Battery percentage, polled every 60s (independently of the general 5s device refresh) — a level that only moves over hours doesn't need a network round-trip every cycle. A newly connected (or reconnected) device always gets its first reading immediately, never waiting out that interval. |
| `sensor` — Signal strength | Device reports RSSI | Bluetooth signal strength in dBm. |
| `sensor` — Pressure | Device reports a pressure reading | A raw pressure value. **Untested against real hardware** — no device_class or unit is set since the exact scale buttplug reports isn't confirmed yet, and the entity is disabled by default until it's verified against real pressure-sensing hardware. |
| `switch` — Enabled | Always | On (the default) means this toy responds normally; turning it off immediately stops it and refuses further commands until switched back on. See [The emergency stop switch](#the-emergency-stop-switch) below. |

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

1. Download this repository (or just the `custom_components/intiface_control`
   folder).
2. Copy the **entire `buttplug` folder** into your Home Assistant
   config directory, so you end up with:
   ```
   config/custom_components/intiface_control/manifest.json
   config/custom_components/intiface_control/__init__.py
   config/custom_components/intiface_control/coordinator.py
   config/custom_components/intiface_control/client.py
   config/custom_components/intiface_control/config_flow.py
   config/custom_components/intiface_control/const.py
   config/custom_components/intiface_control/number.py
   config/custom_components/intiface_control/binary_sensor.py
   config/custom_components/intiface_control/sensor.py
   config/custom_components/intiface_control/switch.py
   config/custom_components/intiface_control/services.yaml
   config/custom_components/intiface_control/strings.json
   config/custom_components/intiface_control/translations/en.json
   ```
   (Not `config/custom_components/intiface_control/custom_components/intiface_control/...`
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

### Changing the Intiface URL later

If Intiface's address changes (a new phone, a different network), you
don't need to remove and re-add the integration — that would lose any
entity customizations, dashboard references, and area assignments tied
to it. Instead: **Settings → Devices & services → Intiface Control →
Configure**, enter the new URL, save. The connection is tested the same
way as during initial setup, and the integration reloads automatically
once saved.

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

There are two kinds of stop switch, both with the same gate semantics —
turning one off doesn't just stop something once, it also **refuses
further commands** until turned back on:

**`switch.stop_all_toys`** — one per config entry, affects every
connected toy.

- **Turning it on** immediately stops every connected toy, cancels any
  running pattern, and blocks all further control commands (intensity,
  position, and starting new patterns) to every toy until you turn it
  back off. You can't accidentally "un-stop" things by nudging a slider
  while it's engaged. All the intensity/position sliders also visually
  reset to 0 the moment it's turned on, so the dashboard doesn't keep
  showing a stale value no toy is actually at anymore.
- **Turning it off** clears that block. It does not resume anything by
  itself — the next slider move or service call is what starts a toy
  again.

**`switch.<toy>_enabled`** — one per toy, framed the other way round as
normal availability rather than a stop button.

- **On** (the default for a newly connected toy) means it responds to
  commands normally.
- **Turning it off** immediately stops that one toy, cancels any pattern
  running specifically on it, and refuses further commands to it — same
  gate mechanism as the global switch, just scoped to one device and
  inverted so a toy is usable right away rather than needing to be
  un-stopped first. If a background pattern is running on `all`/`both`,
  the disabled toy is silently excluded from it (the pattern keeps
  running normally for every other toy) without needing to restart
  anything.
- The two gates are fully independent: disabling one toy doesn't affect
  any other toy or the global switch, and the global switch stopping
  everything doesn't change any toy's own Enabled state.

Both switch types are designed to be used the same way people commonly
used an `input_boolean` "abort" helper with the old REST-bridge-based
setup: as a state you can check in automation/script conditions (`state:
'off'` required to proceed) — except these actually do something when
you flip them, instead of only being a flag other automations had to
react to.

## Patterns (wave / pulse)

Two services are available for background patterns, for any device
with an intensity capability (vibrate, oscillate, rotate, or
constrict). Both run a single wave or pulse cycle, optionally repeated
— not a continuous effect for a fixed total duration. Both target one
or more toys using Home Assistant's own device picker — pick a toy (or
several) the same way you would when targeting a device in an
automation, no need to know or type a device's internal slug.

### `intiface_control.start_wave_pattern`

One smooth rise-and-fall: `min_speed` → `max_speed` → `min_speed`, over `duration` seconds.

| Field | Required | Description |
|---|---|---|
| Target | Yes | One or more toys, picked via the device selector. |
| `repeat` | No, default 1 | How many times to repeat the single wave cycle (1–100). |
| `min_speed` | No, default 0 | The wave's trough, 0–100%. |
| `max_speed` | No, default 50 | The wave's peak, 0–100%. |
| `duration` | No, default 3 | How long **one** wave takes, in seconds (0.2–60). |

```yaml
action: intiface_control.start_wave_pattern
target:
  device_id: abc123devicehash
data:
  min_speed: 10
  max_speed: 80
  duration: 4
  repeat: 5
```

### `intiface_control.start_pulse_pattern`

One low phase followed by one high phase.

| Field | Required | Description |
|---|---|---|
| Target | Yes | One or more toys, picked via the device selector. |
| `repeat` | No, default 1 | How many times to repeat the single pulse cycle (1–100). |
| `low_speed` | No, default 0 | Speed during the low phase, 0–100%. |
| `high_speed` | No, default 80 | Speed during the high phase, 0–100%. |
| `low_duration` | No, default 2 | How long the low phase lasts, in seconds (0.2–60). |
| `high_duration` | No, default 2 | How long the high phase lasts, in seconds (0.2–60). |

```yaml
action: intiface_control.start_pulse_pattern
target:
  device_id: abc123devicehash
data:
  low_speed: 15
  high_speed: 90
  low_duration: 1.5
  high_duration: 0.5
  repeat: 10
```

### `intiface_control.stop_pattern`

| Field | Required | Description |
|---|---|---|
| Target | Yes | One or more toys, picked via the device selector. |

```yaml
action: intiface_control.stop_pattern
target:
  device_id: abc123devicehash
```

Notes:

- Starting a pattern cancels any pattern already running on that exact
  toy. Selecting several toys in one service call runs the same pattern
  on each of them independently (each gets its own cycle timing, they
  aren't synchronized to a shared clock).
- Moving a device's intensity slider directly cancels any pattern
  running on that toy.
- Both services are blocked while the global emergency stop switch, or
  that specific toy's own Enabled switch, is off/on respectively — a
  disabled toy simply refuses to start a pattern at all, rather than
  silently being excluded from one running on other toys.

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

This slug shows up in each toy's entity IDs (e.g.
`number.lovense_hush_intensity`) and unique IDs. You don't need it for
the pattern services — those use Home Assistant's device picker instead
— but it's useful for reading logs or writing your own templates.

## Troubleshooting

**Integration doesn't show up when adding it**
Double-check the folder structure under `custom_components/intiface_control/`
— see the note in [Manual installation](#manual-installation) about
avoiding an accidentally double-nested folder. Then check **Settings →
System → Logs** for an error mentioning `custom_components.intiface_control`.

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

**A persistent "Can't connect to Intiface" notification under Settings → System → Repairs**
This appears once the connection has failed for several consecutive
refresh cycles in a row (not for a single brief blip) — auto-reconnect
is still running in the background regardless, this is purely a
visibility aid so a long-running outage doesn't go unnoticed just
because nobody happened to look at an entity. It clears itself
automatically the moment the connection recovers. If the address
itself has changed, use **Settings → Devices & services → Intiface
Control → Configure** to update it.

**Two toys with the same name**
Each toy's identifier is derived from its name as reported by Intiface.
If two connected toys happen to share the same name (e.g. two of the
same model), they're automatically disambiguated by appending their
Intiface device index (`lovense_hush_0`, `lovense_hush_1`) instead of
colliding into a single entity. A lone toy is never affected by this —
only an actual name collision changes anything.

## Known limitations

- `Constrict`, `PositionWithDuration`, `Rotate`, `Temperature`, `Spray`,
  and `Led` are all implemented using the same capability-introspection
  approach as everything else, but haven't been exercised against real
  hardware of those specific kinds (e.g. a heating, LED-equipped, or
  bidirectional-rotating toy) — only against Intiface's simulated
  devices. If something behaves oddly on real hardware of that type,
  check the Home Assistant logs at debug level; the underlying client
  code logs which specific attempt succeeded or failed. Rotation in
  particular: buttplug's spec documents the value range for Rotate as
  signed to support direction, and that's what this integration sends,
  but a specific device's *actual* supported range (e.g. whether it can
  really reverse, or only spin one way) isn't something this
  integration can introspect — only Intiface itself knows that per
  device, and is expected to handle it appropriately.
- `Pressure` is implemented but genuinely unverified against real
  hardware — the exact value scale/units buttplug reports for it aren't
  confirmed, so the sensor has no unit or device class and is disabled
  by default. If you have pressure-sensing hardware, enabling it and
  reporting back what values it actually shows would help nail this
  down properly.
- `Button` input (a physical button press on a toy) isn't implemented
  at all. Unlike everything else here, a button press is inherently a
  one-off event, not a continuous value — it doesn't fit this
  integration's poll-based refresh model the way battery/RSSI/pressure
  do, and would likely need its own architecture (e.g. a Home Assistant
  `event` entity backed by a push/subscription mechanism, if the
  underlying Python library even supports one) rather than being added
  onto the existing pattern.
- The index-based disambiguation for same-named toys (see
  Troubleshooting above) isn't guaranteed stable across a full Intiface
  restart — indices are assigned in connection order, which could in
  principle shift which of two identically-named toys gets which
  suffix. Toys with distinct names are unaffected.

## Releasing updates (for maintainers)

If you're maintaining this repository and want HACS users to receive
proper update notifications (not just a raw commit-hash version), you
need to publish an actual **GitHub Release** — pushing commits or even
tags alone isn't enough; HACS specifically reads the tag name of the
latest *release* to determine the available version.

A minimal flow:

1. Bump `"version"` in `custom_components/intiface_control/manifest.json`.
2. Commit and push.
3. On GitHub: **Releases → Draft a new release**, create a new tag
   matching the version (e.g. `v0.2.0`), publish it.

HACS-connected installs will then show this as a pending update, the
same as any other tracked integration.
