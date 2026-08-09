# Discovery OS

A RAM-only Alpine Linux that a blank machine netboots into to inventory its
hardware, register with the iPXE Worker, and wait for a role. It's the first
rung of zero-touch join, and doubles as a rescue shell (a getty stays on tty1).

Nothing installs to disk. The machine boots Alpine's diskless netboot image
(`vmlinuz-lts` + `initramfs-lts` + `modloop-lts`) straight into tmpfs, extracts
our overlay, runs one script, and either reboots into an install (once assigned)
or sits polling.

## What's here

```
discovery/
  overlay/                       the apkovl filesystem (rooted at "/")
    etc/local.d/discovery.start  inventory + register + poll (the whole brain)
    usr/local/bin/discovery-*    clock, beacon, heartbeat, sshd
    etc/init.d/discovery-*       openrc units for those
    etc/runlevels/*/             symlinks enabling each of them
    etc/runlevels/default/local  symlink enabling the local service
    etc/runlevels/boot/*         networking + hostname enablement
    etc/network/interfaces       eth0 via dhcp
    etc/hostname                 "discovery"
    root/.ssh/authorized_keys    staged at build time (gitignored)
  authorized_keys                the operator key to embed (gitignored)
  build-overlay.sh               packs overlay/ into dist/discovery.apkovl.tar.gz
  dist/                          build output (gitignored except as noted)
  cache/                         QEMU kernel/initrd cache (gitignored)
```

The overlay is an **apkovl** — Alpine's config overlay, a gzipped tar of a
filesystem rooted at `/`. The netboot initramfs downloads it (`apkovl=<url>` on
the kernel cmdline) and extracts it over the tmpfs root before openrc starts.
`etc/local.d/discovery.start` then runs as the `local` service on the default
runlevel.

## Build

```bash
cp ~/.ssh/id_ed25519.pub discovery/authorized_keys   # optional; see "Shell access"
./build-overlay.sh
# -> dist/discovery.apkovl.tar.gz
```

Pure POSIX/bsdtar; no Alpine or Docker needed to build. The start script is
validated with `shellcheck -s sh`.

The ssh key is a build input rather than a file in the tree — the artifact goes
to a public bucket, and a key that opens every discovery node should be rotatable
with a `cp` instead of a commit. `AUTHORIZED_KEYS=/path/to/key.pub` overrides the
default location. With no key the overlay simply ships none and the node boots
without ssh.

## Upload (to the Worker's R2 bucket)

The Worker serves the overlay from R2 at
`/discovery/discovery.apkovl.tar.gz`. Publish a freshly built overlay with:

```bash
./build-overlay.sh && wrangler r2 object put ipxe-boot-assets/discovery/discovery.apkovl.tar.gz \
  --file dist/discovery.apkovl.tar.gz --content-type application/gzip
```

Or, from the services/ipxe repo, point its `scripts/upload-to-r2.sh` at the
artifact:

```bash
DISCOVERY_APKOVL=/path/to/ipxe/discovery/dist/discovery.apkovl.tar.gz \
  ./scripts/upload-to-r2.sh
```

## Boot flow

```
1. Machine PXE boots -> iPXE menu -> "Discovery" (alpine-netboot entry)
2. iPXE loads vmlinuz-lts + initramfs-lts from the Alpine CDN, with a cmdline
   the Worker renders: modloop=, alpine_repo=, apkovl=<worker>/discovery/...,
   ip=dhcp, ipxe_api=<worker-origin>.
3. initramfs dhcp's eth0, pulls modloop + apkovl, boots to tmpfs.
4. openrc starts the local service -> discovery.start:
     a. apk add curl ca-certificates jq (repo is on the cmdline)
     b. inventory: MACs, CPU, RAM, disks, DMI, arch
     c. POST ${ipxe_api}/api/machines/register {mac, arch, inventory}
        - 201 -> save {id, token} to tmpfs, poll for assignment
        - 409 -> MAC known but we hold no token; print + idle with a shell
     d. every 30s: GET /api/machines/:id/assignment (Bearer token)
        - 204 -> print waiting status, keep polling
        - 200 + role -> print role, reboot (next PXE boot serves the install)
5. A getty stays on tty1 throughout — rescue shell for free.
```

`ipxe_api` is read from the kernel cmdline (`ipxe_api=...`), defaulting to
`https://ipxe.cloudcompute.com`. An `IPXE_API` environment variable overrides
it (used by the container/CI tests). `POLL_SECONDS` is likewise overridable for
tests; it defaults to 30.

The register token is minted once by the Worker (trust-on-first-use) and only
its SHA-256 hash is stored server-side, so the plaintext lives only in the
machine's tmpfs. On reboot the machine re-registers by MAC; a machine that lost
its token to a stale registration gets a 409 and idles with a clear message
rather than clobbering the row.

## What the node reports while it boots

