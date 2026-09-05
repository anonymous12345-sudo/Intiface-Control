"""DataUpdateCoordinator for the Intiface Control integration.

Owns the persistent connection to Intiface and periodically refreshes the
list of connected devices, their capabilities, and battery levels.

Devices that go offline are NEVER removed from HA's entity registry: they
simply drop out of `coordinator.data`, which entities interpret as
"unavailable" (number/sensor entities) or as a state change to off
(the connectivity binary_sensor) — never as "delete this entity". This is
deliberate: losing entities from a dashboard just because a toy was
temporarily switched off would be far more disruptive than showing it as
unavailable.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import client as bp
from .const import (
    BATTERY_POLL_INTERVAL_SECONDS,
    CLIENT_NAME,
    CONF_FALLBACK_URL,
    CONF_URL,
    DOMAIN,
    MAX_CONSECUTIVE_FAILURES,
    UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


def _connection_issue_id(entry_id: str) -> str:
    return f"cannot_connect_{entry_id}"


def _clamp(value: float, lo: float, hi: float) -> float:
    """HA's own number entities already enforce their declared min/max
    before calling into these coordinator methods, but the methods
    themselves are reachable more directly too (a service call, a
    future automation-facing API) — clamping here as well means an
    out-of-range value (e.g. percent=150) can never reach the device as
    something outside the range it was ever meant to represent, no
    matter how it got here."""
    return max(lo, min(hi, value))


class IntifaceCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Coordinates the Intiface connection and the list of known devices."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.url: str = entry.data[CONF_URL]
        self.fallback_url: str | None = entry.data.get(CONF_FALLBACK_URL) or None

        self._bp_client = None
        self._connect_lock = asyncio.Lock()
        self._consecutive_failures = 0

        # Battery polling cache: last known value and when it was last
        # actually fetched, per slug. A slug with no entry here yet gets
        # polled immediately (covers both a brand-new device and one
        # reappearing after being offline for a while) — only a slug
        # that was already polled recently reuses its cached value
        # instead of hitting the network again.
        self._battery_cache: dict[str, float | None] = {}
        self._last_battery_poll: dict[str, float] = {}

        # Running pattern tasks, keyed by device slug.
        self._active_patterns: dict[str, asyncio.Task] = {}

        # How long a position move should take, per slug — a stored
        # preference set via its own number entity, not a live toy state.
        # Not touched by the emergency-stop gates below: it doesn't move
        # anything by itself, only async_apply_position() does, and that
        # already goes through the same gate checks as every other
        # command. Restored from the config entry's own options (see
        # async_set_position_duration below) so it survives a Home
        # Assistant restart/reload instead of silently resetting to 0 —
        # missing/never-set still means 0 (instant move).
        self.position_duration_ms: dict[str, int] = dict(
            entry.options.get("position_duration_ms", {})
        )

        # True while the emergency-stop switch is on. Acts as a real gate:
        # every control method below refuses to send anything while this
        # is set, not just a one-off stop at the moment the switch flips.
        self.stopped: bool = False
        # Slugs currently under a per-device stop switch. Same gate
        # semantics as `stopped` above, but scoped to one toy instead of
        # everything — see async_stop_device()/async_clear_device_stop().
        self.per_slug_stopped: set[str] = set()

        # Slugs we've ever seen — lets platforms distinguish "brand new
        # device this cycle" from "device we already made entities for".
        self.known_slugs: set[str] = set()

        # Persistent slug assignment for the lifetime of this
        # coordinator (this HA session) — see the top of
        # _async_update_data() below for the full reasoning. Keyed by
        # (name-derived base slug, device index or a same-cycle
        # fallback identity), so a device that's already been assigned
        # a slug keeps that exact slug on every later refresh, even
        # across it disconnecting and reconnecting, or another
        # same-named device coming and going around it.
        self._slug_assignments: dict[tuple[str, Any], str] = {}
        # Base slugs (pre-disambiguation, e.g. "lovense_hush") that have
        # ever collided this session. Once a name has collided even
        # once, it never goes back to handing out the plain, unsuffixed
        # slug — otherwise the one surviving device after the other
        # disconnects would have its slug (and therefore its entities)
        # change out from under it, even though nothing happened to it.
        self._collided_base_slugs: set[str] = set()
        # Callbacks platforms register to get called with newly seen
        # devices: list[tuple[slug, device_obj, capabilities]].
        self._new_device_listeners: list = []
        # Callbacks number entities register to reset their displayed
        # value (e.g. an intensity slider back to 0) the moment a stop
        # engages — see async_stop_all()/async_stop_device(). Called with
        # a single argument: the affected slug, or None for "all devices"
        # (a global stop). Entities compare that against their own slug.
        self._stop_listeners: list = []

    def add_stop_listener(self, callback):
        """Number/light entities register here so they visually reset to
        0/off as soon as a stop (global or per-device) is engaged, or a
        pattern finishes on its own, instead of showing a stale value.
        `callback` is invoked with one argument: the affected slug, or
        None if every device is affected. Returns an unsubscribe
        function — entities should call it via `self.async_on_remove()`
        from `async_added_to_hass()` so the listener doesn't keep
        referencing an entity that's since been removed."""
        self._stop_listeners.append(callback)

        def _unsubscribe() -> None:
            if callback in self._stop_listeners:
                self._stop_listeners.remove(callback)

        return _unsubscribe

    def add_new_device_listener(self, callback):
        """Platforms register here to be notified about newly discovered
        devices, so they can create entities for them dynamically.
        Returns an unsubscribe function, same as add_stop_listener()
        above."""
        self._new_device_listeners.append(callback)

        def _unsubscribe() -> None:
            if callback in self._new_device_listeners:
                self._new_device_listeners.remove(callback)

        return _unsubscribe

    def _devices(self) -> list:
        if self._bp_client is None:
            return []
        for attr in ("devices", "device_map"):
            d = getattr(self._bp_client, attr, None)
            if isinstance(d, dict):
                return list(d.values())
            if isinstance(d, list):
                return d
        return []

    def _is_connected(self) -> bool:
        """Best-effort check of whether the buttplug client is still connected."""
        if self._bp_client is None:
            return False
        for attr in ("connected", "is_connected"):
            val = getattr(self._bp_client, attr, None)
            if isinstance(val, bool):
                return val
        return True

    async def _ensure_client(self) -> None:
        if self._bp_client is not None and self._is_connected():
            return
        async with self._connect_lock:
            if self._bp_client is not None and self._is_connected():
                return
            if self._bp_client is not None:
                _LOGGER.warning("Buttplug client appears to be disconnected, reconnecting...")
                self._bp_client = None

            c = bp.ButtplugClient(CLIENT_NAME)
            last_err: Exception | None = None
            for url in (self.url, self.fallback_url):
                if not url:
                    continue
                try:
                    _LOGGER.debug("Connecting to %s", url)
                    await c.connect(url)
                    last_err = None
                    break
                except Exception as exc:
                    last_err = exc
                    _LOGGER.warning("Connect to %s failed: %s", url, exc)
            if last_err is not None:
                raise UpdateFailed(f"Could not connect to Intiface: {last_err}") from last_err

            self._bp_client = c
            if hasattr(c, "start_scanning"):
                await c.start_scanning()
                await asyncio.sleep(2)
                if hasattr(c, "stop_scanning"):
                    await c.stop_scanning()
            _LOGGER.info("Connected, devices: %s", [getattr(d, "name", "?") for d in self._devices()])

    async def _poll_battery(self, slug: str, dev) -> float | None:
        """Fetches battery over the network at most once every
        BATTERY_POLL_INTERVAL_SECONDS per slug — a level that only moves
        on a scale of hours doesn't need a real round-trip every 5s
        refresh. A slug with no prior poll recorded (a brand-new device,
        or one that just reappeared after being offline long enough for
        this same check to naturally expire) always polls immediately,
        so a connected device is never shown without a battery value
        while this cache is still warming up."""
        now = asyncio.get_event_loop().time()
        last_poll = self._last_battery_poll.get(slug)
        if last_poll is None or (now - last_poll) >= BATTERY_POLL_INTERVAL_SECONDS:
            battery = await bp.read_battery(dev)
            self._battery_cache[slug] = battery
            self._last_battery_poll[slug] = now
            return battery
        return self._battery_cache.get(slug)

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            await self._ensure_client()
            self._consecutive_failures = 0
            ir.async_delete_issue(self.hass, DOMAIN, _connection_issue_id(self.config_entry.entry_id))
        except UpdateFailed:
            self._consecutive_failures += 1
            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                _LOGGER.warning(
                    "Intiface unreachable for %d consecutive updates, marking all devices offline",
                    self._consecutive_failures,
                )
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    _connection_issue_id(self.config_entry.entry_id),
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="cannot_connect",
                    translation_placeholders={"url": self.url},
                )
                return {}
            # Keep the last-known snapshot for a few cycles so a single
            # transient blip doesn't flash every device offline.
            return self.data or {}

        devs = self._devices()
        data: dict[str, dict[str, Any]] = {}
        new_devices = []

        # Slugs are name-derived, so two devices with the same name (e.g.
        # two of the same toy model) would otherwise collide and silently
        # overwrite each other below. Disambiguation is *persistent* for
        # the lifetime of this coordinator (see self._slug_assignments/
        # self._collided_base_slugs above), not recomputed from scratch
        # every cycle — recomputing from only the currently-connected set
        # was the source of a real bug: with two same-named devices A/B
        # disambiguated to _0/_1, A disconnecting would make B look like
        # the only device with that name again, silently reverting B's
        # slug back to the plain, unsuffixed form — changing B's entity
        # identity even though nothing happened to B itself. Once a name
        # has ever collided, it never hands out the plain slug again,
        # and a device that's already been assigned a slug always keeps
        # that exact slug afterward, including across it disconnecting
        # and reconnecting later.
        base_slugs = [bp.device_slug(d) for d in devs]
        counts: dict[str, int] = {}
        for s in base_slugs:
            counts[s] = counts.get(s, 0) + 1
        newly_collided = {s for s, c in counts.items() if c > 1} - self._collided_base_slugs
        if newly_collided:
            _LOGGER.warning(
                "Multiple devices share a name (%s) — disambiguating by device index",
                sorted(newly_collided),
            )
        self._collided_base_slugs.update(newly_collided)

        for dev, base_slug in zip(devs, base_slugs):
            idx = getattr(dev, "index", None)
            identity = (base_slug, idx if idx is not None else id(dev))
            if identity in self._slug_assignments:
                slug = self._slug_assignments[identity]
            elif base_slug in self._collided_base_slugs:
                slug = f"{base_slug}_{idx}" if idx is not None else f"{base_slug}_{id(dev)}"
                self._slug_assignments[identity] = slug
            else:
                slug = base_slug
                self._slug_assignments[identity] = slug

            caps = bp.get_capabilities(dev)
            battery = None
            if "battery" in caps:
                battery = await self._poll_battery(slug, dev)
            rssi = None
            if "rssi" in caps:
                rssi = await bp.read_rssi(dev)
            pressure = None
            if "pressure" in caps:
                pressure = await bp.read_pressure(dev)
            data[slug] = {
                "name": getattr(dev, "name", slug),
                "capabilities": caps,
                "battery": battery,
                "rssi": rssi,
                "pressure": pressure,
                "device": dev,
            }
            if slug not in self.known_slugs:
                self.known_slugs.add(slug)
                new_devices.append((slug, dev, caps))

        if new_devices:
            _LOGGER.info("New device(s) discovered: %s", [n for n, _, _ in new_devices])
            for callback in self._new_device_listeners:
                callback(new_devices)

        return data

    def _devices_matching(self, slug: str) -> list:
        """The single device matching `slug`, wrapped in a list (for a
        uniform devs_getter() interface shared with the pattern
        functions) — empty if the device isn't currently connected or is
        under its own per-device stop. Re-evaluated fresh on every call
        (not cached), so it stays correct even if the device reconnects
        with a new object instance mid-pattern, or gets disabled via its
        own Enabled switch mid-run."""
        slug = (slug or "").strip().lower()
        if slug in self.per_slug_stopped:
            return []
        dev = self.get_device(slug)
        return [dev] if dev is not None else []

    async def _cancel_pattern(self, slug: str) -> None:
        """Cancels any pattern running for `slug` and waits for its
        cleanup (which stops the device) to actually finish before
        returning. This matters: task.cancel() alone only *requests*
        cancellation — the pattern's finally-block stop_device() call
        runs later, asynchronously. Without waiting for it here, a
        direct intensity command issued right after cancelling could
        race against that pending stop and get silently overwritten.

        Pops from _active_patterns *before* awaiting, deliberately: this
        is what lets _on_pattern_task_done() below tell a genuine
        natural completion apart from a cancellation that's about to be
        superseded by something else — by the time this task's done
        callback fires, it's already gone from _active_patterns, so that
        callback correctly does nothing instead of racing whatever new
        value this cancellation is about to make way for."""
        task = self._active_patterns.pop(slug, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                _LOGGER.debug("Pattern task for %s raised during cancellation", slug, exc_info=True)

    def _on_pattern_task_done(self, slug: str, task: asyncio.Task) -> None:
        """Registered via task.add_done_callback() when a pattern starts.
        Fires for every way a pattern task can end — finishing all its
        repeats naturally, being cancelled, or raising unexpectedly.

        Only actually resets the display for a *natural* completion:
        if this exact task is still the one tracked in _active_patterns
        for this slug, nothing else has superseded it (a cancellation
        via _cancel_pattern() above already pops the entry first, before
        awaiting), so the toy really did just stop on its own — the
        number/light entities for this slug should stop showing a stale
        value from before the pattern started, matching what the
        pattern's own finally-block already did to the actual device.
        If something else already claimed this slug (a new pattern, or
        a direct command that cancelled this one), this is a stale
        notification for a task nobody's tracking anymore and must do
        nothing, or it would race whatever that newer command is about
        to display instead."""
        if self._active_patterns.get(slug) is task:
            self._active_patterns.pop(slug, None)
            for callback in self._stop_listeners:
                callback(slug)

    def _prune_finished_patterns(self) -> None:
        """Same "only the first to notice cleans up" logic as
        _on_pattern_task_done() above — whichever of the two runs first
        for a given slug is the one that resets the display; the other
        finds nothing left to do."""
        for slug in [s for s, task in self._active_patterns.items() if task.done()]:
            self._active_patterns.pop(slug, None)
            for callback in self._stop_listeners:
                callback(slug)

    async def async_start_wave_pattern(
        self,
        slug: str,
        repeat: int = 1,
        min_speed_percent: float = 0.0,
        max_speed_percent: float = 50.0,
        duration: float = 3.0,
    ) -> None:
        """*_percent arguments are 0-100. Cancels any pattern already
        running for this slug before starting the new one. Refuses if
        the global stop switch or this device's own Enabled switch is
        off."""
        if self.stopped:
            _LOGGER.warning("Ignoring start_wave_pattern for %s: stop switch is on", slug)
            return
        if slug in self.per_slug_stopped:
            _LOGGER.warning("Ignoring start_wave_pattern for %s: device stop switch is on", slug)
            return
        await self._cancel_pattern(slug)
        self._prune_finished_patterns()
        task = self.hass.async_create_task(
            bp.run_wave_pattern(
                lambda: self._devices_matching(slug),
                slug,
                repeat,
                min_speed_percent / 100.0,
                max_speed_percent / 100.0,
                duration,
            )
        )
        task.add_done_callback(lambda t, s=slug: self._on_pattern_task_done(s, t))
        self._active_patterns[slug] = task

    async def async_start_pulse_pattern(
        self,
        slug: str,
        repeat: int = 1,
        low_speed_percent: float = 0.0,
        high_speed_percent: float = 80.0,
        low_duration: float = 2.0,
        high_duration: float = 2.0,
    ) -> None:
        """*_percent arguments are 0-100. Cancels any pattern already
        running for this slug before starting the new one. Refuses if
        the global stop switch or this device's own Enabled switch is
        off."""
        if self.stopped:
            _LOGGER.warning("Ignoring start_pulse_pattern for %s: stop switch is on", slug)
            return
        if slug in self.per_slug_stopped:
            _LOGGER.warning("Ignoring start_pulse_pattern for %s: device stop switch is on", slug)
            return
        await self._cancel_pattern(slug)
        self._prune_finished_patterns()
        task = self.hass.async_create_task(
            bp.run_pulse_pattern(
                lambda: self._devices_matching(slug),
                slug,
                repeat,
                low_speed_percent / 100.0,
                high_speed_percent / 100.0,
                low_duration,
                high_duration,
            )
        )
        task.add_done_callback(lambda t, s=slug: self._on_pattern_task_done(s, t))
        self._active_patterns[slug] = task

    async def async_stop_pattern(self, slug: str) -> None:
        await self._cancel_pattern(slug)

    def get_device(self, slug: str):
        entry = (self.data or {}).get(slug)
        return entry["device"] if entry else None

    async def async_apply_intensity(self, slug: str, percent: float) -> bool:
        """percent is 0-100. Refuses while the global stop switch OR this
        specific device's own stop switch is on — a real gate, not just a
        one-off stop at the moment either switch was flipped."""
        if self.stopped:
            _LOGGER.warning("Ignoring intensity command for %s: stop switch is on", slug)
            return False
        if slug in self.per_slug_stopped:
            _LOGGER.warning("Ignoring intensity command for %s: device stop switch is on", slug)
            return False
        # A direct intensity command overrides any pattern running on this
        # exact target, same as the standalone bridge's /speed endpoint.
        await self._cancel_pattern(slug)
        dev = self.get_device(slug)
        if dev is None:
            return False
        return await bp.apply_intensity(dev, _clamp(percent, 0, 100) / 100.0)

    async def async_apply_rotation(self, slug: str, signed_percent: float) -> bool:
        """signed_percent is -100..100 — positive clockwise, negative
        counter-clockwise. Same gate checks as async_apply_intensity, and
        also cancels any pattern running on this slug (a direct command
        overriding a running pattern, same as intensity/position do)."""
        if self.stopped:
            _LOGGER.warning("Ignoring rotation command for %s: stop switch is on", slug)
            return False
        if slug in self.per_slug_stopped:
            _LOGGER.warning("Ignoring rotation command for %s: device stop switch is on", slug)
            return False
        await self._cancel_pattern(slug)
        dev = self.get_device(slug)
        if dev is None:
            return False
        return await bp.apply_rotation(dev, _clamp(signed_percent, -100, 100) / 100.0)

    async def async_apply_led(self, slug: str, percent: float) -> bool:
        """percent is 0-100 brightness. Same gate checks as
        async_apply_intensity — a stopped or disabled device refuses
        this too, even though an LED isn't a haptic motor."""
        if self.stopped:
            _LOGGER.warning("Ignoring LED command for %s: stop switch is on", slug)
            return False
        if slug in self.per_slug_stopped:
            _LOGGER.warning("Ignoring LED command for %s: device stop switch is on", slug)
            return False
        dev = self.get_device(slug)
        if dev is None:
            return False
        return await bp.apply_led(dev, _clamp(percent, 0, 100) / 100.0)

    def get_position_duration(self, slug: str) -> int:
        return self.position_duration_ms.get(slug, 0)

    def async_set_position_duration(self, slug: str, duration_ms: int) -> None:
        """Stores the preferred move duration for a slug — purely a
        setting for the next async_apply_position() call, doesn't move
        or command the device by itself. Persisted to the config
        entry's own options, so it survives a Home Assistant restart
        or a reload (e.g. from the options flow's URL change) instead
        of silently resetting to 0."""
        duration_ms = int(_clamp(duration_ms, 0, 10000))
        self.position_duration_ms[slug] = duration_ms
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            options={
                **self.config_entry.options,
                "position_duration_ms": dict(self.position_duration_ms),
            },
        )

    async def async_apply_position(self, slug: str, percent: float, duration_ms: int | None = None) -> bool:
        """percent is 0-100. Refuses while the global stop switch OR this
        specific device's own stop switch is on. If duration_ms isn't
        given explicitly, uses whatever was last set via
        async_set_position_duration() for this slug (0/instant if never
        set)."""
        if self.stopped:
            _LOGGER.warning("Ignoring position command for %s: stop switch is on", slug)
            return False
        if slug in self.per_slug_stopped:
            _LOGGER.warning("Ignoring position command for %s: device stop switch is on", slug)
            return False
        if duration_ms is None:
            duration_ms = self.get_position_duration(slug)
        duration_ms = int(_clamp(duration_ms, 0, 10000))
        dev = self.get_device(slug)
        if dev is None:
            return False
        return await bp.send_position(dev, _clamp(percent, 0, 100) / 100.0, duration_ms)

    async def async_stop_all(self) -> None:
        """Stops every toy immediately and engages the global gate:
        subsequent calls to async_apply_intensity/async_apply_position/
        async_start_pattern are refused until async_clear_stop() is
        called (i.e. the stop switch is turned off again). Also notifies
        any registered stop listeners (the intensity/position number
        entities) so they visually reset to 0."""
        self.stopped = True
        for t in list(self._active_patterns.keys()):
            await self._cancel_pattern(t)
        for dev in self._devices():
            await bp.stop_device(dev)
        for callback in self._stop_listeners:
            callback(None)

    def async_clear_stop(self) -> None:
        """Turns the global gate back off (does not resume anything by
        itself — the next explicit command from an entity/service is what
        moves a toy again)."""
        self.stopped = False

    async def async_stop_device(self, slug: str) -> None:
        """Stops a single toy immediately and engages a per-device gate:
        subsequent commands targeting just this slug are refused until
        async_clear_device_stop() is called. Also cancels any pattern
        running on this slug and notifies stop listeners scoped to this
        slug so its own sliders reset to 0."""
        self.per_slug_stopped.add(slug)
        await self._cancel_pattern(slug)
        dev = self.get_device(slug)
        if dev is not None:
            await bp.stop_device(dev)
        for callback in self._stop_listeners:
            callback(slug)

    def async_clear_device_stop(self, slug: str) -> None:
        """Turns a single device's gate back off."""
        self.per_slug_stopped.discard(slug)

    async def async_shutdown_client(self) -> None:
        for t in list(self._active_patterns.keys()):
            await self._cancel_pattern(t)
        if self._bp_client is not None and hasattr(self._bp_client, "disconnect"):
            try:
                await self._bp_client.disconnect()
            except Exception:
                _LOGGER.debug("Error while disconnecting", exc_info=True)
        self._bp_client = None
        ir.async_delete_issue(self.hass, DOMAIN, _connection_issue_id(self.config_entry.entry_id))
