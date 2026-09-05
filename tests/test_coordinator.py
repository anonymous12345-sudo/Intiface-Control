"""Tests for IntifaceCoordinator: device discovery, the emergency-stop
gate, pattern handling, and the cancellation race-condition fix.

The real `buttplug.ButtplugClient` tries to open an actual WebSocket
connection on .connect() — no good for unit tests. We monkeypatch it
with a FakeButtplugClient here, and pre-populate `coordinator._bp_client`
directly (bypassing the connect/scan flow, including its real 2-second
scan-window sleep) so tests stay fast.

Note: these tests call `coordinator.async_refresh()`, not
`async_request_refresh()`. The latter goes through HA's internal
Debouncer and doesn't complete synchronously even when awaited — a test
asserting on `coordinator.data` right after would see stale data.
`async_refresh()` is the immediate, non-debounced equivalent.
"""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.intiface_control import client as bp
from custom_components.intiface_control.const import CONF_FALLBACK_URL, CONF_URL, DOMAIN
from custom_components.intiface_control.coordinator import IntifaceCoordinator


class FakeButtplugClient:
    """Replaces the real ButtplugClient for tests: no real connection,
    devices are whatever the test puts in `.devices`."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.devices: dict = {}

    async def connect(self, url: str) -> None:
        pass

    async def start_scanning(self) -> None:
        pass

    async def stop_scanning(self) -> None:
        pass


@pytest.fixture
def mock_bp_client(monkeypatch):
    monkeypatch.setattr(bp, "ButtplugClient", FakeButtplugClient)


@pytest.fixture
async def coordinator(hass, mock_bp_client):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_URL: "ws://fake:12345", CONF_FALLBACK_URL: None},
    )
    entry.add_to_hass(hass)
    coord = IntifaceCoordinator(hass, entry)
    # Bypass the real connect/scan flow (and its 2s scan-window sleep) —
    # tests only care about behaviour once a connection already exists.
    coord._bp_client = FakeButtplugClient("test")
    return coord


@pytest.mark.asyncio
async def test_first_refresh_discovers_devices_and_capabilities(coordinator, fake_device) -> None:
    coordinator._bp_client.devices = {
        0: fake_device("Lovense Hush", outputs={bp.VIBRATE}, battery=0.9),
    }
    await coordinator.async_refresh()

    assert "lovense_hush" in coordinator.data
    assert coordinator.data["lovense_hush"]["capabilities"] == ["vibrate", "battery"]
    assert coordinator.data["lovense_hush"]["battery"] == 90.0


@pytest.mark.asyncio
async def test_new_device_listener_fires_for_devices_added_later(coordinator, fake_device) -> None:
    await coordinator.async_refresh()

    seen: list = []
    coordinator.add_new_device_listener(lambda batch: seen.append(batch))

    coordinator._bp_client.devices[1] = fake_device("Brand New Toy", outputs={bp.ROTATE})
    await coordinator.async_refresh()

    assert len(seen) == 1
    assert seen[0][0][0] == "brand_new_toy"


@pytest.mark.asyncio
async def test_offline_device_disappears_from_data_but_isnt_an_error(coordinator, fake_device) -> None:
    coordinator._bp_client.devices = {0: fake_device("Lovense Hush", outputs={bp.VIBRATE})}
    await coordinator.async_refresh()
    assert "lovense_hush" in coordinator.data

    coordinator._bp_client.devices = {}
    await coordinator.async_refresh()
    assert "lovense_hush" not in coordinator.data
    # A device disappearing is a legitimate "toy went offline" state —
    # entities should show unavailable, this is not treated as an error.
    assert coordinator.get_device("lovense_hush") is None


@pytest.mark.asyncio
async def test_apply_intensity_on_offline_device_returns_false_not_error(coordinator) -> None:
    await coordinator.async_refresh()
    ok = await coordinator.async_apply_intensity("does_not_exist", 50)
    assert ok is False


@pytest.mark.asyncio
async def test_emergency_stop_blocks_further_commands(coordinator, fake_device) -> None:
    """The actual bug reported during development: the stop switch used
    to only perform a one-off stop, without blocking anything sent
    afterwards. This is the regression test for that fix."""
    coordinator._bp_client.devices = {0: fake_device("Lovense Hush", outputs={bp.VIBRATE})}
    await coordinator.async_refresh()

    await coordinator.async_stop_all()
    assert coordinator.stopped is True

    ok = await coordinator.async_apply_intensity("lovense_hush", 55)
    assert ok is False, "command should have been refused while stopped"

    coordinator.async_clear_stop()
    assert coordinator.stopped is False

    ok = await coordinator.async_apply_intensity("lovense_hush", 55)
    assert ok is True, "command should work again once the gate is cleared"


@pytest.mark.asyncio
async def test_stop_all_notifies_stop_listeners(coordinator, fake_device) -> None:
    coordinator._bp_client.devices = {0: fake_device("Lovense Hush", outputs={bp.VIBRATE})}
    await coordinator.async_refresh()

    calls = []
    coordinator.add_stop_listener(lambda affected_slug: calls.append(affected_slug))
    await coordinator.async_stop_all()
    # None means "every device is affected" — a global stop, not scoped
    # to one slug.
    assert calls == [None]


@pytest.mark.asyncio
async def test_device_stop_blocks_only_that_device(coordinator, fake_device) -> None:
    """Regression coverage for the per-device Enabled switch: disabling
    one toy must refuse commands to it while leaving every other toy —
    and the global stop switch's own state — completely unaffected."""
    hush = fake_device("Lovense Hush", outputs={bp.VIBRATE})
    hismith = fake_device("Hismith Sex Machine", outputs={bp.OSCILLATE})
    coordinator._bp_client.devices = {0: hush, 1: hismith}
    await coordinator.async_refresh()

    await coordinator.async_stop_device("lovense_hush")
    assert "lovense_hush" in coordinator.per_slug_stopped
    assert hush.sent[-1] == ("STOP", None)

    ok = await coordinator.async_apply_intensity("lovense_hush", 50)
    assert ok is False, "disabled device should refuse commands"

    ok = await coordinator.async_apply_intensity("hismith_sex_machine", 50)
    assert ok is True, "other devices must be unaffected by one device's own stop"

    assert coordinator.stopped is False, "a per-device stop must not engage the global gate"

    coordinator.async_clear_device_stop("lovense_hush")
    assert "lovense_hush" not in coordinator.per_slug_stopped
    ok = await coordinator.async_apply_intensity("lovense_hush", 50)
    assert ok is True, "re-enabling should restore normal operation"


