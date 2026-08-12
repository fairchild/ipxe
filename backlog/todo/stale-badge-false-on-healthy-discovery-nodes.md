---
priority: 2
timeout: 1w
arc: closed-loop
---

# `stale` reads true on a healthy discovery node, permanently

Observed 2026-08-06, 70 minutes after a verified-healthy boot:

```
fleet: {'total': 1, 'stale': 1, 'stalled': 0, 'spoof': 4}
last:  04:24:35Z heartbeat started-23_up3981_for3939_t36_pwr-ok
```

The node is fine — 23 services, 36 °C, supply healthy, heartbeating every five
minutes. `stalled` is correctly 0. But `stale` is 1 and will stay 1 for as long
as this boot lasts.

`isStale` derives from `last_checkin`, which a discovery node touches exactly
once per boot, at registration; the assignment poll deliberately does not bump
it. So sixty minutes after any successful boot, every healthy discovery node in
the fleet reads stale forever.

This is the same wrong question the stall detector was built to stop asking.
`findStalledMachines` says so in its own comments — "has it spoken to us lately"
would flag every healthy node within fifteen minutes, so it asks "did this boot
produce a registration" instead. The `stale` badge never got the same treatment,
and now contradicts the detector sitting next to it on the same dashboard.

The cost is not a wrong number, it is a trained reflex: an indicator that reads
alarming while everything is fine is one an operator learns to ignore, and it is
the indicator they will be ignoring during the next real outage.

## Options, cheapest first

- **Derive staleness from the newest boot event for that MAC** rather than from
  `last_checkin`. The boot feed already holds the heartbeat, and this is the
  same data the stall scan reads.
- **Exclude unassigned/discovery-state machines from the stale count**, and let
  `stalled` be the only health signal for them. Smallest change; leaves `stale`
  meaningful for assigned machines, which do check in on their own schedule.
- Bump `last_checkin` on heartbeat. **Rejected** — it would defeat
  `findStalledMachines`, whose whole clearing rule is `last_checkin >= bootMs`.
  A wedged node heartbeats too, so this would clear the stall flag on exactly
  the machines the detector exists to catch.

Whichever lands, verify by mutation: confirm the badge goes true on a node that
genuinely has not been heard from, not merely that it goes false on a healthy
one. Both are needed; only one is currently observed.

Lives in the companion control-plane service's registry staleness helper and
stats route.
