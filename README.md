# iPXE Bootstrap

Proxy DHCP + TFTP container for network booting bare-metal machines into an iPXE boot menu. Runs alongside your existing DHCP server without interfering.

## Quick Start

```bash
docker run -d --net=host ghcr.io/fairchild/ipxe-bootstrap
```

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