@pytest.mark.asyncio
async def test_device_stop_notifies_listener_with_its_own_slug_only(coordinator, fake_device) -> None:
    coordinator._bp_client.devices = {
        0: fake_device("Lovense Hush", outputs={bp.VIBRATE}),
        1: fake_device("Hismith Sex Machine", outputs={bp.OSCILLATE}),
    }
    await coordinator.async_refresh()

    calls = []
    coordinator.add_stop_listener(lambda affected_slug: calls.append(affected_slug))
    await coordinator.async_stop_device("lovense_hush")

    assert calls == ["lovense_hush"], "a per-device stop must only notify with its own slug"


@pytest.mark.asyncio
async def test_disabled_device_refuses_wave_pattern_start(coordinator, fake_device) -> None:
    """With "all"/"both" targeting removed, a per-device stop now simply
    refuses to start a pattern on that device at all — there's no more
    multi-device pattern for it to be silently excluded from."""
    hush = fake_device("Lovense Hush", outputs={bp.VIBRATE})
    coordinator._bp_client.devices = {0: hush}
    await coordinator.async_refresh()

    await coordinator.async_stop_device("lovense_hush")
    hush.sent.clear()

    await coordinator.async_start_wave_pattern("lovense_hush", duration=1, max_speed_percent=80)
    await asyncio.sleep(1.3)

    assert hush.sent == [], "a disabled device must refuse to start a pattern entirely"


