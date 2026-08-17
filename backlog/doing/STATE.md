# Current state — 2026-08-16

Written as a handoff. Read this first; it is the fastest path back into context.

## 2026-08-16 — the card has a front door

The control plane grew a setup page, `/frame` (operator token required), that
walks one card from download to a machine checking in: it offers whatever
`scripts/publish-card.sh` has put under the boot endpoint's `cards/` prefix,
verifies the SHA-256 in the browser before handing the file over, gives the
write commands for Imager / macOS / Linux, says what to expect from power-on
second by second, then shows every boot event and registration since the
button was pressed — grouped by MAC, Pi OUIs tagged, a ladder from iPXE menu
to registered — and takes a registered machine on to the frame role and its
Trips grant. Progress survives a reload. The publish script refuses an image
without its sidecar or one that fails the builder's structural verification,
and reads the upload back before it calls it published.

Found on the way: `frame-render.py-e`, a `sed -i -e` backup, had been in the
tree since #22 and in every published overlay since — byte-identical to the
script beside it, harmless to run, and exactly the kind of file the "no debris
in the overlay" claim said was absent. Removed; `build-overlay.sh` now fails on
editor backups, Finder files and swap files as it already did on `__pycache__`.
The published overlay still carries the stray copy until the next rebuild and
upload.

Still true, and unchanged by any of this: the card image has not been booted
from a written card, and no panel is attached. `docs/pi-support-matrix.md` now
says the first in as many words. The claim-token idea (a per-download secret in
the image, so the watch step knows which machine is yours) is written up in
`backlog/todo/card-claim-token.md` with why it is four small projects, not one.

## 2026-08-12 — the display stack is real, and the frame is one panel from glass

The reboot the section below was waiting for happened, twice, and the frame
now runs the whole chain end to end: bundle → authenticated trips manifest →
photograph. What it does not have is a panel. `no panel (RuntimeError: No
EEPROM detected!)` is the honest, current state of the hardware — nothing is
plugged into the header — so the picture lands on the preview sink and the
dashboard says exactly that. **Physical Inky proof remains the one open
gate**, and it is blocked on hardware, not on software.

**Slice 0b is done** (ipxe#26). The display stack is one artifact this
project builds, pins and serves: `build-display-bundle.sh` resolves 45 Alpine
packages and 5 wheels inside a target-architecture container, locks every
name/version/SHA-256, and emits a tarball the overlay pins by hash. Boot to
`display stack ready` is **8 seconds**. The bundle is byte-for-byte
reproducible. `inky` 2.4.0 is present on the node for the first time — every
production frame boot before this one was silently dry-run, which no health
signal would ever have shown.

**Slice 2 is done** (same PR). `scripts/build-pi-uefi-card.sh` writes a
deterministic, structurally verified card image from pinned inputs, and
refuses to build if the custom iPXE binary is missing rather than
substituting a stock one that boots to a shell. `dist/pi4-frame-card-v1-
v1.38.img.gz`, two builds, identical bytes. **Not yet flashed or booted** —
that is the next physical experiment, and it is what would move the Pi 4 row
of `docs/pi-support-matrix.md` from "proven by the bench card" to "proven by
a card this repo built".

Live state, checked rather than assumed: `last_checkin` moves every ~5 min,
`role_status` reads `sink=preview`, `images=14`, `ok=true`, `panel_error` set,
and the reported `image_sha256` matches a manifest entry exactly. The stale
badge is honest for frames now.

Four things the hardware taught, none of which a test would have:

- **`apk` will not install a local package on a diskless root** without
  `--force-non-repository`; the guard is right and the RAM node is the
  exception it exists for.
- **`apk` keeps consulting the cmdline mirrors even installing from files**,
  picks a dependency that lives only there, then fails it as `masked in:
  --no-network`. `--repositories-file /dev/null` is what "install this bundle
  and nothing else" actually requires. Four failed boots found this; the
  scratch-root test that passed beforehand could not, because a fresh
  `--initdb` root has no repositories to consult. *A test that cannot
  reproduce the condition is not evidence about it.*
- **`strings | grep -q` lies under `set -o pipefail`** — grep exits at the
  first match, strings takes SIGPIPE, the pipeline reports failure. It made
  the card builder reject every valid iPXE binary it was given.
- **A seeded `RPI_EFI.fd` carries the Pi it came from**: DHCP ClientId,
  MAC-named NIC records, PXE boot options, plus 62 records the firmware had
  deleted and never erased. `patch-rpi-uefi-vars.py scrub` removes them and
  verifies by searching the bytes, because a factory image cannot be given
  `SystemTableMode=2` offline — its variable store is empty.

**The next physical steps, in order:** attach the 4" Impression (PIM600,
640×400 UC8159 — but read the EEPROM, do not assume: a Spectra 6 4.0 is
600×400 on a different controller and the override table carries both), watch
`role_status.sink` change from `preview` to `inky`, then flash and boot a card
this repo built.

