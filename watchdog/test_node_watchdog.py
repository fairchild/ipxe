#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Behavioural tests for the watchdog. No hardware, no network, no waiting.

Everything runs against a virtual clock, so a three-hour escalation ladder is
exercised in milliseconds. The assertions are about what an operator would
observe — when the outlet was switched, how many times, and whether a human got
told — not about which counters moved inside the policy.

    python3 watchdog/test_node_watchdog.py
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import sys
import unittest
from pathlib import Path
from collections.abc import Callable
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))

import node_watchdog as nw  # noqa: E402

logging.disable(logging.CRITICAL)

POLL = 10.0
DRAIN = 10.0


def config(**overrides: float | int) -> nw.PolicyConfig:
    defaults: dict[str, float | int] = dict(
        failure_threshold=3,
        max_cycles=5,
        boot_timeout=60,
        min_cycle_interval=100,
        backoff_base=100,
        backoff_max=100_000,
        stable_period=300,
    )
    return nw.PolicyConfig(**{**defaults, **overrides})  # type: ignore[arg-type]


class Harness:
    """One watchdog run against a virtual clock, with the alerts it raised."""

    def __init__(
        self,
        probe: Callable[[Harness], bool],
        policy_config: nw.PolicyConfig | None = None,
        deadline: float = 3000.0,
        outlet: nw.Outlet | None = None,
    ) -> None:
        self.clock = nw.VirtualClock()
        self.outlet = outlet or nw.SimulatedOutlet()
        self.cycler = nw.PowerCycler(self.outlet, self.clock, DRAIN)
        self.policy = nw.Policy(policy_config or config())
        self.alerts: list[str] = []
        self._probe = probe
        self.deadline = deadline

    def run(self) -> int:
        return nw.run(
            self.policy,
            lambda: self._probe(self),
            self.cycler,
            self.clock,
            self.alerts.append,
            POLL,
            self.deadline,
        )

    @property
    def cycle_times(self) -> list[float]:
        return [t for t, ok in self.cycler.history if ok]

    @property
    def gaps(self) -> list[float]:
        times = self.cycle_times
        return [b - a for a, b in zip(times, times[1:])]


always_down = lambda _h: False  # noqa: E731
always_up = lambda _h: True  # noqa: E731


class ConsecutiveFailureCounting(unittest.TestCase):
    def test_no_cycle_below_the_threshold(self) -> None:
        polls = iter([False, False, True, False, False, True])
        harness = Harness(lambda _h: next(polls, True), deadline=500)
        harness.run()
        self.assertEqual(harness.cycle_times, [])

    def test_cycles_once_the_run_of_failures_is_unbroken(self) -> None:
        harness = Harness(always_down, deadline=POLL * 3.5)
        harness.run()
        self.assertEqual(len(harness.cycle_times), 1)
        self.assertEqual(harness.cycle_times[0], POLL * 2)  # third poll

    def test_alternating_health_never_cycles(self) -> None:
        flip = iter(range(1000))
        harness = Harness(lambda _h: next(flip) % 2 == 0, deadline=2000)
        harness.run()
        self.assertEqual(harness.cycle_times, [])

    def test_a_healthy_check_clears_the_run(self) -> None:
        polls = iter([False, False, True] * 50)
        harness = Harness(lambda _h: next(polls, True), deadline=2000)
        harness.run()
        self.assertEqual(harness.cycle_times, [])


class BootWindow(unittest.TestCase):
    def test_failures_while_the_node_boots_do_not_trigger_a_second_cycle(self) -> None:
        cfg = config(boot_timeout=600, min_cycle_interval=1, backoff_base=1)
        harness = Harness(always_down, cfg, deadline=500)
        harness.run()
        self.assertEqual(len(harness.cycle_times), 1)

    def test_recovery_during_the_boot_window_is_noticed(self) -> None:
        harness = Harness(lambda h: h.outlet.cycles >= 1, deadline=2000)
        harness.run()
        self.assertEqual(len(harness.cycle_times), 1)
        self.assertEqual(harness.alerts, [])


class Backoff(unittest.TestCase):
    def test_the_wait_between_cycles_doubles(self) -> None:
        cfg = config(backoff_base=200, min_cycle_interval=1, max_cycles=5)
        harness = Harness(always_down, cfg, deadline=20_000)
        harness.run()

        self.assertEqual(len(harness.cycle_times), 5)
        expected = [200.0, 400.0, 800.0, 1600.0]
        for k, (gap, want) in enumerate(zip(harness.gaps, expected), start=1):
            self.assertGreaterEqual(gap, want, f"gap after cycle {k} too short")
            self.assertLess(gap, want + DRAIN + 2 * POLL, f"gap after cycle {k} too long")

    def test_backoff_is_capped(self) -> None:
        cfg = config(backoff_base=200, backoff_max=500, min_cycle_interval=1, max_cycles=5)
        harness = Harness(always_down, cfg, deadline=20_000)
        harness.run()
        for gap in harness.gaps[2:]:
            self.assertLess(gap, 500 + DRAIN + 2 * POLL)


