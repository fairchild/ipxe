# iPXE Bootstrap

Proxy DHCP + TFTP container for network booting bare-metal machines into an iPXE boot menu. Runs alongside your existing DHCP server without interfering.

## Quick Start

```bash
docker run -d --net=host --cap-add=NET_ADMIN ghcr.io/fairchild/ipxe-bootstrap
```

`NET_ADMIN` is required — dnsmasq refuses to start without it.

## How It Works

```
firmware PXE → TFTP (custom iPXE binary) →
embedded script chains to the boot menu over HTTPS → boot menu
```

1. Machine powers on, firmware broadcasts PXE request
2. dnsmasq (proxy DHCP) responds with a TFTP boot file — no IP assignment, your real DHCP handles that
3. Firmware loads the custom iPXE binary via TFTP
4. iPXE runs its embedded script: retry DHCP, then chain to `<IPXE_SERVER_URL>/boot.ipxe?arch=<buildarch>` over HTTPS
5. iPXE fetches and renders the boot menu (TLS validated against pinned root CAs, no `ca.ipxe.org` callout)

Architecture is auto-detected: BIOS x86 (`undionly.kpxe`, with `ipxe.pxe` as a broken-UNDI fallback), UEFI x86-64, and UEFI ARM64.

The binaries are compiled from a pinned iPXE upstream commit in the image's builder stage — see [`build/`](build/). Each embeds the chain script and the trusted root-CA fingerprints, so the container makes no network fetches at startup and does not depend on iPXE's own CA infrastructure. The dnsmasq user-class stanza remains as a fallback for any stock iPXE binary but is no longer the primary path.

Boards without an RTC, without PXE-capable firmware, or with a NIC the generic kernel can't bring up need more than the chain above. [`docs/pi4-uefi-netboot.md`](docs/pi4-uefi-netboot.md) collects what booting real Raspberry Pi 4 hardware turned up — clock-before-TLS, what pinned trust does to your choice of mirror, genet's missing PHY under ACPI, and which of those emulation can and can't catch.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `IPXE_SERVER_URL` | `https://ipxe.cloudcompute.com` | Boot menu host for the dnsmasq user-class fallback. The embedded chain URL is baked at build time via `ARG IPXE_SERVER_URL` (see [`build/`](build/)), not this runtime var. |
| `DHCP_RANGE` | `192.168.1.0` | Network for proxy DHCP (e.g. `192.168.1.0`) |

## Architectures

Multi-arch image: `linux/amd64` and `linux/arm64`.

## Docker Compose

```yaml
services:
  bootstrap:
    image: ghcr.io/fairchild/ipxe-bootstrap:latest
    network_mode: host
    cap_add:
      - NET_ADMIN
    environment:
      - IPXE_SERVER_URL=https://ipxe.cloudcompute.com
      - DHCP_RANGE=${DHCP_RANGE:-192.168.1.0}
```

## QEMU Boot Lab

Docker on macOS can't put this container on the real LAN, so historically
"verified" stopped at "dnsmasq starts" — the interesting behavior (proxy DHCP
coexisting with a real DHCP server, arch detection, the TFTP handoff, iPXE's
second DHCP, the HTTP(S) chain) was only testable by booting hardware.

`lab/` is a self-contained privileged container that reproduces the production
topology in its own network namespace and boots real QEMU guests through the
whole chain, turning all of that into machine-checkable assertions.

```bash
scripts/test-boot.sh                       # local mode (deterministic), all guests
MODE=live scripts/test-boot.sh             # chain to the real Worker over HTTPS
GUESTS="bios uefi" scripts/test-boot.sh    # pick guests
```

It builds the bootstrap image, builds the lab on top of it (consuming the
**production** binaries and `dnsmasq.conf.template` via `COPY --from`), then:

- runs an **authoritative** dnsmasq (the fake home router — leases only, no boot
  options) alongside the **proxy** dnsmasq (the production config, verbatim);
- boots a **BIOS x86**, a **UEFI x86-64** (OVMF), and a **UEFI ARM64** (AAVMF)
  guest on a bridge, capturing serial + DHCP/HTTP logs;
- asserts, per guest: the authoritative server leased the IP while the proxy
  supplied boot info, the proxy tagged the arch, iPXE started and chained to the
  boot menu over HTTP(S) via the URL compiled into its embedded script, and the
  chain reached the target. A **proxy contract** phase additionally verifies each
  arch → binary mapping (including UEFI arch 9, which no guest covers), that the
  TFTP server serves each binary with the pinned sha256, and that the proxy still
  hands a user-class boot URL — the stock-iPXE fallback the custom binaries no
  longer depend on.

Two modes: **local** (default) chains to a plain-HTTP stub served from the
container — deterministic, and what CI runs; **live** chains to
`${IPXE_SERVER_URL}` (the real Worker over HTTPS). Because the chain target is
now compiled into the binary (`ARG IPXE_SERVER_URL` → the embedded script), not
merely handed over DHCP, each mode rebuilds the bootstrap image with a matching
`--build-arg IPXE_SERVER_URL`: the in-container stub (`http://10.77.0.1:8080`)
for local, the live Worker for live. Per-guest logs land in `lab/out/`
(gitignored). CI runs BIOS + UEFI x86 in local mode on every PR that touches
`bootstrap/` or `lab/` (`.github/workflows/boot-test.yml`).

**What it proves:** proxy/authoritative DHCP coexistence, DHCP arch detection
(client-arch → tag → binary), TFTP integrity of the compiled binaries, iPXE's
embedded chain reaching a rendered menu over HTTP(S), and — as a compatibility
check — the proxy's user-class second-DHCP URL handoff for stock iPXE.

**What it can't:** the *stage-1 fetch of the stock iPXE binary by a dumb firmware*
is asserted at the DHCP/TFTP layer, not via a continuous guest boot — QEMU's own
network-boot ROMs are already iPXE (they send user-class `iPXE` on their first
request, short-circuiting to stage 2), and Debian's OVMF/AAVMF ship no UEFI
network stack, so the UEFI guests boot the stock `ipxe.efi`/`ipxe-arm64.efi` from
a virtual ESP instead of TFTP-loading it. Real-hardware specifics — NIC UNDI
quirks, firmware bugs, and Secure Boot — remain out of scope.
