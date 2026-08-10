---
priority: 1
timeout: 3w
arc: frame
---

# `frame` role: netboot straight into an e-ink picture frame

Goal: plug in a prepared SD card → the Pi netboots → registers → gets assigned
`frame` → every boot thereafter displays images on a Pimoroni Inky panel with
no hands. Eventually the images come from the trips gallery; v1 is a plain
directory of images the Worker serves.

Design settled 2026-08-08 from a three-agent research pass plus live hardware
experiments. Forks below are marked where they await confirmation.

## What hardware already proved (2026-08-08, production boot path)

The bench Pi, netbooted exactly as production does (pftf UEFI in Devicetree
mode → iPXE → Alpine diskless), with `dtparam=spi=on`, `dtparam=i2c_arm=on`,
`dtoverlay=spi0-0cs` on the card:

- `/dev/spidev0.0` present; GPIO8 reads `SPI_CE0_N input` — unclaimed, which
  is what the inky library requires (it toggles CS itself and refuses a pin
  the kernel owns).
- `/dev/i2c-1` present after `modprobe i2c-dev`.
- A raw `SPI_IOC_MESSAGE` transfer at 1 MHz succeeds — the kernel drives real
  pins. Only glass remains unproven, and no panel is attached yet.
- `/proc/device-tree/model` = "Raspberry Pi 4 Model B" — passes the
  `gpiodevice` platform gate, which hard-fails without a devicetree (one more
  reason ACPI mode can never carry this role).

## The design

**One overlay; the Worker decides at boot time; the node branches at runtime.**

1. Operator assigns `frame` via the existing `POST /api/machines/:id/assign`.
2. The node's existing `poll_assignment` reboots on 200 — unchanged.
3. `/boot.ipxe?mac=…` grows a branch: `assigned` + a role of kind `ram` →
   serve the existing Alpine discovery netboot script with `role=frame` (and a
   single-use nonce) added to the kernel cmdline. Today this case falls
   through to the menu and the node idles in `wait_without_token` forever —
   that gap closes regardless of any other choice. Redeeming the nonce moves
   the machine `assigned → active`; the node needs no persistent secret,
   which suits a RAM node fed from a world-readable bucket.
4. `discovery.start` reads `role=` via its existing `cmdline_value`;
   `role=frame` → skip registration/poll, start `frame-display`. Beacon,
   heartbeat, clock, sshd stay exactly as today; heartbeat carries
   `target=frame`, which the Worker's stall detector must accept — a wire-
   contract change, so **Worker deploys first**, verified with `wrangler tail`
   showing zero rejects.

**`frame-display`** (new, `discovery/overlay/usr/local/bin/` + openrc unit):
Python + the stock `inky` library. Deps: `apk add py3-pillow py3-numpy
py3-libgpiod` (community repo — must be appended to `/etc/apk/repositories`;
the netboot cmdline only configures `main`) + pip for `inky`, `gpiodevice`,
`smbus2`, `spidev` (vendor pinned wheels in R2; boot-time PyPI is a fleet-wide
single point of failure). Loop: poll a `manifest.json` under the image base
URL → on change or rotation interval, fetch, Pillow-resize, `set_image()`
(the lib dithers), `show()` (~30–45 s blocking refresh, normal for these
panels). Panel model comes from role config, not EEPROM autodetect. Use
`supervise-daemon`, not backgrounding — do not add a third service with the
`rc-status`-reads-crashed defect.

**Modules**: `spidev` and `i2c-dev` do not autoload — `/etc/modules` entries
in the overlay (harmless on non-frame nodes).

**Fallback image source**: a `frames/` prefix in the existing R2 bucket,
served by the Worker. It remains the no-config test-card path; assigned frames
use the authenticated Trips source described below.