class AntiFlap(unittest.TestCase):
    def test_a_minimum_interval_is_held_even_with_no_backoff(self) -> None:
        cfg = config(backoff_base=1, backoff_max=1, min_cycle_interval=500, max_cycles=4)
        harness = Harness(always_down, cfg, deadline=20_000)
        harness.run()
        self.assertEqual(len(harness.cycle_times), 4)
        for gap in harness.gaps:
            self.assertGreaterEqual(gap, 500)


class HardCap(unittest.TestCase):
    def test_a_node_that_never_comes_back_stops_at_the_cap_and_alerts(self) -> None:
        cfg = config(max_cycles=3)
        harness = Harness(always_down, cfg, deadline=100_000)
        exit_code = harness.run()

        self.assertEqual(exit_code, nw.EXIT_GAVE_UP)
        self.assertEqual(len(harness.cycle_times), 3)
        self.assertEqual(len(harness.alerts), 1)
        self.assertIn("hard cap", harness.alerts[0])

    def test_nothing_happens_after_giving_up(self) -> None:
        cfg = config(max_cycles=2)
        harness = Harness(always_down, cfg, deadline=100_000)
        harness.run()
        before = len(harness.cycle_times)
        harness.run()
        self.assertEqual(len(harness.cycle_times), before)

    def test_a_healthy_node_is_never_touched(self) -> None:
        harness = Harness(always_up, deadline=100_000)
        self.assertEqual(harness.run(), nw.EXIT_OK)
        self.assertEqual(harness.cycle_times, [])
        self.assertEqual(harness.alerts, [])


class Recovery(unittest.TestCase):
    def test_a_node_that_stays_healthy_earns_a_fresh_cycle_budget(self) -> None:
        cfg = config(max_cycles=2, stable_period=300)

        def up_for_a_while_after_the_first_cycle(h: Harness) -> bool:
            if not h.cycle_times:
                return False
            return h.clock.now() - h.cycle_times[0] < 500

        harness = Harness(up_for_a_while_after_the_first_cycle, cfg, deadline=100_000)
        exit_code = harness.run()

        self.assertEqual(exit_code, nw.EXIT_GAVE_UP)
        self.assertEqual(
            len(harness.cycle_times),
            3,
            "one cycle, a stable recovery, then a full fresh budget of two",
        )

    def test_a_brief_recovery_does_not_refill_the_budget(self) -> None:
        cfg = config(max_cycles=3, stable_period=1_000)
        seen: set[int] = set()

        def one_healthy_poll_per_cycle(h: Harness) -> bool:
            count = h.outlet.cycles
            if count and count not in seen:
                seen.add(count)
                return True
            return False

        harness = Harness(one_healthy_poll_per_cycle, cfg, deadline=100_000)
        exit_code = harness.run()

        self.assertEqual(exit_code, nw.EXIT_GAVE_UP)
        self.assertEqual(len(harness.cycle_times), 3)


class UnreachablePlug(unittest.TestCase):
    def test_giving_up_when_the_outlet_cannot_be_switched(self) -> None:
        harness = Harness(
            always_down,
            config(plug_failure_cap=3),
            deadline=100_000,
            outlet=nw.SimulatedOutlet(fail=True),
        )
        exit_code = harness.run()

        self.assertEqual(exit_code, nw.EXIT_GAVE_UP)
        self.assertEqual(harness.cycle_times, [])
        self.assertIn("power controller unreachable", harness.alerts[0])

    def test_a_failed_attempt_does_not_spend_the_cycle_budget(self) -> None:
        harness = Harness(
            always_down,
            config(plug_failure_cap=99, max_cycles=1),
            deadline=1_000,
            outlet=nw.SimulatedOutlet(fail=True),
        )
        harness.run()
        self.assertGreater(len(harness.cycler.history), 1)
        self.assertEqual(harness.policy.cycles_used, 0)


class Defaults(unittest.TestCase):
    def test_dry_run_unless_armed(self) -> None:
        args = nw.parse_args([])
        self.assertFalse(args.arm)
        self.assertIsInstance(nw.build_outlet(args), nw.SimulatedOutlet)

    def test_arming_without_a_plug_is_refused(self) -> None:
        args = nw.parse_args(["--arm"])
        args.plug_host = args.plug_key = ""
        with self.assertRaises(SystemExit):
            nw.build_outlet(args)

    def test_poll_interval_default_is_in_range(self) -> None:
        self.assertTrue(30 <= nw.parse_args([]).poll_interval <= 60)


MACHINES = {
    "machines": [
        {
            "id": "m-1",
            "mac": "dc:a6:32:11:22:33",
            "state": "active",
            "last_seen": "2026-07-31T12:00:00Z",
        },
        {"id": "m-2", "mac": "e4:5f:01:aa:bb:cc", "state": "discovered", "last_seen": None},
    ]
}


