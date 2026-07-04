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
    etc/runlevels/default/local  symlink enabling the local service
    etc/runlevels/boot/*         networking + hostname enablement
    etc/network/interfaces       eth0 via dhcp
    etc/hostname                 "discovery"
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
./build-overlay.sh
# -> dist/discovery.apkovl.tar.gz
```

Pure POSIX/bsdtar; no Alpine or Docker needed to build. The start script is
validated with `shellcheck -s sh`.

## Upload (to the Worker's R2 bucket)

The Worker serves the overlay from R2 at
`/discovery/discovery.apkovl.tar.gz`. Publish a freshly built overlay with:

```bash
wrangler r2 object put ipxe-boot-assets/discovery/discovery.apkovl.tar.gz \
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