The check-in endpoint (`/api/checkin?mac=&arch=&target=&stage=&detail=`) is the
only channel a headless node has, so everything rides it. `discovery-beacon`
fires once per runlevel — `stage=sysinit`, `rc-boot`, `rc-default` — which proves
the boot got that far. `discovery-heartbeat` runs from sysinit onwards and
answers the question a per-runlevel beacon cannot: what is it sitting in *now*.
It reads `/run/openrc/starting`, and sends `stage=starting` while a service is
starting, `stage=stuck-in` once the same one has persisted across samples, and
`stage=heartbeat` with a count of started services when nothing is in flight.

`target` always stays `discovery` — it names the *image* that is booting, and
the Worker filters the boot log by it to find discovery boots. Everything the
node knows and the Worker cannot derive goes in `detail`: the openrc state, how
long this boot has been running, how long it has been reporting the same thing,
and the board's health.

```
detail=<state>_up<uptime>_for<held>[_t<degC>][_uv][_thr][_pwr-ok]

modloop_up95_for30_t58_pwr-ok      95 s in, 30 s starting modloop, 58 C, supply fine
started-14_up3600_for3400_t71_uv_thr  idle an hour, 71 C, under-voltage and throttling
started-14_up600_for580_t44        x86: a temperature, no firmware to ask
```

`up` is the load-bearing field. It is what lets the Worker anchor on when the
boot started rather than on when the node last spoke, which is the difference
between "still talking, therefore fine" and "still talking, and stuck for fifty
minutes". `/proc/uptime` is also the only clock worth quoting from an RTC-less
board mid-boot: monotonic, and right on the first tick.

This used to live in `target`, which meant every heartbeat arrived claiming to
be a boot of an image called `networking_t45_uv` — so the Worker's target filter
discarded exactly the reports that mattered, and a permanently wedged node was
the one thing the fleet dashboard could not see.

Under-voltage is the Pi's signature failure and it presents as everything being
slightly wrong rather than as an error, which makes it expensive to chase from
the outside. It comes from the firmware — the raw throttle word on kernels that
still expose `get_throttled`, otherwise the `rpi_volt` hwmon device's
under-voltage alarm, which is what a mainline aarch64 kernel offers. Neither is
a record of the whole boot: `raspberrypi-hwmon` polls the firmware every two
seconds and clears the sticky bits as it goes, so each read describes about the
last two seconds. The heartbeat therefore samples the power bits far faster than
it reports them and latches what it saw — the reads are local files, and only
reporting costs requests.

Reporting stays deliberately quiet: one instance (pidfile), a report only when
something changes, a keepalive every five minutes otherwise, and a floor between
health-driven reports so a supply flapping in and out of brown-out cannot become
its own flood. Temperature reports on movement rather than on value, so a
reading resting on a boundary never oscillates. An earlier heartbeat put ~2
requests/second into an endpoint that rate-limits at 60/min; do not regress it.

## Shell access

`discovery-sshd` puts dropbear on the node, so it is a machine an operator can
log into rather than one that can only be inferred from telemetry:

```bash
ssh root@<node-ip>    # key-only; the key you built into the overlay
```

dropbear rather than openssh: one small package, no separate keygen package to
fetch, and `-R` mints host keys on demand — which suits a node with nowhere to
persist them. Each boot therefore has new host keys, so expect ssh's
changed-host-key warning on a re-boot of the same machine.

Anything scripted against the node has to opt out of host-key pinning or it
breaks on the second boot it ever sees. A scoped stanza keeps that opt-out from
leaking to hosts whose keys are actually stable:

```
Host <node>
  User root
  ProxyJump <relay>
  UserKnownHostsFile /dev/null
  StrictHostKeyChecking no
  LogLevel ERROR
```

Pinning can't be restored by shipping a fixed host key in the overlay — the
overlay is published to a world-readable bucket, so a private key in it is a
public key in the worst sense. Accepting TOFU-per-boot inside the LAN is the
honest trade.

Everything about it is bounded and fails soft, because it necessarily runs at
boot on a RAM node: dropbear is not in the base image, so it comes from the
plain-http mirror on the kernel cmdline. `apk` is wrapped in `timeout` and
retried a bounded number of times, the whole script is detached by its openrc
unit so a slow mirror cannot hold the runlevel, and it sits in the *default*
runlevel after `local` — a network fetch in sysinit is what cost this project a
day, and the runlevel where a stall is most likely is the one where the
heartbeat, not ssh, is the diagnostic. Registration is the node's job; ssh is a
convenience, and it is wired so it can never come first.

With no key staged into the overlay the script logs that fact and exits without
starting a daemon: a node nobody can log into beats a node anybody can.

The operational consequence of running after `local`, which is easy to miss
until you need it: **a boot that wedges before `local` has no ssh either.** The
shell is available on a healthy node and absent on exactly the node you most
want to inspect, so out-of-band power (`watchdog/node_watchdog.py`) is the only
way back from a wedge, not a redundant convenience. Two habits follow. Restore a
known-good overlay to R2 the moment you confirm a node is wedged — the apkovl
only matters at next boot, so it costs nothing and makes every later reboot
healthy however it gets triggered. And when the node *is* healthy, prefer
`ssh root@<node-ip> reboot` over the plug: it re-runs the whole netboot chain,
a RAM node loses nothing, and it does not depend on a wifi relay that flaps.