class CheckinSignal(unittest.TestCase):
    def probe(self, max_age: float = 300.0) -> nw.Probe:
        return nw.checkin_probe(
            "https://ipxe.example/api/machines", "DC-A6-32-11-22-33", "tok", max_age, 5
        )

    def at(self, iso: str) -> float:
        return nw.parse_timestamp(iso)  # type: ignore[return-value]

    def test_a_recent_checkin_reads_as_healthy(self) -> None:
        with mock.patch.object(nw, "_http_get", return_value=json.dumps(MACHINES)), \
             mock.patch.object(nw.time, "time", return_value=self.at("2026-07-31T12:01:00Z")):
            self.assertTrue(self.probe()())

    def test_a_stale_checkin_reads_as_down(self) -> None:
        with mock.patch.object(nw, "_http_get", return_value=json.dumps(MACHINES)), \
             mock.patch.object(nw.time, "time", return_value=self.at("2026-07-31T13:00:00Z")):
            self.assertFalse(self.probe()())

    def test_an_unknown_mac_reads_as_down(self) -> None:
        with mock.patch.object(nw, "_http_get", return_value=json.dumps({"machines": []})):
            self.assertFalse(self.probe()())

    def test_an_unreachable_control_plane_does_not_cut_power(self) -> None:
        with mock.patch.object(nw, "_http_get", side_effect=OSError("no route")):
            self.assertTrue(
                self.probe()(), "a broken watchdog must not be mistaken for a broken node"
            )

    def test_mac_formatting_does_not_matter(self) -> None:
        self.assertEqual(nw.normalize_mac("DC-A6-32-11-22-33"), "dca632112233")
        self.assertIsNotNone(find := nw.find_record(MACHINES, "dca632112233"))
        self.assertEqual(find["id"], "m-1")  # type: ignore[index]

    def test_epoch_timestamps_in_seconds_or_milliseconds(self) -> None:
        self.assertEqual(nw.parse_timestamp(1_753_996_800), 1_753_996_800.0)
        self.assertEqual(nw.parse_timestamp(1_753_996_800_000), 1_753_996_800.0)


class FakeResponse(io.BytesIO):
    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None


class MerossWireFormat(unittest.TestCase):
    """The envelope is a contract with the device, so it is worth pinning."""

    def send(self, replies: list[dict]) -> list[dict]:
        sent: list[dict] = []

        def fake_urlopen(request, timeout=None):  # type: ignore[no-untyped-def]
            sent.append(json.loads(request.data))
            return FakeResponse(json.dumps(replies[len(sent) - 1]).encode())

        outlet = nw.MerossOutlet("10.0.0.9", "s3cr3t", channel=2)
        with mock.patch("urllib.request.urlopen", fake_urlopen):
            outlet.set(True)
        return sent

    def test_switching_an_outlet_sends_a_signed_togglex_for_that_channel(self) -> None:
        digest = {"payload": {"all": {"digest": {"togglex": [{"channel": 2, "onoff": 1}]}}}}
        sent = self.send([{"payload": {}}, digest])

        header, payload = sent[0]["header"], sent[0]["payload"]
        self.assertEqual(header["namespace"], "Appliance.Control.ToggleX")
        self.assertEqual(header["method"], "SET")
        self.assertEqual(payload, {"togglex": {"channel": 2, "onoff": 1}})
        self.assertEqual(
            header["sign"],
            hashlib.md5(
                f"{header['messageId']}s3cr3t{header['timestamp']}".encode()
            ).hexdigest(),
        )

    def test_a_rejected_key_is_reported_not_swallowed(self) -> None:
        with self.assertRaises(nw.OutletError) as caught:
            self.send([{"payload": {"error": {"code": 5001, "detail": "sign error"}}}])
        self.assertIn("MEROSS_KEY", str(caught.exception))

    def test_an_outlet_that_ignores_the_command_counts_as_a_failure(self) -> None:
        unchanged = {"payload": {"all": {"digest": {"togglex": [{"channel": 2, "onoff": 0}]}}}}
        with self.assertRaises(nw.OutletError):
            self.send([{"payload": {}}, unchanged])


class SimulationMode(unittest.TestCase):
    def test_the_default_invocation_switches_nothing_and_ends_at_a_human(self) -> None:
        exit_code = nw.main(
            ["--fast", "--deadline", "100000", "--poll-interval", "10",
             "--boot-timeout", "60", "--min-cycle-interval", "60",
             "--backoff-base", "60", "--max-cycles", "3"]
        )
        self.assertEqual(exit_code, nw.EXIT_GAVE_UP)

    def test_a_node_that_comes_back_ends_quietly(self) -> None:
        exit_code = nw.main(
            ["--fast", "--deadline", "5000", "--poll-interval", "10",
             "--health", "sim:recover-after-cycle:1", "--boot-timeout", "60"]
        )
        self.assertEqual(exit_code, nw.EXIT_OK)


if __name__ == "__main__":
    unittest.main(verbosity=2)
