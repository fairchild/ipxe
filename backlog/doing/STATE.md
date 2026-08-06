# Current state — 2026-08-06

Written as a handoff. Read this first; it is the fastest path back into context.

## Where things actually are

The Pi 4 netboots end to end, self-registers, and the stall detector has now
fired against real hardware. The five-item list this document used to carry is
done; what follows is what that proved and what it exposed.

**Deployed to production** (Worker version `ad0cdf99`, 2026-08-06 02:17Z): the
boot-chain fixes *and* the hygiene round. Verify with

```bash
curl -s https://ipxe.cloudcompute.com/api/does-not-exist
```

`{"error":"not_found"}` means the hygiene round is live; a page of HTML means
something has regressed to an older deploy.

**Uploaded to R2**: the overlay built from this tree,
sha256 `5377e707…`. Confirm the served bytes match the local build before
believing anything about node behaviour:

```bash
curl -s http://ipxe.cloudcompute.com/discovery/discovery.apkovl.tar.gz | shasum -a 256
```

## What the hardware run proved

Deploy order held: Worker first, then a freshly built overlay. The node came up
speaking the new wire format on the first try — `target=discovery` with the
openrc state in `detail`, e.g. `started-23_up42_for0_t36_pwr-ok`. A `wrangler
tail` running across the whole window parsed 250 records with **zero** rejected
check-ins, which is the independent confirmation that matters: the boot feed
showing events only proves what it accepted, not what it turned away.

Then a deliberate wedge — an openrc service that blocks the default runlevel
forever, so `local` and therefore registration never run. The detector fired
15m13s after boot start and named the service:

```
reason: stuck            service: discovery-wedge   held_seconds: 606
boot_started: 03:01:24Z  last_checkin: 02:52:07Z    temp_c: 36
```

`reason: stuck`, not `silent`, is the whole point. The node was talking the
entire time, five minutes apart, exactly as designed. The silence-based rule
this replaced could never have reached this case. `boot_started` is anchored on
the node's `/proc/uptime`, so it dates the boot rather than the last contact,
and `last_checkin` sitting *before* `boot_started` is precisely what makes the
boot stalled rather than merely old.

Recovery was a power cycle onto the restored overlay: healthy heartbeat inside
90 seconds, `stalled` back to 0, `spoof` unchanged at 4, and re-registration
under the same machine id — which incidentally re-confirms the
rotate-while-unassigned policy on hardware.

**What this did and did not prove.** `reason` has three values and hardware
exercised one. `stuck` is the one that depends on the new wire contract — the
node emitting `detail` and the Worker parsing an openrc state and an uptime out
of it — so it was the right one to spend a hardware run on. `silent` and
`no-progress` remain unit-tested only (`test/unit/stalls.test.ts`), and they are
the *lower*-risk paths precisely because they parse no `detail`: a silent node's
newest event is the iPXE `stage=boot` with no detail at all, and `bootStartMs`
falls back to the event timestamp. Worth knowing rather than worth fixing —
"the stall detector is proven" is too strong; "the path that carried the new
contract is proven" is right.

## What it exposed — read before wedging anything again

**On a wedged boot you do not get a shell.** `discovery-sshd` declares
`after net local`, so ssh comes up *after* `local`. Anything that blocks before
`local` therefore blocks ssh too, by construction. The wedge test declared
`after discovery-sshd` *and* `before local`, which is a cycle; openrc broke it
silently by discarding the ordering, ran the wedge first, and the node spent
fifteen minutes reachable only by ping. **The plug is the sole recovery path for
a wedged boot**, which promotes its flakiness (below) from annoyance to risk.

Mitigation used, and worth repeating: **restore the known-good overlay to R2 the
moment the wedge is confirmed**, long before attempting recovery. It does not
disturb the running node — the apkovl only matters at next boot — so it makes
every subsequent reboot healthy no matter how it gets triggered.

**`rc-status` cannot distinguish a crashed service from a working one.**
`discovery-clock` and `discovery-sshd` both background their daemon and return,
so openrc records them `crashed` on a completely healthy node. Two costs: a real
crash in either is invisible, and `need` on them is unsatisfiable — openrc skips
the dependent **without a word**. The first wedge attempt used `need
discovery-sshd` and produced a perfectly healthy boot, which is the same failure
shape as everything else in this project: the instrument reported success
because it never ran. Tracked in `backlog/todo/openrc-crashed-services.md`.