@pytest.mark.asyncio
async def test_pattern_cancellation_completes_before_next_command_is_sent(coordinator, fake_device) -> None:
    """Regression test for a real race condition found during
    development: cancelling a running pattern only *requests*
    cancellation — the pattern's own cleanup (which stops the device)
    runs asynchronously afterwards. Without waiting for that cleanup to
    finish before sending a new command, a directly-set intensity value
    could get silently overwritten by the old pattern's delayed stop.
    This asserts the fix: the explicit command is always the last thing
    sent, with nothing arriving after it."""
    dev = fake_device("Lovense Hush", outputs={bp.VIBRATE})
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_refresh()

    await coordinator.async_start_wave_pattern("lovense_hush", duration=10, max_speed_percent=90)
    await asyncio.sleep(0.3)

    await coordinator.async_apply_intensity("lovense_hush", 42)
    assert dev.sent[-1] == (bp.VIBRATE, (0.42,))

    n_after = len(dev.sent)
    await asyncio.sleep(0.5)
    assert len(dev.sent) == n_after, "nothing should have been sent after the explicit command"


@pytest.mark.asyncio
async def test_pattern_start_and_stop(coordinator, fake_device) -> None:
    dev = fake_device("Lovense Hush", outputs={bp.VIBRATE})
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_refresh()

    await coordinator.async_start_wave_pattern("lovense_hush", duration=1, max_speed_percent=80)
    await asyncio.sleep(1.3)

    assert len(dev.sent) > 3, "expected several commands during a 1s wave pattern"
    assert dev.sent[-1] == ("STOP", None), "pattern should stop the device when it finishes"


@pytest.mark.asyncio
async def test_wave_pattern_rises_and_falls_symmetrically(coordinator, fake_device) -> None:
    """A single wave must start near min_speed, peak near max_speed at
    its midpoint, and come back down — not the old continuous
    oscillation, and not a square wave."""
    dev = fake_device("Lovense Hush", outputs={bp.VIBRATE})
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_refresh()

    await coordinator.async_start_wave_pattern(
        "lovense_hush", duration=3, min_speed_percent=10, max_speed_percent=90
    )
    await asyncio.sleep(3.3)

    speeds = [v[0] for _, v in dev.sent[:-1]]
    assert speeds[0] == pytest.approx(0.1, abs=0.01)
    peak = max(speeds)
    assert peak > 0.85
    assert speeds.index(peak) == len(speeds) // 2 or abs(speeds.index(peak) - len(speeds) // 2) <= 1
    assert speeds[-1] < peak - 0.3, "must have come back down from the peak by the end"


