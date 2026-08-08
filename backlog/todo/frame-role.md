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

**Image source v1**: a `frames/` prefix in the existing R2 bucket, served by
the Worker; publish = `wrangler r2 object put` + regenerate the manifest.

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
6. **V1 images from a `frames/` R2 prefix.** Confirmed. trips integration
   stays future work.

## Risks that remain

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

## Slices

- **Slice 0 — image on glass** (bench, no merge): attach the panel, apk/pip
  the package map, run an inky example over ssh. Everything except the glass
  itself is already proven.
- **Slice 0b — the display stack on the node** (prerequisite of glass, see
  above): build aarch64-musl wheels once on the node (`apk add gcc
  python3-dev musl-dev py3-pip`, `pip wheel inky`), upload to R2, teach
  `ensure_deps` to `pip install --no-index --find-links` them.
- **Slice 1 — assign → zero-touch frame** (first merged slice): Worker role
  entry (`kind: ram`, arch-guarded per cleanup-plan item 6), boot.ts branch +
  nonce redeem, stall detector accepts `target=frame`; overlay role branch +
  `frame-display`. Acceptance: power-cycle the assigned Pi with no human
  contact → image within ~3 minutes; `wrangler tail` parses zero rejects;
  mutation-check the boot.ts branch.
- **Slice 2 — card builder**: `build-pi-uefi-card.sh` as above; a second Pi
  from blank card to frame with no manual UEFI or config.txt step.
