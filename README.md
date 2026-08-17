# iPXE Bootstrap

Proxy DHCP + TFTP container for network booting bare-metal machines into an iPXE boot menu. Runs alongside your existing DHCP server without interfering.

The RAM-only picture-frame path has a standalone visual guide tracing the full
camera-to-glass path: Trips contribution and curation, credential boundaries,
iPXE boot, authenticated media delivery, rendering, and shared language:
**[Frame role field guide](https://fairchild.github.io/ipxe/frame-role.html)**
([source](docs/frame-role.html)).

A Raspberry Pi 4 joins that path from one generic SD card: `scripts/build-pi-uefi-card.sh`
writes a deterministic image (pinned UEFI firmware, our iPXE binary, the settings that
make Ethernet work; no secrets, no identity), `scripts/publish-card.sh` verifies and
publishes it, and the control plane's setup page — `/frame` on the boot endpoint,
operator token required — walks the rest: download with the checksum verified in the
browser, write, boot, watch the machine check in, assign the role. Which boards can run
it, and how each claim was established, is in
[docs/pi-support-matrix.md](docs/pi-support-matrix.md).

## Quick Start

```bash
docker run -d --net=host --cap-add=NET_ADMIN \
  --env-file /path/to/bootstrap.env \
  ghcr.io/fairchild/ipxe-bootstrap
```

`NET_ADMIN` is required — dnsmasq refuses to start without it. The environment
file must supply a random `BOOTSTRAP_TOKEN` of at least 32 characters and a
comma-separated `BOOTSTRAP_ALLOWED_MACS` allowlist. Keep that file outside the
repository and readable only by its operator. The container fails closed when
either value is absent.

## How It Works

```
firmware PXE → TFTP (custom iPXE binary) →
non-secret bootstrap script → site-local boot proxy →
authenticated HTTPS request to the control plane → boot menu or assigned role
```

1. Machine powers on, firmware broadcasts PXE request
2. dnsmasq (proxy DHCP) responds only when the machine MAC is explicitly allowlisted — no IP assignment, your real DHCP handles that
3. Firmware loads the custom iPXE binary via TFTP
4. iPXE follows the DHCP filename to a static TFTP script, which passes its architecture and MAC to the site-local boot proxy
5. The proxy checks both `BOOTSTRAP_CLIENT_CIDR` and `BOOTSTRAP_ALLOWED_MACS`, adds the server-side bearer, and requests `<IPXE_SERVER_URL>/boot.ipxe` over HTTPS
6. iPXE receives and runs the returned menu or role script; the long-lived bootstrap bearer never enters TFTP or device RAM

Architecture is auto-detected: BIOS x86 (`undionly.kpxe`, with `ipxe.pxe` as a broken-UNDI fallback), UEFI x86-64, and UEFI ARM64.

The binaries are compiled from a pinned iPXE upstream commit in the image's builder stage — see [`build/`](build/). Each embeds the DHCP-filename retry script and trusted root-CA fingerprints, so the container does not depend on iPXE's own CA infrastructure. Stock iPXE follows the same DHCP-provided bootstrap script.

The bearer proves that a request passed through the managed bootstrap; it is not
device identity. The MAC allowlist prevents the service from answering unrelated
LAN clients, but a MAC remains spoofable and is only a machine selector. The
device-side hop to the local proxy is plain HTTP, so the PXE network is a trust
boundary: an on-path client could observe or race the short-lived, one-use role
nonce returned by the control plane. Restrict the proxy to the boot VLAN or
trusted LAN, choose a narrow `BOOTSTRAP_CLIENT_CIDR`, and rotate the bearer if
the bootstrap host is compromised.

Boards without an RTC, without PXE-capable firmware, or with a NIC the generic kernel can't bring up need more than the chain above. The **[logbook](docs/logbook/)** collects what real hardware turned up — clock-before-TLS, what pinned trust does to your choice of mirror, genet's missing PHY under ACPI, and which of those emulation can and can't catch. It's published at [fairchild.github.io/ipxe](https://fairchild.github.io/ipxe/) alongside the field guides.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `IPXE_SERVER_URL` | `https://ipxe.cloudcompute.com` | HTTPS control-plane origin requested by the site-local boot proxy. |
| `DHCP_RANGE` | `192.168.1.0` | Network for proxy DHCP (e.g. `192.168.1.0`) |
| `BOOTSTRAP_CLIENT_CIDR` | `192.168.1.0/24` | Source network allowed to use the site-local boot proxy. Keep this as narrow as the PXE network permits. |
| `BOOTSTRAP_ALLOWED_MACS` | none; required | Comma-separated MAC addresses that may receive PXE offers and use the proxy. The service fails closed without it. |
| `BOOTSTRAP_TOKEN` | none; required | Random bearer shared only by the bootstrap container and control plane. Minimum 32 characters. Never commit it or place it in a TFTP artifact. |

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
      - BOOTSTRAP_CLIENT_CIDR=${BOOTSTRAP_CLIENT_CIDR:-192.168.1.0/24}
      - BOOTSTRAP_ALLOWED_MACS=${BOOTSTRAP_ALLOWED_MACS:?set in a mode-600 environment file}
      - BOOTSTRAP_TOKEN=${BOOTSTRAP_TOKEN:?set in a mode-600 environment file}
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
**production** binaries, boot proxy, and dnsmasq configuration via
`COPY --from`), then:

- runs an **authoritative** dnsmasq (the fake home router — leases only, no boot
  options) alongside the **proxy** dnsmasq (the production config, verbatim);
- boots a **BIOS x86**, a **UEFI x86-64** (OVMF), and a **UEFI ARM64** (AAVMF)
  guest on a bridge, capturing serial + DHCP/HTTP logs;
- asserts, per guest: the authoritative server leased the IP while the proxy
supplied boot info only for allowlisted machines, the proxy tagged the arch, iPXE started and passed through
  the authenticated local proxy, and the chain reached the protected target. A
  **proxy contract** phase additionally verifies each
  arch → binary mapping (including UEFI arch 9, which no guest covers), that the
  TFTP server serves each binary with the pinned sha256, and that the proxy hands
  every user-class client the non-secret bootstrap script.

Two modes: **local** (default) proxies to a protected in-container HTTP stub —
deterministic, and what CI runs; **live** proxies to `${IPXE_SERVER_URL}` over
HTTPS. Local mode proves that a bare upstream request gets `401`, an allowlisted
request through the boot proxy succeeds, a non-allowlisted request gets `403`,
and no TFTP artifact contains the long-lived proof. Per-guest logs land in `lab/out/`
(gitignored). CI runs BIOS + UEFI x86 in local mode on every PR that touches
`bootstrap/` or `lab/` (`.github/workflows/boot-test.yml`).

**What it proves:** proxy/authoritative DHCP coexistence, DHCP arch detection
(client-arch → tag → binary), TFTP integrity of the compiled binaries, iPXE's
embedded chain reaching a rendered menu through the authenticated proxy, and
the user-class second-DHCP bootstrap-script handoff for stock iPXE.

**What it can't:** the *stage-1 fetch of the stock iPXE binary by a dumb firmware*
is asserted at the DHCP/TFTP layer, not via a continuous guest boot — QEMU's own
network-boot ROMs are already iPXE (they send user-class `iPXE` on their first
request, short-circuiting to stage 2), and Debian's OVMF/AAVMF ship no UEFI
network stack, so the UEFI guests boot the stock `ipxe.efi`/`ipxe-arm64.efi` from
a virtual ESP instead of TFTP-loading it. Real-hardware specifics — NIC UNDI
quirks, firmware bugs, and Secure Boot — remain out of scope.
