# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

iPXE bootstrap container — a proxy DHCP + TFTP server that network-boots bare-metal machines into an iPXE menu served by `ipxe.cloudcompute.com`. Runs alongside existing DHCP without interfering (proxy mode, no IP assignment).

## Relationship to services/ipxe

This repo builds and publishes the **bootstrap container** (`ghcr.io/fairchild/ipxe-bootstrap`). The companion repo ([fairchild/services](https://github.com/fairchild/services) under `ipxe/`) is a Cloudflare Worker that serves the boot menu, iPXE scripts, and binaries at `ipxe.cloudcompute.com`. The two repos form a complete system:

```
bootstrap container (this repo)          Worker service (services/ipxe)
─────────────────────────────           ──────────────────────────────
dnsmasq proxy DHCP + TFTP               Hono app on Cloudflare Workers
Serves stock iPXE binaries              Generates iPXE boot menus
Tells iPXE where to chain    ─────→    /boot.ipxe, /menu/:id.ipxe
                                        Serves custom binaries from R2
                                        Boot telemetry via KV
```

## Build & Test

```bash
# Build container locally
docker build -t ipxe-bootstrap ./bootstrap

# Run (requires host networking for DHCP)
docker run --rm --net=host --cap-add=NET_ADMIN ipxe-bootstrap

# With custom settings
docker run --rm --net=host --cap-add=NET_ADMIN \
  -e IPXE_SERVER_URL=https://ipxe.cloudcompute.com \
  -e DHCP_RANGE=10.0.0.0 \
  ipxe-bootstrap
```

No unit tests in this repo — the container is tested by booting a machine. The Worker service has Vitest tests (`bun run test` in the services/ipxe repo).

## CI/CD

GitHub Actions (`.github/workflows/build-push.yml`) triggers on changes to `bootstrap/**`:
- Builds multi-arch image (`linux/amd64`, `linux/arm64`) via QEMU + buildx
- Pushes to `ghcr.io/fairchild/ipxe-bootstrap` with branch/SHA/latest tags
- PRs build but don't push

## Boot Chain

```
1. Machine PXE boots → dnsmasq responds (proxy DHCP)
2. Firmware downloads iPXE binary via TFTP (arch auto-detected: BIOS/UEFI x86-64/ARM64)
3. iPXE does second DHCP (user-class "iPXE") → dnsmasq responds with boot menu URL
4. iPXE chains to https://ipxe.cloudcompute.com/boot.ipxe over HTTPS
5. User selects OS → Worker returns per-distro iPXE script → machine boots
```

The architecture detection in `dnsmasq.conf.template` maps PXE client-arch options to binaries:
- `0` → `undionly.kpxe` (BIOS x86)
- `7`, `9` → `ipxe.efi` (UEFI x86-64)
- `11` → `ipxe-arm64.efi` (UEFI ARM64)

## Key Design Decisions

- **Stock iPXE binaries**: Downloaded at container startup from `boot.ipxe.org`, not built from source. The Worker service repo has `scripts/build-ipxe.sh` for custom builds with embedded chain URLs (see the services repo).
- **Proxy DHCP** (`port=0`, `dhcp-range=...,proxy`): Never assigns IPs. Works alongside any existing DHCP server.
- **envsubst templating**: `dnsmasq.conf.template` uses `${IPXE_SERVER_URL}` and `${DHCP_RANGE}` — substituted at container startup, not build time.
- **Alpine 3.20**: Minimal image — only `dnsmasq`, `envsubst`, `curl`.
