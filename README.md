# iPXE Bootstrap

Proxy DHCP + TFTP container for network booting bare-metal machines into an iPXE boot menu. Runs alongside your existing DHCP server without interfering.

## Quick Start

```bash
docker run -d --net=host --cap-add=NET_ADMIN ghcr.io/fairchild/ipxe-bootstrap
```

`NET_ADMIN` is required — dnsmasq refuses to start without it.

## How It Works

```
firmware PXE → TFTP (stock iPXE binary) → DHCP again →
dnsmasq hands iPXE the boot menu URL → iPXE chains over HTTPS → boot menu
```

1. Machine powers on, firmware broadcasts PXE request
2. dnsmasq (proxy DHCP) responds with a TFTP boot file — no IP assignment, your real DHCP handles that
3. Firmware loads stock iPXE binary via TFTP
4. iPXE does a second DHCP request (identified by user-class `iPXE`)
5. dnsmasq responds with the HTTPS boot menu URL
6. iPXE fetches and renders the boot menu

Architecture is auto-detected: BIOS x86, UEFI x86-64, and UEFI ARM64.

The iPXE binaries are baked into the image at build time and verified against pinned sha256s (`bootstrap/ipxe-binaries.sha256`) — the container makes no network fetches at startup.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `IPXE_SERVER_URL` | `https://ipxe.cloudcompute.com` | HTTPS URL serving `boot.ipxe` menu |
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
  supplied boot info, the proxy tagged the arch, iPXE started and got the boot
  URL on its second DHCP, and the chain reached the target. A **proxy contract**
  phase additionally verifies each arch → stock-binary mapping (including UEFI
  arch 9, which no guest covers) and that the TFTP server serves each binary with
  the pinned sha256.

Two modes: **local** (default) chains to a plain-HTTP stub served from the
container — deterministic, and what CI runs; **live** chains to
`${IPXE_SERVER_URL}` (the real Worker over HTTPS). Per-guest logs land in
`lab/out/` (gitignored). CI runs BIOS + UEFI x86 in local mode on every PR that
touches `bootstrap/` or `lab/` (`.github/workflows/boot-test.yml`).

**What it proves:** proxy/authoritative DHCP coexistence, DHCP arch detection
(client-arch → tag → binary), TFTP integrity of the stock binaries, iPXE's
user-class second DHCP and URL handoff, and the HTTP(S) chain to a rendered menu.

**What it can't:** the *stage-1 fetch of the stock iPXE binary by a dumb firmware*
is asserted at the DHCP/TFTP layer, not via a continuous guest boot — QEMU's own
network-boot ROMs are already iPXE (they send user-class `iPXE` on their first
request, short-circuiting to stage 2), and Debian's OVMF/AAVMF ship no UEFI
network stack, so the UEFI guests boot the stock `ipxe.efi`/`ipxe-arm64.efi` from
a virtual ESP instead of TFTP-loading it. Real-hardware specifics — NIC UNDI
quirks, firmware bugs, and Secure Boot — remain out of scope.