**The Meross plug's control plane flaps on a minutes timescale.** `No route to
host` is the normal case, not an error. Two hard-won details, now encoded in the
cycle script: read state from `Appliance.System.All` and never from
`Appliance.Control.ToggleX`, which answers for **channel 0 only** regardless of
the channel asked for — on a multi-outlet plug it silently reports the master
while you steer channel 2. And the plug ACKs a SET it then ignores, so a
transition is not real until a GET agrees. During this session a SET landed
whose ACK was lost, and the read-back is the only reason we knew the Pi was
powered off rather than crashed.

When the node is *healthy*, `ssh -J orin root@192.168.8.157 reboot` is far more
reliable than the plug and re-runs the whole netboot chain — a RAM node loses
nothing. Reserve the plug for when the node is wedged, which is also the only
time it is indispensable.

## Next steps, in order

1. **Merge PR #1172** (`fairchild/services` @ `feat/ipxe-pi-serial-console`).
   Everything it carries is now verified against hardware. Production is
   deployed from that branch and is ahead of `main`; this repo has carried
   unnoticed deploy drift for weeks before.
2. **Fix the openrc `crashed` reporting** — see the backlog item. Small, and it
   restores `need` and `rc-status` as usable tools.
3. **The boot image** (`own-boot-image-plan.md`). It moots a third of the
   remaining debt; check work against it before starting anything else.
4. **Arch-aware roles** (`cleanup-plan.md` item 6) before any role is assigned
   to the Pi. Its own card is `mmcblk0`; a disk-install role would partition the
   card that boots it.

## Open, deliberately not done

- **No executable test of the cross-repo wire contract.** Revert the node's
  `TARGET` and drop `&detail=` and all 209 tests stay green while every
  heartbeat is silently discarded. Hardware has now proven the contract once;
  nothing stops it drifting tomorrow. Belongs to the boot-image work, where the
  contract gets redesigned anyway.
- **`MAX_STALL_SCAN_KEYS` = 2000** is exhausted past roughly 7 discovery nodes
  at the current keepalive rate; truncation becomes permanent and is reported
  honestly rather than hidden. Fine at current scale.
- **modloop vs ntpd was never settled.** Both changed at once during the outage
  and the fix was attributed to one without proof. 30 minutes to resolve, and
  `own-boot-image-plan.md` may care which.
- The rest is in `cleanup-plan.md`, including what we are declining permanently.

## Operational facts worth not rediscovering

- Remote control path: Tailscale → `orin` (100.121.24.73, LAN 192.168.8.107) →
  Meross plug 192.168.8.106, **channel 2 is the Pi**. `MEROSS_*` and
  `DASHBOARD_TOKEN` in `~/.config/ipxe-lab.env` (mode 600).
- `watchdog/node_watchdog.py` already does both hard parts correctly — it reads
  the `System.All` digest and verifies by default. Use it. `orin:/tmp/pwr.py` is
  an old scratch script that only SETs and never reads back; it is the one that
  produced false conclusions, and it is worth deleting rather than fixing.
- The node takes ssh at `root@192.168.8.157` via `-J orin`, using the key in
  `discovery/authorized_keys` (gitignored build input, currently
  `id_ed25519.pub`). Without that file dropbear declines to start — fails
  closed.
- Cold boot to healthy heartbeat is ~45 seconds: menu → iPXE boot → sysinit at
  up17 → rc-default at up30 → sshd-up at up37 → heartbeat at up42.
- `wrangler tail --env production --format json` is the instrument that does not
  depend on the node. It is pretty-printed concatenated JSON, not JSONL —
  parse with `json.JSONDecoder().raw_decode` in a loop.
- A wedged boot still answers ping and still heartbeats. Neither is evidence of
  a working boot; only a registration inside *this* boot is.

## The lesson that keeps recurring

Four times now an instrument has failed the same way: beacons shipped inside the
artifact whose absence they should report; a heartbeat that flooded the endpoint
it needed; a stall detector suppressed by its own sibling; and this session, a
wedge that never ran because it depended on a service openrc had quietly
declared crashed. **A diagnostic must not depend on the subsystem it observes.**

Also unchanged: every one of the seven defects adversarial review found sat
behind a full green test suite. Mutation-check new tests — revert the feature
and confirm the test goes red.