Note also that openrc records `discovery-sshd` and `discovery-clock` as
`crashed` on a perfectly healthy node, because each backgrounds its daemon and
returns with nothing left to supervise. `rc-status` therefore cannot tell you
whether this node is well, and `need` on either silently skips the dependent —
use `after`. Tracked in `backlog/todo/openrc-crashed-services.md`.

## The frame role

For a visual end-to-end explanation and the glossary used across the bootstrap,
control-plane, media-source, and display layers, see the
**[Frame role field guide](../docs/frame-role.html)**.

A machine assigned `frame` (a `kind: "ram"` role — see the Worker's
`roles.ts`) reboots into this same overlay with `role=frame`, a single-use
`role_nonce`, and its `machine_id` on the kernel cmdline. `discovery.start`
then skips register-and-poll — the machine is already past that lifecycle —
redeems the nonce at `/api/machines/:id/role-ack` (the boot's
registration-equivalent: `assigned → active`, `last_checkin` stamped, stall
scan satisfied), and starts `frame-display`. Beacon and heartbeat report
`target=frame`, so frame boots are distinguishable in the feed and covered
by the stall scan.

`frame-display` supervises `frame-render.py`, whose shape is fetch →
compose → **sink**: it polls `frames/manifest.json` on the Worker, fetches
the current image, and letterboxes it onto every display the machine has.
Sinks are independent and probed each pass:

- **HDMI** (`/dev/fb0`, the vc4 console — one reason modloop stays): a raw
  Pillow blit, no X. The console shares this surface, so the fb repaints
  every poll pass to clean any text scribbled over it; tty1 keeps its getty
  underneath as the rescue path.
- **Inky e-ink** (SPI, via the inky library's EEPROM auto-detect): refreshes
  only on image change — a repaint costs ~half a minute of flashing.
  **Absent until the vendored-wheels work lands** (`backlog/todo/
  frame-role.md`, Slice 0b): the lib is pip-only and the node deliberately
  installs nothing from PyPI at boot. Until then boots log
  `no panel (No module named 'inky')`, honestly.
- **Preview** (`/tmp/frame-preview.png`) when no display exists — the same
  pipeline minus the device write, which is what makes a frame testable
  over ssh.

Publish images with the Worker repo's `scripts/publish-frames.sh <dir>` —
it uploads the images *and* regenerates the manifest, which is the contract
(an image without a manifest entry does not exist). Rotation is a function
of wall clock, not local state, so every frame in a fleet shows the same
picture and a reboot lands mid-schedule instead of restarting it.

## Verify with QEMU

The overlay was verified end-to-end against a local `wrangler dev` (see the
companion PR in fairchild/services). Fetch the pinned kernel/initrd once:

```bash
BASE=https://dl-cdn.alpinelinux.org/alpine/v3.22/releases/x86_64/netboot
mkdir -p cache
curl -sSL -o cache/vmlinuz-lts   "$BASE/vmlinuz-lts"
curl -sSL -o cache/initramfs-lts "$BASE/initramfs-lts"
```

Start `wrangler dev` in the services/ipxe repo (serves on :8787 with local
D1/KV/R2), upload the overlay into the local preview bucket, then:

```bash
qemu-system-x86_64 \
  -M q35 -m 2048 -smp 2 \
  -kernel cache/vmlinuz-lts -initrd cache/initramfs-lts \
  -append "modules=loop,squashfs,sd-mod,usb-storage,virtio-net,virtio-pci \
modloop=https://dl-cdn.alpinelinux.org/alpine/v3.22/releases/x86_64/netboot/modloop-lts \
alpine_repo=https://dl-cdn.alpinelinux.org/alpine/v3.22/main \
apkovl=http://10.0.2.2:8787/discovery/discovery.apkovl.tar.gz \
ip=dhcp ipxe_api=http://10.0.2.2:8787 console=ttyS0,115200" \
  -netdev user,id=n0 -device virtio-net-pci,netdev=n0,mac=52:54:00:12:34:56 \
  -display none -serial file:cache/serial.log -no-reboot
```

`10.0.2.2` is the host as seen from QEMU's user-mode network, so the guest
reaches `wrangler dev` on the host's `:8787`. Simulate an assignment while it
polls:

```bash
wrangler d1 execute ipxe-machines --local \
  --command "UPDATE machines SET state='assigned', role='worker' WHERE mac='52:54:00:12:34:56'"
```

The serial log shows registration (201 + id + token), the 30s poll returning
204, then `assignment received: role=worker. Rebooting into install.`

Real-hardware validation is Michael's step.

## arm64

x86_64 only for now. arm64 is a follow-up: add an `aarch64` netboot distro entry
(`alpineArch: "aarch64"`) and verify under `qemu-system-aarch64 -M virt`. The
start script is already arch-agnostic (it reads `uname -m` and handles the arm
DMI/`/proc/cpuinfo` differences).

> **Build before you upload.** `dist/` is gitignored, so it can trivially be older than
> `overlay/`. The node and the Worker share a wire format — the heartbeat's `detail` field is
> parsed by the Worker's stall detector — so publishing a stale tarball ships a node the Worker
> can no longer read, with every test still green on both sides.
