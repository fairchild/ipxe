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