## 2026-08-10 — control loop MERGED AND DEPLOYED (the reboot it awaited is done, above)

Both halves are in production. services#1239 (squash-merged) carried the
Worker: `deploy-ipxe` ran on the merge push and applied D1 migration 0005
before deploying — verified at the job level, then against the live API:
the machine detail now carries `role_status`/`role_status_at` (null until a
node reports one) and `role_config.token` reads back `<redacted>`. ipxe#22
(squash-merged) carried the node half, including the late fix that honors an
explicit `config: null` as "operator cleared it" — the Worker had grown that
distinction (services `ab28b680`) after this section below was written, and
the node ignored it; the fastest cross-repo drift yet, hours old.

Overlay `a5b698b9…` built from the merged tree, uploaded, and the served
hash matches the local build. Build input recovered the hard way:
`discovery/authorized_keys` exists in no local checkout — it was extracted
from the served overlay (`tar -xzOf … ./root/.ssh/authorized_keys`), which is
the exact key the node already trusts and is a public half, safe to pull from
the world-readable bucket.

**What remains is one reboot.** The bench Pi still runs the old overlay
(role_status null, last_checkin frozen at its last boot — checked, not
assumed). `ssh -J <relay> root@<node> reboot`, ~110 s, then: role-ack mints
the boot token, the first render pass posts status, `last_checkin` starts
moving every ~5 min, the stale badge clears honestly, and the dashboard
drawer's Frame status section fills in. That run is also the first real
render of the drawer against production — worth eyes on the page. Until the
reboot, old-overlay/new-Worker is a safe mix by construction: no token in
RAM → no exchange attempted.

## 2026-08-09 — the frame gains a control loop (code complete at the time; superseded above)

The role-ack now hands a RAM boot a rotating machine token alongside its
config, and frame-render spends it once per pass: a five-field status out
(content digest on glass, sink, showable count, manifest health, uptime), the
operator's current role config back. A config edit reaches a live frame
within one `FRAME_POLL` (≤300 s) instead of at next reboot, and
`last_checkin` moves every pass — which, once deployed, makes the dashboard's
stale badge honest for frames and retires the "never arm the watchdog against
a frame" landmine. The display never depends on the control plane: no token
file means no exchange and exactly the old behavior. Contract documented in
`discovery/README.md` ("The control loop").

The companion control plane adds the frame-status section, a role-config editor
whose `<redacted>` sentinel round-trips without exposing or clobbering the
secret, and reset/delete controls. The node half keeps the role-ack token
private, reports only an image content digest rather than the source filename,
uses HTTPS for authenticated check-ins, and treats control-plane failures as
soft so that rendering never depends on them.

## 2026-08-08 latest — the frame is on glass (HDMI)

The frame now **displays on the connected monitor**: cold netboot to picture
in ~81 s, verified by reading `/dev/fb0` back and comparing to the rendered
image, then re-proven end-to-end from a fresh netboot of the rebuilt overlay
(`802d0980…`). frame-render is now fetch → compose → *sink*: HDMI
framebuffer (raw Pillow blit to the vc4 console, repainted every poll pass
because the console scribbles on it), Inky e-ink (only on image change;
absent until Slice 0b vendors the wheels), preview PNG when neither exists.
Two more hardware-taught facts: modern Pillow removed its `BGR;16` packer,
so RGB565 packs via numpy (now in ensure_deps — the inky stack wants it
anyway); and the tty1 getty stays underneath the picture as the rescue path,
cursor hidden, blank timer off.

Next for the frame, in order: **Slice 0b** (vendored aarch64-musl wheels for
inky in R2 — a prerequisite of attaching either panel), then attach a panel
and the same loop drives both displays. Real images: replace the test cards
via `scripts/publish-frames.sh` in the Worker repo; trips integration is its
own work item.

## 2026-08-08 late — the frame role is live end to end

