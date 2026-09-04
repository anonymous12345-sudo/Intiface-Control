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
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import client as bp
from .const import (
    CLIENT_NAME,
    CONF_FALLBACK_URL,
    CONF_URL,
    DOMAIN,
    MAX_CONSECUTIVE_FAILURES,
    UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class ButtplugCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Coordinates the Intiface connection and the list of known devices."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.entry = entry
        self.url: str = entry.data[CONF_URL]
        self.fallback_url: str | None = entry.data.get(CONF_FALLBACK_URL) or None

        self._bp_client = None
        self._connect_lock = asyncio.Lock()
        self._consecutive_failures = 0

        # Running pattern tasks, keyed by target (slug or "all"/"both").
        # Mirrors the standalone bridge's active_patterns dict.
        self._active_patterns: dict[str, asyncio.Task] = {}

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
        # Callbacks platforms register to get called with newly seen
        # devices: list[tuple[slug, device_obj, capabilities]].
        self._new_device_listeners: list = []
        # Callbacks number entities register to reset their displayed
        # value (e.g. an intensity slider back to 0) the moment a stop
        # engages — see async_stop_all()/async_stop_device(). Called with
        # a single argument: the affected slug, or None for "all devices"
        # (a global stop). Entities compare that against their own slug.
        self._stop_listeners: list = []

    def add_stop_listener(self, callback) -> None:
        """Number entities register here so they visually reset to 0 as
        soon as a stop (global or per-device) is engaged, instead of
        showing a stale slider position until the next interaction.
        `callback` is invoked with one argument: the affected slug, or
        None if every device is affected."""
        self._stop_listeners.append(callback)

    def add_new_device_listener(self, callback) -> None:
        """Platforms register here to be notified about newly discovered
        devices, so they can create entities for them dynamically."""
        self._new_device_listeners.append(callback)

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

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        try:
            await self._ensure_client()
            self._consecutive_failures = 0
        except UpdateFailed:
            self._consecutive_failures += 1
            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                _LOGGER.warning(
                    "Intiface unreachable for %d consecutive updates, marking all devices offline",
                    self._consecutive_failures,
                )
                return {}
            # Keep the last-known snapshot for a few cycles so a single
            # transient blip doesn't flash every device offline.
            return self.data or {}

        devs = self._devices()
        data: dict[str, dict[str, Any]] = {}
        new_devices = []
        for dev in devs:
            slug = bp.device_slug(dev)
            caps = bp.get_capabilities(dev)
            battery = None
            if "battery" in caps:
                battery = await bp.read_battery(dev)
            data[slug] = {
                "name": getattr(dev, "name", slug),
                "capabilities": caps,
                "battery": battery,
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

    def _devices_matching(self, target: str) -> list:
        """Devices belonging to `target` — a device slug, or "all"/"both"
        for everything. Re-evaluated fresh on every call (not cached), so
        it stays correct even if a device reconnects with a new object
        instance mid-pattern. Devices under a per-device stop are always
        excluded here, even when `target` is "all"/"both" — this is what
        makes a running "all" pattern automatically skip a toy the moment
        its own stop switch engages, without any extra cancellation logic."""
        target = (target or "").strip().lower()
        devs = [d for d in self._devices() if bp.device_slug(d) not in self.per_slug_stopped]
        if target in ("all", "both", ""):
            return devs
        return [d for d in devs if bp.device_slug(d) == target]

    async def _cancel_pattern(self, target: str) -> None:
        """Cancels any pattern running for `target` and waits for its
        cleanup (which stops the device) to actually finish before
        returning. This matters: task.cancel() alone only *requests*
        cancellation — the pattern's finally-block stop_device() call
        runs later, asynchronously. Without waiting for it here, a
        direct intensity command issued right after cancelling could
        race against that pending stop and get silently overwritten."""
        task = self._active_patterns.pop(target, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                _LOGGER.debug("Pattern task for %s raised during cancellation", target, exc_info=True)

    def _prune_finished_patterns(self) -> None:
        for t in [t for t, task in self._active_patterns.items() if task.done()]:
            self._active_patterns.pop(t, None)

    async def async_start_pattern(
        self,
        target: str,
        pattern_type: str,
        repeat: int = 1,
        min_speed_percent: float = 0.0,
        max_speed_percent: float = 50.0,
        wave_duration: float = 3.0,
        low_speed_percent: float = 0.0,
        high_speed_percent: float = 80.0,
        low_duration: float = 2.0,
        high_duration: float = 2.0,
    ) -> None:
        """All *_percent arguments are 0-100. Cancels any pattern already
        running for this exact target string before starting the new one
        — note that "lovense_hush" and "all" are tracked as separate
        targets, so starting a pattern on "all" does not automatically
        cancel one already running on a specific device slug (matches
        the standalone bridge's behaviour).

        A specific device slug that's individually stopped refuses to
        start at all here; a stopped device inside an "all"/"both" pattern
        is instead silently excluded on every tick by _devices_matching()
        — the pattern still starts and runs for the other devices."""
        if self.stopped:
            _LOGGER.warning("Ignoring start_pattern for %s: stop switch is on", target)
            return
        if target in self.per_slug_stopped:
            _LOGGER.warning("Ignoring start_pattern for %s: device stop switch is on", target)
            return
        await self._cancel_pattern(target)
        self._prune_finished_patterns()
        task = self.hass.async_create_task(
            bp.run_pattern_loop(
                lambda: self._devices_matching(target),
                pattern_type,
                target,
                repeat,
                min_speed=min_speed_percent / 100.0,
                max_speed=max_speed_percent / 100.0,
                wave_duration=wave_duration,
                low_speed=low_speed_percent / 100.0,
                high_speed=high_speed_percent / 100.0,
                low_duration=low_duration,
                high_duration=high_duration,
            )
        )
        self._active_patterns[target] = task

    async def async_stop_pattern(self, target: str) -> None:
        await self._cancel_pattern(target)

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
        return await bp.apply_intensity(dev, percent / 100.0)

    async def async_apply_position(self, slug: str, percent: float, duration_ms: int = 0) -> bool:
        """percent is 0-100. Refuses while the global stop switch OR this
        specific device's own stop switch is on."""
        if self.stopped:
            _LOGGER.warning("Ignoring position command for %s: stop switch is on", slug)
            return False
        if slug in self.per_slug_stopped:
            _LOGGER.warning("Ignoring position command for %s: device stop switch is on", slug)
            return False
        dev = self.get_device(slug)
        if dev is None:
            return False
        return await bp.send_position(dev, percent / 100.0, duration_ms)

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
        running specifically on this device's own slug (a pattern running
        on "all"/"both" instead just silently stops including this device
        on its next tick, via _devices_matching()) and notifies stop
        listeners scoped to this slug so its own sliders reset to 0."""
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