@pytest.mark.asyncio
async def test_pulse_pattern_holds_low_then_high(coordinator, fake_device) -> None:
    dev = fake_device("Lovense Hush", outputs={bp.VIBRATE})
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_refresh()

    await coordinator.async_start_pulse_pattern(
        "lovense_hush",
        low_speed_percent=10, high_speed_percent=90,
        low_duration=0.4, high_duration=0.4,
    )
    await asyncio.sleep(1.0)

    speeds = [v[0] for _, v in dev.sent[:-1]]
    n = len(speeds)
    first_half = speeds[: n // 2]
    second_half = speeds[n // 2 :]
    assert all(v == pytest.approx(0.1, abs=0.01) for v in first_half)
    assert all(v == pytest.approx(0.9, abs=0.01) for v in second_half)


@pytest.mark.asyncio
async def test_pattern_repeat_runs_multiple_cycles(coordinator, fake_device) -> None:
    dev = fake_device("Lovense Hush", outputs={bp.VIBRATE})
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_refresh()

    await coordinator.async_start_pulse_pattern(
        "lovense_hush", repeat=3,
        low_speed_percent=10, high_speed_percent=90,
        low_duration=0.2, high_duration=0.2,
    )
    await asyncio.sleep(1.5)

    speeds = [v[0] for _, v in dev.sent[:-1]]
    transitions = sum(1 for i in range(1, len(speeds)) if speeds[i - 1] < 0.5 < speeds[i])
    assert transitions == 3, f"repeat=3 should produce exactly 3 low->high transitions, got {transitions}"


@pytest.mark.asyncio
async def test_duplicate_device_names_get_disambiguated(coordinator, fake_device) -> None:
    """Regression test: two toys sharing the same name (e.g. two of the
    same model) used to silently collide into a single dict entry,
    losing one of them entirely with no error. They must now each get
    their own slug, and remain independently controllable."""
    hush_a = fake_device("Lovense Hush", outputs={bp.VIBRATE})
    hush_a.index = 0
    hush_b = fake_device("Lovense Hush", outputs={bp.VIBRATE})
    hush_b.index = 1
    coordinator._bp_client.devices = {0: hush_a, 1: hush_b}
    await coordinator.async_refresh()

    assert len(coordinator.data) == 2, f"both devices must get their own entry, got {list(coordinator.data)}"
    assert "lovense_hush_0" in coordinator.data
    assert "lovense_hush_1" in coordinator.data

    await coordinator.async_apply_intensity("lovense_hush_0", 40)
    await coordinator.async_apply_intensity("lovense_hush_1", 70)
    assert hush_a.sent[-1] == (bp.VIBRATE, (0.4,))
    assert hush_b.sent[-1] == (bp.VIBRATE, (0.7,))


@pytest.mark.asyncio
async def test_lone_device_keeps_plain_slug_when_no_collision(coordinator, fake_device) -> None:
    """A single device must not be disambiguated just because the
    disambiguation logic exists — only an actual name collision should
    change its slug from the plain, stable one."""
    dev = fake_device("Lovense Hush", outputs={bp.VIBRATE})
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_refresh()

    assert list(coordinator.data) == ["lovense_hush"]


class FailingButtplugClient:
    """Always fails to connect — used to exercise the repair-issue path,
    which only engages after several consecutive real connection
    failures (not the pre-populated-devices bypass the other tests use)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.devices: dict = {}
        self.fail_count = 0

    async def connect(self, url: str) -> None:
        self.fail_count += 1
        raise ConnectionError("simulated failure")

    async def start_scanning(self) -> None:
        pass

    async def stop_scanning(self) -> None:
        pass


@pytest.mark.asyncio
async def test_repair_issue_created_after_repeated_connection_failures(hass, monkeypatch) -> None:
    monkeypatch.setattr(bp, "ButtplugClient", FailingButtplugClient)

    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_URL: "ws://unreachable:12345", CONF_FALLBACK_URL: None}
    )
    entry.add_to_hass(hass)
    coord = IntifaceCoordinator(hass, entry)

    for _ in range(3):
        await coord.async_refresh()

    issue_reg = ir.async_get(hass)
    issue = issue_reg.async_get_issue(DOMAIN, f"cannot_connect_{entry.entry_id}")
    assert issue is not None
    assert issue.translation_placeholders == {"url": "ws://unreachable:12345"}


@pytest.mark.asyncio
async def test_repair_issue_cleared_once_connection_recovers(hass, monkeypatch) -> None:
    monkeypatch.setattr(bp, "ButtplugClient", FailingButtplugClient)

    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_URL: "ws://flaky:12345", CONF_FALLBACK_URL: None}
    )
    entry.add_to_hass(hass)
    coord = IntifaceCoordinator(hass, entry)

    for _ in range(3):
        await coord.async_refresh()

    issue_reg = ir.async_get(hass)
    assert issue_reg.async_get_issue(DOMAIN, f"cannot_connect_{entry.entry_id}") is not None

    # Swap in a working client and force a fresh connect attempt.
    monkeypatch.setattr(bp, "ButtplugClient", lambda name: FakeButtplugClient(name))
    coord._bp_client = None
    await coord.async_refresh()

    assert issue_reg.async_get_issue(DOMAIN, f"cannot_connect_{entry.entry_id}") is None


@pytest.mark.asyncio
async def test_repair_issue_cleared_on_shutdown(hass, monkeypatch) -> None:
    monkeypatch.setattr(bp, "ButtplugClient", FailingButtplugClient)

    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_URL: "ws://unreachable:12345", CONF_FALLBACK_URL: None}
    )
    entry.add_to_hass(hass)
    coord = IntifaceCoordinator(hass, entry)

    for _ in range(3):
        await coord.async_refresh()

    issue_reg = ir.async_get(hass)
    assert issue_reg.async_get_issue(DOMAIN, f"cannot_connect_{entry.entry_id}") is not None

    await coord.async_shutdown_client()
    assert issue_reg.async_get_issue(DOMAIN, f"cannot_connect_{entry.entry_id}") is None


@pytest.mark.asyncio
async def test_battery_polled_immediately_for_a_new_device(coordinator, fake_device) -> None:
    """A device must never show as connected without a battery value
    just because the 60s battery-poll interval hasn't elapsed yet."""
    dev = fake_device("Lovense Hush", outputs={bp.VIBRATE}, battery=0.9)
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_refresh()

    assert coordinator.data["lovense_hush"]["battery"] == 90.0


@pytest.mark.asyncio
async def test_battery_not_repolled_within_the_interval(coordinator, fake_device) -> None:
    dev = fake_device("Lovense Hush", outputs={bp.VIBRATE}, battery=0.9)
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_refresh()
    assert coordinator.data["lovense_hush"]["battery"] == 90.0

    dev._battery = 0.5
    await coordinator.async_refresh()

    assert coordinator.data["lovense_hush"]["battery"] == 90.0, (
        "a poll within the interval must reuse the cached value, not fetch again"
    )


@pytest.mark.asyncio
async def test_battery_repolled_after_the_interval_elapses(coordinator, fake_device) -> None:
    dev = fake_device("Lovense Hush", outputs={bp.VIBRATE}, battery=0.9)
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_refresh()

    coordinator._last_battery_poll["lovense_hush"] -= 61
    dev._battery = 0.5
    await coordinator.async_refresh()

    assert coordinator.data["lovense_hush"]["battery"] == 50.0


@pytest.mark.asyncio
async def test_position_duration_defaults_to_instant_move(coordinator, fake_device) -> None:
    """Before any duration is ever set for a slug, moves must behave
    exactly as they did before this feature existed — instant."""
    dev = fake_device("Simulated Stroker", outputs={bp.POSITION, bp.POSITION_WITH_DURATION})
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_refresh()

    assert coordinator.get_position_duration("simulated_stroker") == 0

    ok = await coordinator.async_apply_position("simulated_stroker", 60)
    assert ok is True
    assert dev.sent[0][0] == bp.POSITION, "with no duration set, should use plain Position, not PositionWithDuration"


@pytest.mark.asyncio
async def test_position_duration_is_stored_and_reused(coordinator, fake_device) -> None:
    dev = fake_device("Simulated Stroker", outputs={bp.POSITION, bp.POSITION_WITH_DURATION})
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_refresh()

    coordinator.async_set_position_duration("simulated_stroker", 2500)
    assert coordinator.get_position_duration("simulated_stroker") == 2500

    await coordinator.async_apply_position("simulated_stroker", 80)
    assert dev.sent[-1] == (bp.POSITION_WITH_DURATION, (0.8, 2500))


@pytest.mark.asyncio
async def test_position_duration_can_be_overridden_explicitly(coordinator, fake_device) -> None:
    """An explicit duration_ms argument still takes priority over the
    stored preference, for any future caller that wants to override it
    for one specific move."""
    dev = fake_device("Simulated Stroker", outputs={bp.POSITION, bp.POSITION_WITH_DURATION})
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_refresh()

    coordinator.async_set_position_duration("simulated_stroker", 2500)
    await coordinator.async_apply_position("simulated_stroker", 50, duration_ms=100)
    assert dev.sent[-1] == (bp.POSITION_WITH_DURATION, (0.5, 100))


@pytest.mark.asyncio
async def test_position_duration_setting_alone_never_commands_the_device(coordinator, fake_device) -> None:
    dev = fake_device("Simulated Stroker", outputs={bp.POSITION, bp.POSITION_WITH_DURATION})
    coordinator._bp_client.devices = {0: dev}
    await coordinator.async_refresh()

    coordinator.async_set_position_duration("simulated_stroker", 3000)
    assert dev.sent == []