The bench Pi **is now the frame**: state `active`, role `frame`, and every
boot since assignment serves the frame script. Assignment-to-active-heartbeat
measured at **88 seconds** with no hands. Control-plane version `9f8a9f01`,
overlay `b861f84b…` (ipxe#13); `wrangler tail` across the
whole window: **zero rejected check-ins**. The node dry-run renders the
`frames/` R2 directory to `/tmp/frame-preview.png` — the panel isn't attached
yet, and per `backlog/todo/frame-role.md` **Slice 0b (vendored inky wheels)
is a prerequisite of glass**: today's boots log
`no driveable panel (No module named 'inky'); dry-run`, honestly.

Three production facts the hardware taught that no test would have:

- **Our embedded binaries never sent a MAC** — `?arch=${buildarch}` only —
  so `findByMac` had never once run for a machine booting our own binaries
  and every assignment (frame *or* Debian) was silently inert. Fixed
  Worker-side: a one-hop iPXE bounce re-chains with `${net0/mac}`, no
  reflash. This had survived because every test passes `mac=` explicitly.
- **The zone 403s urllib's default User-Agent** (`Python-urllib` reads as a
  bot). Identical request, named UA, 200. frame-render sends
  `frame-display/1`.
- **The zone's http exemptions are narrower than believed**: `/frames/` and
  ~a third of check-ins get 301'd to https; busybox wget and urllib both
  follow, so nothing is lost, but "plain http, no TLS dependency" now
  overstates — the redirect target is https and the fetch survives because
  the clock is right by the default runlevel.

Reset path untested on hardware (assign/reset was exercised in tests only).
The Meross watchdog must not be armed against the frame — its freshness
probe reads `last_seen`, which a healthy frame only touches at boot
(`frame-role.md`, risks).

## 2026-08-08 — soft restart is reliable, and the card is frame-ready

Four consecutive `ssh reboot` cycles with no plug involvement: ~110 s from
command to healthy heartbeat every time, stage timings identical across all
four (sysinit up17, rc-default up30, sshd up35, heartbeat up39). Soft restart
is the routine restart; the plug stays reserved for a wedged boot.

The SD card's config.txt now carries `dtparam=spi=on`, `dtparam=i2c_arm=on`
and `dtoverlay=spi0-0cs` (dtbo added to `overlays/`), applied from the running
node by mounting `/dev/mmcblk0p1` — no physical access needed. The original
config is backed up on the card as `config.txt.bak-20260808`. Verified after
reboot, on the production netboot path: `/dev/spidev0.0` exists with GPIO8
unclaimed (`SPI_CE0_N input`), `/dev/i2c-1` appears after `modprobe i2c-dev`,
and a raw `SPI_IOC_MESSAGE` transfer at 1 MHz succeeds. That settles "does SPI
work under pftf UEFI Devicetree netboot" — the question the frame role
(`backlog/todo/frame-role.md`) hinged on. Driving actual glass is the only
step left, blocked on physically attaching the panel (i2c scan shows nothing
at 0x50, so no Inky is connected today).

Two facts the frame implementation must carry: `spidev` and `i2c-dev` need
explicit modprobe or `/etc/modules` entries — nothing autoloads them; and the
node's apk has only `main` configured, while `py3-pillow`, `py3-numpy` and
`py3-libgpiod` live in `community`, which must be appended to
`/etc/apk/repositories` before installing.

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

When the node is *healthy*, `ssh -J <relay> root@<node> reboot` is far more
reliable than the plug and re-runs the whole netboot chain — a RAM node loses
nothing. Reserve the plug for when the node is wedged, which is also the only
time it is indispensable.

## Next steps, in order

1. **Merge the companion serial-console control-plane change.** Everything it
   carries is now verified against hardware. Production was deployed ahead of
   that repository's main branch; this repo has carried unnoticed deploy drift
   before.
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

- Remote control path: Tailscale → a LAN relay host → the Meross plug, on the
  outlet channel the node is wired to. **This repo is public, so the addresses,
  hostnames and channel number stay out of it** — they live with the credentials
  in `~/.config/ipxe-lab.env` (mode 600), which is where `MEROSS_HOST`,
  `MEROSS_KEY`, `MEROSS_CHANNEL` and `DASHBOARD_TOKEN` already are. Source it
  with `set -a; . ~/.config/ipxe-lab.env; set +a` and every command below works
  without a specific appearing in a commit.
- `watchdog/node_watchdog.py` already does both hard parts correctly — it reads
  the `System.All` digest and verifies by default. Use it, and run it from the
  relay: the plug is not routable from outside the LAN. An older scratch script
  that only SET and never read back is what produced false conclusions; delete
  any copy you find rather than fixing it.
- The node takes ssh as `root` via `-J <relay>`, using the key in
  `discovery/authorized_keys` (gitignored build input). Without that file
  dropbear declines to start — fails closed.
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
