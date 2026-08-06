# node-watchdog

Out-of-band power recovery for one lab node. Watches a health signal, and when
the node is genuinely gone, cuts and restores its outlet — then converges on
telling a human.

The escalation ladder is the point. A deterministic failure does not improve on
the fifth power cut: a Pi whose firmware is in ACPI mode has an Ethernet port
that will never come up, and cutting its power ten times just produces ten
identical failures more slowly. So the cycle budget is finite, and running out
of it is a normal, expected outcome that ends in an alert rather than in a
louder loop. Workstream C of `backlog/todo/pi-closed-loop-plan.md`.

Single file, standard library only, no package to install.

## Quick start

Dry run is the default and switches nothing:

```bash
./watchdog/node_watchdog.py --fast --node pi4-a
```

`--fast` runs the whole ladder on a virtual clock, so you watch 83 minutes of
policy in about a second. Drop it for real-time. Try `--health sim:flap` and
`--health sim:recover-after-cycle:2` to see the two interesting shapes: a node
that briefly answers after each cycle and still burns the budget, and one that
actually comes back.

Against a real node, still with no plug attached:

```bash
export DASHBOARD_TOKEN=...
./watchdog/node_watchdog.py --node pi4-a --health checkin:dc:a6:32:11:22:33
```

Real power needs `--arm` plus `MEROSS_HOST` and `MEROSS_KEY`. Arming with a
simulated health signal is refused — use `--arm --test-cycle` to exercise the
plug on its own.

## Health signals

Pluggable through `--health`, because the right signal differs by node:

| Spec | Meaning |
|---|---|
| `checkin:MAC` | the iPXE Worker heard from this MAC within `--max-age` |
| `ping:HOST` | one ICMP echo |
| `tcp:HOST:PORT` | a TCP connect succeeds |
| `https://…` | GET returns < 400 (sends `DASHBOARD_TOKEN` if set) |
| `sim:…` | scripted, for exercising the policy: `down`, `up`, `flap`, `recover-after-cycle:N` |

`checkin:` is the useful one here. It queries `GET /api/machines` with the
dashboard bearer token, finds the record whose MAC matches (any punctuation,
any case), and reads `last_seen`. That is the same fact the dashboard shows, so
the watchdog and the human agree on what "up" means. It also handles `/api/boots`
via `--worker-path`, and epoch seconds or milliseconds as well as ISO-8601, since
only the `machines` table schema is pinned down in this repo.

One deliberate asymmetry: if the Worker itself is unreachable, the probe reports
*healthy*. A watchdog that cuts power because its own control plane is down is
worse than no watchdog. Absence of evidence about the node is not evidence the
node is gone.

## How it escalates

With defaults — 45s poll, 3 failures, 180s boot timeout, 300s floor and backoff
base, 5 cycles:

```
t+0      first failed check
t+90     third consecutive failure   → CYCLE 1 (off, 10s drain, on)
t+270    boot window ends, counting resumes
t+415    down again                  → CYCLE 2   (waited 300s)
t+1055                               → CYCLE 3   (waited 600s)
t+2280                               → CYCLE 4   (waited 1200s)
t+4720                               → CYCLE 5   (waited 2400s)
t+5000   still down                  → ALERT, exit 2
```

Each rung is separately configurable and separately tested:

**Consecutive failures.** A single missed check means nothing; only an unbroken
run of `--failures` reaches the threshold. Any healthy check clears the run.

**Boot window.** For `--boot-timeout` after a cycle the node is allowed to be
silent — it is booting. Failures in that window are not counted, but a *success*
in it counts immediately as recovery.

**Anti-flap floor.** No two cycle attempts closer together than
`--min-cycle-interval`, whatever else the policy thinks.

**Backoff.** The wait after cycle *k* is `--backoff-base × 2^(k-1)`, capped at
`--backoff-max`, floored by the anti-flap interval. Later cycles are less likely
to help, so they cost more.

**Hard cap.** After `--max-cycles` the watchdog alerts and exits 2. It does not
resume.

**Recovery.** A healthy check resets the failure run at once. The *cycle budget*
only refills after the node has been continuously healthy for
`--stable-period` (default 15 min). That distinction is what stops a node that
comes back for thirty seconds after every cut from being cycled forever — a
brief recovery is a symptom, not a fix.

**Unreachable plug.** A failed switch does not spend the cycle budget, but three
in a row is its own alert: we cannot recover a node whose outlet we cannot reach.

Exit codes: `0` clean stop or signal, `2` gave up and alerted, `3`
misconfiguration.

## Control path

Direct signed HTTP to the plug on the LAN. `POST http://<ip>/config` with a
JSON envelope whose header carries `md5(messageId + key + timestamp)` as `sign`
— the same protocol `meross_lan` speaks. Turning an outlet on or off is
`Appliance.Control.ToggleX` with `{"channel": N, "onoff": 0|1}`; the watchdog
reads `Appliance.System.All` afterwards to confirm the outlet actually moved,
because some firmware accepts a SET and silently ignores it.

Local, because a recovery tool that depends on the WAN fails exactly when it is
needed. The two alternatives:

- **`meross-iot`** is cloud-account-based in every version. Its "LAN" transport
  still discovers the device IP through the cloud and keeps an MQTT session
  open, so it is a latency optimisation on a cloud session, not a LAN-only
  mode. It also pulls in aiohttp, paho-mqtt and pycryptodomex. Against ~90
  lines of stdlib that is a poor trade.
- **Local MQTT re-pairing** — factory reset, join the device's `Meross_*` AP,
  point it at your own broker with `Appliance.Config.Key` — gets you real push
  instead of a 30s poll and keeps working on firmware that encrypts HTTP. It is
  also a one-way door: the device leaves the Meross cloud, the phone app stops
  working, and you now own a broker that is itself a single point of failure.
  Worth revisiting if the plug ever stops answering plaintext HTTP; overkill for
  polling two outlets.

