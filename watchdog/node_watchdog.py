#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Out-of-band power-cycle watchdog for the netboot lab.

Watches one node's health signal and, when the node is genuinely gone, cuts and
restores its outlet — then converges on telling a human. A deterministic failure
does not improve on the fifth power cut, so the cycle budget is finite by design.

Dry run is the default; real switching needs --arm.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from collections.abc import Callable, Iterator
from typing import Any, Protocol

LOG = logging.getLogger("watchdog")

EXIT_OK = 0
EXIT_GAVE_UP = 2
EXIT_MISCONFIGURED = 3


# --------------------------------------------------------------------------- clock


class Clock(Protocol):
    def now(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class RealClock:
    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class VirtualClock:
    """Deterministic clock: sleeping advances time instead of spending it."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += seconds


# --------------------------------------------------------------------------- policy


class Action(Enum):
    WAIT = "wait"
    CYCLE = "cycle"
    GIVE_UP = "give_up"


@dataclass(frozen=True, slots=True)
class Verdict:
    action: Action
    reason: str


@dataclass(frozen=True, slots=True)
class PolicyConfig:
    failure_threshold: int = 3
    max_cycles: int = 5
    boot_timeout: float = 180.0
    min_cycle_interval: float = 300.0
    backoff_base: float = 300.0
    backoff_max: float = 3600.0
    stable_period: float = 900.0
    plug_failure_cap: int = 3


class Policy:
    """The escalation state machine. Pure: no I/O, no wall clock of its own.

    Time only enters through the `now` argument, so the whole ladder — threshold,
    boot window, anti-flap floor, backoff, hard cap — is exercisable in tests
    without waiting for any of it.
    """

    def __init__(self, config: PolicyConfig) -> None:
        self.config = config
        self.consecutive_failures = 0
        self.cycles_used = 0
        self.plug_failures = 0
        self.last_cycle_at: float | None = None
        self.last_attempt_at: float | None = None
        self.healthy_since: float | None = None
        self.stopped = False

    def observe(self, healthy: bool, now: float) -> Verdict:
        if self.stopped:
            return Verdict(Action.GIVE_UP, "already stopped")
        return self._on_healthy(now) if healthy else self._on_failure(now)

    def _on_healthy(self, now: float) -> Verdict:
        self.consecutive_failures = 0
        self.plug_failures = 0
        if self.healthy_since is None:
            self.healthy_since = now
        stable_for = now - self.healthy_since
        if self.cycles_used and stable_for >= self.config.stable_period:
            spent = self.cycles_used
            self.cycles_used = 0
            self.last_cycle_at = None
            self.last_attempt_at = None
            return Verdict(
                Action.WAIT,
                f"healthy {stable_for:.0f}s — cycle budget reset (was {spent} spent)",
            )
        return Verdict(Action.WAIT, "healthy")

    def _on_failure(self, now: float) -> Verdict:
        self.healthy_since = None

        if self.last_cycle_at is not None:
            booting_for = now - self.last_cycle_at
            if booting_for < self.config.boot_timeout:
                remaining = self.config.boot_timeout - booting_for
                return Verdict(Action.WAIT, f"boot window, {remaining:.0f}s left")

        self.consecutive_failures += 1
        if self.consecutive_failures < self.config.failure_threshold:
            return Verdict(
                Action.WAIT,
                f"failure {self.consecutive_failures}/{self.config.failure_threshold}",
            )

        if self.cycles_used >= self.config.max_cycles:
            self.stopped = True
            return Verdict(
                Action.GIVE_UP,
                f"still down after {self.cycles_used} power cycles — hard cap reached, "
                "the fault is not one a power cut fixes",
            )

        if self.last_attempt_at is not None:
            earliest = self.last_attempt_at + self.cooldown()
            if now < earliest:
                return Verdict(
                    Action.WAIT, f"cooldown, next cycle in {earliest - now:.0f}s"
                )

        return Verdict(
            Action.CYCLE,
            f"down for {self.consecutive_failures} consecutive checks — "
            f"cycle {self.cycles_used + 1}/{self.config.max_cycles}",
        )

    def cooldown(self) -> float:
        """Minimum spacing between cycle attempts: anti-flap floor, then backoff."""
        exponent = max(self.cycles_used, 1) - 1
        backoff = self.config.backoff_base * 2**exponent
        return max(self.config.min_cycle_interval, min(self.config.backoff_max, backoff))

    def record_cycle(self, now: float, succeeded: bool) -> Verdict:
        self.last_attempt_at = now
        if succeeded:
            self.cycles_used += 1
            self.plug_failures = 0
            self.consecutive_failures = 0
            self.last_cycle_at = now
            return Verdict(
                Action.WAIT,
                f"cycled ({self.cycles_used}/{self.config.max_cycles}), "
                f"waiting {self.config.boot_timeout:.0f}s for boot",
            )
        self.plug_failures += 1
        if self.plug_failures >= self.config.plug_failure_cap:
            self.stopped = True
            return Verdict(
                Action.GIVE_UP,
                f"power controller unreachable {self.plug_failures}x — "
                "cannot recover a node whose outlet we cannot reach",
            )
        return Verdict(
            Action.WAIT,
            f"power cycle failed ({self.plug_failures}/{self.config.plug_failure_cap})",
        )


# --------------------------------------------------------------------------- probes

Probe = Callable[[], bool]


def ping_probe(host: str, timeout: float) -> Probe:
    def check() -> bool:
        try:
            done = subprocess.run(
                ["ping", "-c", "1", host],
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            LOG.debug("ping %s: %s", host, exc)
            return False
        return done.returncode == 0

    return check


def tcp_probe(host: str, port: int, timeout: float) -> Probe:
    def check() -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError as exc:
            LOG.debug("tcp %s:%d: %s", host, port, exc)
            return False

    return check


def http_probe(url: str, timeout: float, token: str | None = None) -> Probe:
    def check() -> bool:
        try:
            _http_get(url, timeout=timeout, token=token)
        except Exception as exc:
            LOG.debug("http %s: %s", url, exc)
            return False
        return True

    return check


MAC_RE = re.compile(r"[^0-9a-f]")
TIMESTAMP_KEYS = (
    "last_seen",
    "lastSeen",
    "last_checkin",
    "lastCheckin",
    "seen_at",
    "seenAt",
    "updated_at",
    "updatedAt",
    "created_at",
    "createdAt",
    "timestamp",
    "ts",
    "time",
)


def normalize_mac(value: str) -> str:
    return MAC_RE.sub("", value.lower())


def _walk(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def find_record(document: Any, mac: str) -> dict[str, Any] | None:
    want = normalize_mac(mac)
    for record in _walk(document):
        for value in record.values():
            if isinstance(value, str) and normalize_mac(value) == want:
                return record
    return None


def parse_timestamp(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value / 1000.0 if value > 1e11 else float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.isdigit():
        return parse_timestamp(int(text))
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def record_age(record: dict[str, Any], wall_now: float) -> float | None:
    for key in TIMESTAMP_KEYS:
        if key in record:
            stamp = parse_timestamp(record[key])
            if stamp is not None:
                return wall_now - stamp
    return None


def checkin_probe(
    url: str, mac: str, token: str, max_age: float, timeout: float
) -> Probe:
    """Healthy iff the iPXE Worker has heard from this MAC recently.

    The most useful signal we have: it is the same fact the dashboard shows, so
    the watchdog and the human agree on what "up" means.
    """

    def check() -> bool:
        try:
            document = json.loads(_http_get(url, timeout=timeout, token=token))
        except Exception as exc:
            LOG.warning("check-in query failed (%s) — treating as unknown, not down", exc)
            return True
        record = find_record(document, mac)
        if record is None:
            LOG.debug("no record for %s in %s", mac, url)
            return False
        age = record_age(record, time.time())
        if age is None:
            LOG.warning("record for %s carries no recognised timestamp: %s", mac, sorted(record))
            return False
        LOG.debug("%s last seen %.0fs ago (max %.0fs)", mac, age, max_age)
        return age <= max_age

    return check


def _http_get(url: str, timeout: float, token: str | None = None) -> str:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


# --------------------------------------------------------------------------- outlets


class Outlet(Protocol):
    def set(self, on: bool) -> None: ...
    def describe(self) -> str: ...


class OutletError(RuntimeError):
    pass


NS_TOGGLEX = "Appliance.Control.ToggleX"
NS_ALL = "Appliance.System.All"
NS_ABILITY = "Appliance.System.Ability"


class MerossOutlet:
    """Direct signed HTTP to the device on the LAN — no cloud in the recovery path.

    Envelope and signature follow the meross_lan client: POST /config with
    md5(messageId + key + timestamp) as `sign`. The device key is an account-wide
    secret recovered once from the Meross cloud and cached in the environment.
    """

    def __init__(
        self,
        host: str,
        key: str,
        channel: int,
        timeout: float = 5.0,
        verify: bool = True,
    ) -> None:
        self.host = host
        self.key = key
        self.channel = channel
        self.timeout = timeout
        self.verify = verify

    def describe(self) -> str:
        return f"meross://{self.host}/channel/{self.channel}"

    def call(self, namespace: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        message_id = uuid.uuid4().hex
        stamp = int(time.time())
        signature = hashlib.md5(
            f"{message_id}{self.key}{stamp}".encode()
        ).hexdigest()
        envelope = {
            "header": {
                "messageId": message_id,
                "namespace": namespace,
                "method": method,
                "payloadVersion": 1,
                "from": "ipxe-watchdog",
                "triggerSrc": "ipxe-watchdog",
                "timestamp": stamp,
                "timestampMs": 0,
                "sign": signature,
            },
            "payload": payload,
        }
        request = urllib.request.Request(
            f"http://{self.host}/config",
            data=json.dumps(envelope).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise OutletError(f"{self.host}: {exc}") from exc
        error = (body.get("payload") or {}).get("error")
        if error:
            hint = " (wrong MEROSS_KEY?)" if error.get("code") == 5001 else ""
            raise OutletError(f"{self.host}: device error {error}{hint}")
        return body

    def set(self, on: bool) -> None:
        self.call(
            NS_TOGGLEX,
            "SET",
            {"togglex": {"channel": self.channel, "onoff": int(on)}},
        )
        if self.verify and self.read() is not on:
            raise OutletError(
                f"{self.describe()}: SET accepted but state did not change — "
                "firmware may be ignoring the command"
            )

    def read(self) -> bool | None:
        digest = (
            self.call(NS_ALL, "GET", {})
            .get("payload", {})
            .get("all", {})
            .get("digest", {})
        )
        for entry in digest.get("togglex", []):
            if entry.get("channel") == self.channel:
                return bool(entry.get("onoff"))
        return None

    def identify(self) -> dict[str, Any]:
        system = self.call(NS_ALL, "GET", {}).get("payload", {}).get("all", {})
        abilities = sorted(
            self.call(NS_ABILITY, "GET", {}).get("payload", {}).get("ability", {})
        )
        return {"system": system.get("system", {}), "digest": system.get("digest", {}), "abilities": abilities}


class SimulatedOutlet:
    """Records switching without touching anything. The default."""

    def __init__(self, channel: int = 1, fail: bool = False) -> None:
        self.channel = channel
        self.fail = fail
        self.on = True
        self.transitions: list[bool] = []
        self.cycles = 0

    def describe(self) -> str:
        return f"simulated://channel/{self.channel}"

    def set(self, on: bool) -> None:
        if self.fail:
            raise OutletError("simulated outlet is unreachable")
        self.transitions.append(on)
        if self.on and not on:
            self.cycles += 1
        self.on = on


class PowerCycler:
    def __init__(self, outlet: Outlet, clock: Clock, drain: float = 10.0) -> None:
        self.outlet = outlet
        self.clock = clock
        self.drain = drain
        self.history: list[tuple[float, bool]] = []

    def cycle(self) -> bool:
        started = self.clock.now()
        LOG.warning("cutting power: %s", self.outlet.describe())
        try:
            self.outlet.set(False)
            self.clock.sleep(self.drain)
            self.outlet.set(True)
        except OutletError as exc:
            LOG.error("power cycle failed: %s", exc)
            self.history.append((started, False))
            return False
        LOG.warning("power restored: %s", self.outlet.describe())
        self.history.append((started, True))
        return True


# --------------------------------------------------------------------------- runner


Alerter = Callable[[str], None]


def make_alerter(command: str | None, node: str) -> Alerter:
    def alert(message: str) -> None:
        LOG.critical("ALERT [%s] %s", node, message)
        if not command:
            return
        try:
            subprocess.run(
                shlex.split(command) + [message],
                env={**os.environ, "WATCHDOG_NODE": node, "WATCHDOG_ALERT": message},
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            LOG.error("alert command failed: %s", exc)

    return alert


def run(
    policy: Policy,
    probe: Probe,
    cycler: PowerCycler,
    clock: Clock,
    alert: Alerter,
    poll_interval: float,
    deadline: float | None = None,
    should_stop: Callable[[], bool] = lambda: False,
) -> int:
    """Poll, judge, act. Returns EXIT_GAVE_UP when escalation ends at a human."""
    start = clock.now()

    while not should_stop():
        now = clock.now()
        if deadline is not None and now - start >= deadline:
            return EXIT_OK
        healthy = probe()
        verdict = policy.observe(healthy, now)
        LOG.info(
            "t+%-6.0f %-9s %s",
            now - start,
            "healthy" if healthy else "DOWN",
            verdict.reason,
        )
        if verdict.action is Action.GIVE_UP:
            alert(verdict.reason)
            return EXIT_GAVE_UP
        if verdict.action is Action.CYCLE:
            outcome = policy.record_cycle(clock.now(), cycler.cycle())
            LOG.info("t+%-6.0f %-9s %s", clock.now() - start, "action", outcome.reason)
            if outcome.action is Action.GIVE_UP:
                alert(outcome.reason)
                return EXIT_GAVE_UP
        clock.sleep(poll_interval)
    LOG.info("stopping on signal")
    return EXIT_OK


# --------------------------------------------------------------------------- wiring


def build_probe(args: argparse.Namespace, outlet: Outlet) -> Probe:
    spec: str = args.health
    if spec.startswith("sim:"):
        if args.arm:
            raise SystemExit(
                "refusing to switch real power from a simulated health signal — "
                "drop --arm, or use --arm --test-cycle to exercise the plug"
            )
        return build_simulated_probe(spec.removeprefix("sim:"), outlet)
    if spec.startswith("ping:"):
        return ping_probe(spec.removeprefix("ping:"), args.probe_timeout)
    if spec.startswith("tcp:"):
        host, _, port = spec.removeprefix("tcp:").rpartition(":")
        return tcp_probe(host, int(port), args.probe_timeout)
    if spec.startswith(("http://", "https://")):
        return http_probe(spec, args.probe_timeout, args.dashboard_token or None)
    if spec.startswith("checkin:"):
        mac = spec.removeprefix("checkin:")
        if not args.dashboard_token:
            raise SystemExit("checkin: probe needs DASHBOARD_TOKEN (or --dashboard-token)")
        url = args.worker_url.rstrip("/") + args.worker_path
        return checkin_probe(url, mac, args.dashboard_token, args.max_age, args.probe_timeout)
    raise SystemExit(f"unrecognised --health spec: {spec!r}")


def build_simulated_probe(scenario: str, outlet: Outlet) -> Probe:
    """Scripted health for exercising the ladder without a node or a plug."""
    cycles = lambda: getattr(outlet, "cycles", 0)  # noqa: E731

    if scenario == "down":
        return lambda: False
    if scenario == "up":
        return lambda: True
    if scenario.startswith("recover-after-cycle:"):
        target = int(scenario.rpartition(":")[2])
        return lambda: cycles() >= target
    if scenario == "flap":
        seen: set[int] = set()

        def flapping() -> bool:
            count = cycles()
            if count and count not in seen:
                seen.add(count)
                return True  # one healthy poll after each cycle, then gone again
            return False

        return flapping
    raise SystemExit(
        f"unrecognised sim scenario: {scenario!r} "
        "(down | up | flap | recover-after-cycle:N)"
    )


def build_outlet(args: argparse.Namespace) -> Outlet:
    if not args.arm:
        return SimulatedOutlet(channel=args.channel)
    if not args.plug_host or args.plug_key is None:
        raise SystemExit(
            "--arm needs MEROSS_HOST and MEROSS_KEY (see watchdog/README.md); "
            'MEROSS_KEY="" is valid for a plug that was never cloud-paired'
        )
    return MerossOutlet(
        args.plug_host, args.plug_key, args.channel, args.probe_timeout, not args.no_verify
    )


def env_float(name: str, default: float) -> float:
    return float(os.environ.get(name) or default)


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(name) or default)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--health",
        default=os.environ.get("WATCHDOG_HEALTH", "sim:down"),
        help="health signal: checkin:MAC | ping:HOST | tcp:HOST:PORT | https://URL "
        "| sim:{down,up,flap,recover-after-cycle:N}",
    )
    parser.add_argument("--node", default=os.environ.get("WATCHDOG_NODE", "node"))
    parser.add_argument("--poll-interval", type=float, default=env_float("WATCHDOG_POLL", 45))
    parser.add_argument("--failures", type=int, default=env_int("WATCHDOG_FAILURES", 3))
    parser.add_argument("--max-cycles", type=int, default=env_int("WATCHDOG_MAX_CYCLES", 5))
    parser.add_argument("--boot-timeout", type=float, default=env_float("WATCHDOG_BOOT_TIMEOUT", 180))
    parser.add_argument("--min-cycle-interval", type=float, default=env_float("WATCHDOG_MIN_INTERVAL", 300))
    parser.add_argument("--backoff-base", type=float, default=env_float("WATCHDOG_BACKOFF_BASE", 300))
    parser.add_argument("--backoff-max", type=float, default=env_float("WATCHDOG_BACKOFF_MAX", 3600))
    parser.add_argument("--stable-period", type=float, default=env_float("WATCHDOG_STABLE_PERIOD", 900))
    parser.add_argument("--drain", type=float, default=env_float("WATCHDOG_DRAIN", 10))
    parser.add_argument("--probe-timeout", type=float, default=env_float("WATCHDOG_PROBE_TIMEOUT", 5))
    parser.add_argument("--max-age", type=float, default=env_float("WATCHDOG_MAX_AGE", 300),
                        help="checkin: probe — how stale last_seen may be before down")
    parser.add_argument("--worker-url", default=os.environ.get("IPXE_BASE_URL", "https://ipxe.cloudcompute.com"))
    parser.add_argument("--worker-path", default=os.environ.get("WATCHDOG_WORKER_PATH", "/api/machines"))
    parser.add_argument("--dashboard-token", default=os.environ.get("DASHBOARD_TOKEN", ""))
    parser.add_argument("--plug-host", default=os.environ.get("MEROSS_HOST", ""))
    parser.add_argument("--plug-key", default=os.environ.get("MEROSS_KEY"))
    parser.add_argument("--channel", type=int, default=env_int("MEROSS_CHANNEL", 1))
    parser.add_argument("--alert-command", default=os.environ.get("WATCHDOG_ALERT_COMMAND", ""))
    parser.add_argument("--arm", action="store_true", help="switch real power (default is dry run)")
    parser.add_argument("--no-verify", action="store_true", help="skip outlet state read-back")
    parser.add_argument("--fast", action="store_true", help="virtual clock: run the ladder instantly")
    parser.add_argument("--deadline", type=float, default=None, help="stop after N (virtual) seconds")
    parser.add_argument("--identify", action="store_true", help="query the plug and exit")
    parser.add_argument("--test-cycle", action="store_true", help="perform one power cycle and exit")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )

    if args.identify:
        outlet = build_outlet(args)
        if not isinstance(outlet, MerossOutlet):
            raise SystemExit("--identify needs --arm plus MEROSS_HOST/MEROSS_KEY")
        print(json.dumps(outlet.identify(), indent=2))
        return EXIT_OK

    clock: Clock = VirtualClock() if args.fast else RealClock()
    outlet = build_outlet(args)
    cycler = PowerCycler(outlet, clock, args.drain)

    if args.test_cycle:
        return EXIT_OK if cycler.cycle() else EXIT_MISCONFIGURED

    probe = build_probe(args, outlet)
    policy = Policy(
        PolicyConfig(
            failure_threshold=args.failures,
            max_cycles=args.max_cycles,
            boot_timeout=args.boot_timeout,
            min_cycle_interval=args.min_cycle_interval,
            backoff_base=args.backoff_base,
            backoff_max=args.backoff_max,
            stable_period=args.stable_period,
        )
    )
    LOG.info(
        "watching %s via %s, power via %s%s",
        args.node,
        args.health,
        outlet.describe(),
        "" if args.arm else " (DRY RUN — pass --arm to switch real power)",
    )
    stopping = False

    def request_stop(*_: object) -> None:
        nonlocal stopping
        stopping = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, request_stop)

    return run(
        policy, probe, cycler, clock, make_alerter(args.alert_command, args.node),
        args.poll_interval, args.deadline, lambda: stopping,
    )


if __name__ == "__main__":
    sys.exit(main())