**Card prep** (`scripts/build-pi-uefi-card.sh`, new): flash pftf release →
`patch-rpi-uefi-vars.py set RPI_EFI.fd SystemTableMode=2` → append the three
dtparam/dtoverlay lines → copy `spi0-0cs.dtbo` (pin to pftf's DTB version)
into `overlays/`. Every piece exists; only the wrapper is missing. This is
what makes "plug in an SD card" reproducible on the next Pi.

## Forks — confirmed 2026-08-08

1. **Single overlay + cmdline role.** Confirmed. Revisit inside own-boot-image,
   where a frame variant becomes a manifest entry.
2. **Stock inky lib.** Confirmed by implication of the panel answer: the fleet
   has a Spectra 6 13.3" *and* a 4" — two controllers, which is exactly the
   hardware matrix the stock lib absorbs and a custom driver would double.
   Spectra 6 support is recent, so pin a current lib version.
3. **Node-side dithering for v1.** Confirmed.
4. **Panels: both.** Spectra 6 13.3" and a 4", carrier boards arriving ~a week
   after confirmation. Two consequences: `frame-display` is panel-agnostic —
   EEPROM auto-detect is the *primary* path (it exists for precisely this
   mixed-fleet case) with an explicit override in role config as fallback;
   and the service ships a dry-run mode (render the dithered output to a PNG
   instead of `show()`) so the whole pipeline is testable before glass and
   debuggable after.
5. **The bench Pi 4 becomes the frame.** Confirmed — its card is already
   prepared. The lab loses its dedicated test node; wedge-style tests now need
   scheduling around the frame being on display.
6. **V1 images from a `frames/` R2 prefix.** Confirmed as a fallback. The
   production frame now uses an authenticated Trips Published Roll.

## Risks that remain

- **The watchdog's freshness probe predates RAM roles.** `node_watchdog.py`
  judges liveness by record age (`last_seen` preferred), and the deployed
  frame moves that timestamp only at each boot's ack. The prepared control
  loop fixes this with authenticated check-ins every render pass, but it is
  not live until the Worker deploys first and a rebuilt overlay follows. Do
  not arm the watchdog against the frame before both halves are deployed and
  the dashboard shows fresh status from the physical Pi.

- **NVRAM reversion**: DT mode lives inside `RPI_EFI.fd`; a reverted card is a
  dark panel with a healthy heartbeat. Card builder sets it; several cold
  cycles should confirm persistence.
- **Boot-time installs**: mirror or PyPI outage bricks frame boots. Vendored
  wheels + pinned versions mitigate; the real fix is own-boot-image.
- **Wire-contract drift** (`target=frame`): the failure class STATE.md already
  documents — both repos green while every heartbeat is rejected. Worker
  first, `wrangler tail` as the check.
- **trips is not at trips.cloudcompute.com**: the repo deploys to a different
  domain and is an invite-authed gallery, not a public image directory.
  Feeding it into frames is real future work (likely a Worker-side selection
  that re-serves chosen images into `frames/`), not a v1 dependency.

## Accepted for v1 (pre-deploy review found these; each is deliberate)

- **Production frame boots are dry-run until Slice 0b lands.** `ensure_deps`
  installs only python3+Pillow; the inky lib is pip-only and pip is not on the
  node. Every health signal reads green while the glass would stay blank —
  fine today because no glass exists, a trap the day it does. Slice 0b
  (vendored musl wheels for inky/gpiodevice/smbus2/spidev in R2, installed by
  `ensure_deps`) is therefore a *prerequisite* of attaching the panel, not a
  nicety.
- **`set_image` with an RGB canvas assumes Impression-class panels.** Both
  fleet panels are (Spectra 6 13.3", Impression 4"); a pHAT/wHAT would need
  palette-mode handling in `compose`.
- **A bad publish (manifest sha256 ≠ object) freezes the previous image**
  with only console-log evidence, refetching ~12×/hour until the slot
  rotates. Correct for truncation; a mismatch counter in heartbeat detail
  would make it fleet-visible if it ever recurs.

## Trips integration — DONE 2026-08-09

Photos come from trips, authenticated end to end; iPXE never hosts them. The
node's credential chain: nonce-proven role-ack delivers the operator-stored
role config (`PUT /api/machines/:id/role-config`, dashboard-authed) — a
`{source, token}` blob persisted by the iPXE control plane so future boots can
receive it, then held by the Pi only in a 0600 RAM file. frame-render fetches
`source` with `Authorization: Bearer <token>` expecting
`{"images":[{name, url?, sha256?}]}`, resolves image URLs against the
manifest, prefetches the whole set into RAM (network changes the set,
display never needs it), and falls back to the Worker's test cards when no
config is set.

Trips reuses a Trip-scoped Display Grant and an Organizer-curated Published
Roll. Its manifest and every image route require the same bearer token; the
manifest includes `images[{name,url,sha256}]` and is also a compatible Web App
Manifest superset. The live HDMI frame proved the whole path after a boot-time
config delivery: authenticated manifest and image fetches, digest verification,
and local rotation from the prefetched set. The public `/frames/` route remains
only as the explicit no-config fallback.

Security boundary: Trips stores the grant hash, the iPXE control plane stores
the courier payload needed for later reboots, and the Pi keeps the delivered
plaintext only for the life of that RAM boot. "RAM-only" describes the node,
not the end-to-end courier.

## Slices

- **Slice 0 — image on glass** (bench, no merge): attach the panel, apk/pip
  the package map, run an inky example over ssh. Everything except the glass
  itself is already proven.
- **Slice 0b — the display stack on the node** (prerequisite of glass, see
  above): build aarch64-musl wheels once on the node (`apk add gcc
  python3-dev musl-dev py3-pip`, `pip wheel inky`), upload to R2, teach
  `ensure_deps` to `pip install --no-index --find-links` them.
- **Slice 1 — assign → zero-touch frame**: **DONE 2026-08-08**
  (control-plane and node changes deployed and proven on hardware). Assignment to
  active frame heartbeat in 88 s; second boot re-served the frame script
  from `active`; `wrangler tail` zero rejects; every new branch
  mutation-checked. Render is dry-run to `/tmp/frame-preview.png` pending
  Slice 0b. Also fixed on the way: the embedded binaries never sent a MAC
  (Worker-side bounce now supplies it — this had silently disabled *all*
  assignment flows), and the zone 403s urllib's default UA (frame-render
  sends `frame-display/1`).
- **Slice 1b — HDMI sink**: **DONE 2026-08-08.** frame-render refactored to
  fetch → compose → sink; `/dev/fb0` (vc4 console, RGB565-via-numpy blit,
  repaint-every-pass because the console shares the surface) beside the Inky
  sink (image-change only) and the preview PNG. Cold netboot to picture on a
  1080p monitor: ~81 s. The Inky sink stays absent until Slice 0b.
- **Slice 1c — authenticated Trips source**: **DONE 2026-08-09.** Trips
  publishes an Organizer-controlled Roll through a Trip-scoped bearer grant;
  iPXE delivers `{source, token}` at boot; the physical Pi fetched and displayed
  a curated set with no public photo route.
- **Slice 1d — live frame control loop**: **CODE COMPLETE, NOT DEPLOYED.** The
  Worker accepts a small status object and returns current role config; the Pi
  stores the role-ack token in RAM and exchanges once per render pass. The
  server accepts that token until the next successful role-ack rotates its hash,
  or an operator resets/deletes the machine; power loss alone is not expiry.
  Deploy Worker first, then rebuild/upload the overlay, reboot the Pi, and
  require a fresh dashboard status before enabling watchdog automation.
- **Slice 2 — card builder**: `build-pi-uefi-card.sh` as above; a second Pi
  from blank card to frame with no manual UEFI or config.txt step.