The device key is account-wide and normally recovered once from the Meross cloud
(`https://iotx-us.meross.com/v1/Auth/signIn`), then cached. Nothing in this repo
does that login, and nothing here stores credentials — export `MEROSS_KEY` from
your own secret store. Devices that were never paired to the cloud, or were
locally re-paired, often take an empty key, so `MEROSS_KEY=""` is worth trying
before going near the cloud.

Wiring it up:

```bash
export MEROSS_HOST=10.0.0.42 MEROSS_KEY=...
./watchdog/node_watchdog.py --arm --identify        # model, firmware, channels
./watchdog/node_watchdog.py --arm --test-cycle --channel 1
```

`--identify` prints the device's abilities. On this device family channel 0 is
the master and **1 and 2 are the two physical outlets** — drive those, not 0.
If the ability list contains anything under `Appliance.Encrypt.*`, this client
will not work: that firmware wants AES-encrypted payloads, which the standard
library cannot do. The MSS620 is not in that group.

## Safety preconditions

These are physical setup, not code, and the watchdog is unsafe without them.

**Force the outlet's power-on state to ON.** The Meross app calls it the
power-on behaviour; it must be "on", not "last state" or "off". The entire
recovery model is that restoring power boots the node. If the outlet comes back
off, or comes back to whatever it was, a power cycle is an off switch.

**Keep the SD card read-only at runtime.** A hard power cut during a write can
corrupt the FAT boot partition — that is precisely the failure a watchdog
creates if it is careless, and the reason the card is mounted read-only.
Cloning a spare card is still the cheapest recovery path there is.

**Set `POWER_OFF_ON_HALT=1` in the Pi EEPROM** so a clean halt is
distinguishable from a crash. Without it, a deliberately halted node looks
exactly like a hung one, and the watchdog will keep power-cycling something that
was supposed to be off.

Check which outlet is which before arming. `--test-cycle` on the wrong channel
is a power cut on the wrong machine.

## Tests

Thirty behavioural tests, no hardware, no network, no waiting — the whole ladder
runs against a virtual clock in milliseconds.

```bash
uv run --no-project watchdog/test_node_watchdog.py
```

They assert on what an operator would see — when the outlet was switched, how
many times, and whether a human was told — rather than on the policy's internal
counters. The wire-format tests pin the signed envelope, since that is a
contract with the device rather than an implementation detail.

## Running it

A plain long-running process; nothing beyond Python 3.11 and network access.
One process per node, since the state is per-node.

```ini
[Unit]
Description=iPXE lab power watchdog (pi4-a)
After=network-online.target

[Service]
Environment=WATCHDOG_NODE=pi4-a
Environment=WATCHDOG_HEALTH=checkin:dc:a6:32:11:22:33
Environment=MEROSS_HOST=10.0.0.42
Environment=MEROSS_CHANNEL=1
EnvironmentFile=/etc/watchdog/pi4-a.env      # DASHBOARD_TOKEN, MEROSS_KEY
ExecStart=/usr/bin/uv run /opt/ipxe/watchdog/node_watchdog.py --arm
Restart=on-failure
RestartPreventExitStatus=2
```

`RestartPreventExitStatus=2` is load-bearing. Escalation state lives in memory,
so a supervisor that restarts the watchdog after it gives up hands it a fresh
cycle budget and turns "tell a human" back into an infinite loop. Exit 2 means
the ladder ran out; the fix is a person, not a restart.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `WATCHDOG_NODE` | `node` | label in logs and alerts |
| `WATCHDOG_HEALTH` | `sim:down` | health signal spec |
| `WATCHDOG_POLL` | `45` | seconds between checks |
| `WATCHDOG_FAILURES` | `3` | consecutive failures before down |
| `WATCHDOG_MAX_CYCLES` | `5` | hard cap |
| `WATCHDOG_BOOT_TIMEOUT` | `180` | grace after a cycle |
| `WATCHDOG_MIN_INTERVAL` | `300` | anti-flap floor |
| `WATCHDOG_BACKOFF_BASE` / `_MAX` | `300` / `3600` | backoff growth and ceiling |
| `WATCHDOG_STABLE_PERIOD` | `900` | healthy time that refills the budget |
| `WATCHDOG_DRAIN` | `10` | seconds off, for capacitors |
| `WATCHDOG_MAX_AGE` | `300` | `checkin:` staleness threshold |
| `WATCHDOG_PROBE_TIMEOUT` | `5` | per-request timeout |
| `WATCHDOG_WORKER_PATH` | `/api/machines` | or `/api/boots` |
| `WATCHDOG_ALERT_COMMAND` | — | run on give-up; message as argv and `$WATCHDOG_ALERT` |
| `IPXE_BASE_URL` | `https://ipxe.cloudcompute.com` | Worker base |
| `DASHBOARD_TOKEN` | — | bearer for the Worker API |
| `MEROSS_HOST` | — | plug IP, ideally DHCP-reserved |
| `MEROSS_KEY` | — | device key; `""` is valid for unpaired plugs |
| `MEROSS_CHANNEL` | `1` | outlet, `1` or `2` |

Every one has a CLI flag that wins over it.

## Not yet verified against hardware

Everything in the Meross path is written from protocol documentation and the
`meross_lan` client, and has been tested only against canned responses. Nobody
has pointed this at the physical plug. Specifically unconfirmed: that the
signature is accepted by this unit's firmware, that channels 1 and 2 map to the
outlets you expect, that the read-back verification agrees with a real digest,
and that 10 seconds is long enough to drain for a clean boot. Run `--identify`
and `--test-cycle` on a lamp before pointing it at a node.
