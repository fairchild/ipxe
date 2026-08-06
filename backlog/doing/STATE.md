# Current state — 2026-08-05

Written as a handoff. Read this first; it is the fastest path back into context.

## Where things actually are

The Pi 4 netboots end to end and self-registers. It has been up **three days
continuously** (registered 2026-08-02T04:22Z, heartbeating every 5 min, 22
services, no drift), driven entirely remotely from a different site.

**Deployed to production:** the boot-chain fixes only — `-rpi` kernel flavor,
`modloop`/`apkovl`/`alpine_repo` over plain http, `console=ttyAMA0`, the
`/api/boots` newest-first fix, Worker-side https enforcement.

**NOT deployed:** everything from the hygiene round. Verify with
`curl -s -o /dev/null -w '%{http_code}' https://ipxe.cloudcompute.com/api/does-not-exist`
— 200 means the fix round is still unshipped, 404 means it landed.

**NOT uploaded:** the rebuilt overlay in `discovery/dist/`. The Pi is running the
older overlay, which is why its heartbeats still work — the new one changes the
wire format (`detail` field) and the Worker must be deployed *first or together*.

Both branches are pushed and clean:
- `fairchild/ipxe` → `claude/rpi-observability-dashboard-294063` @ `84444f8`
- `fairchild/services` → `feat/ipxe-pi-serial-console` @ `927d78b5` (PR #1172)

## The ordering constraint that matters

The node and the Worker now share a wire contract: the heartbeat's `detail`
field is what the stall detector parses. **Deploy the Worker before uploading
the overlay.** Ship them out of order — or ship a stale `dist/` — and the node
speaks a format the Worker cannot read, with every test green on both sides,
because nothing tests the wire between the repos. `discovery/build-overlay.sh`
must run immediately before any `wrangler r2 object put`.

## Next steps, in order

1. **Deploy the Worker** (`cd ipxe && bun run deploy`, needs
   `CLOUDFLARE_ACCOUNT_ID=9d1a8fe235b13dcab0fa3bcb6181ab0c`). 209 tests pass.
2. **Rebuild and upload the overlay**, in that order.
3. **Power-cycle the Pi** and confirm: it still registers, health tokens appear
   in `detail`, and the stall detector stays quiet on a healthy boot (a false
   positive here is worse than no detector).
4. **Deliberately wedge a boot** — the detector has never fired against real
   hardware, only tests. Until it does, it is unproven.
5. **Merge PR #1172** once 1–4 hold.

## Open, deliberately not done

- **No executable test of the cross-repo wire contract.** Revert the node's
  `TARGET` and drop `&detail=` and all 209 tests stay green while every
  heartbeat is silently discarded. Belongs to the boot-image work, where the
  contract gets redesigned anyway.
- **The SSH key is not embedded.** `cp ~/.ssh/id_ed25519.pub
  discovery/authorized_keys` then rebuild. Without it dropbear refuses to start
  — fails closed, safe to leave.
- **`MAX_STALL_SCAN_KEYS` = 2000** is exhausted past roughly 7 discovery nodes
  at the current keepalive rate; truncation becomes permanent and is reported
  honestly rather than hidden. Fine at current scale.
- **modloop vs ntpd was never settled.** Both changed at once during the outage
  and the fix was attributed to one without proof. 30 minutes to resolve, and
  `own-boot-image-plan.md` may care which.
- The rest is in `cleanup-plan.md`, including what we are declining permanently.

## Operational facts worth not rediscovering

- Remote control path: Tailscale → `orin` (100.121.24.73, LAN 192.168.8.107) →
  Meross plug 192.168.8.106, **channel 2 is the Pi**. Power script at
  `orin:/tmp/pwr.py`; `MEROSS_*` and `DASHBOARD_TOKEN` in
  `~/.config/ipxe-lab.env` (mode 600).
- Always read back plug state after a SET — this hardware family ACKs and
  ignores.
- `wrangler tail --env production --format json` is the instrument that does not
  depend on the node. It is pretty-printed concatenated JSON, not JSONL.
- The Pi's own card is `mmcblk0`; assigning a disk-install role to it would
  partition the card that boots it. Arch-aware roles (cleanup-plan item 6) is
  the fix and is not built yet.

## The lesson that keeps recurring

Three times an instrument failed the same way: beacons shipped inside the
artifact whose absence they should report; a heartbeat that flooded the endpoint
it needed; a stall detector suppressed by its own sibling. **A diagnostic must
not depend on the subsystem it observes.** Also: every one of the seven defects
adversarial review found sat behind a full green test suite, so mutation-check
new tests — revert the feature and confirm the test goes red.
