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
Serves custom-compiled iPXE binaries    Generates iPXE boot menus
Binaries embed the chain URL  ─────→    /boot.ipxe, /menu/:id.ipxe
                                        Serves custom binaries from R2
                                        Boot telemetry via KV
```

## Build & Test

```bash
# Build container locally (context is the repo root — the builder stage
# compiles iPXE from build/, so ./bootstrap alone is not enough)
docker build -f bootstrap/Dockerfile -t ipxe-bootstrap .

# Run (requires host networking for DHCP)
docker run --rm --net=host --cap-add=NET_ADMIN ipxe-bootstrap

# With custom settings
docker run --rm --net=host --cap-add=NET_ADMIN \
  -e IPXE_SERVER_URL=https://ipxe.cloudcompute.com \
  -e DHCP_RANGE=10.0.0.0 \
  ipxe-bootstrap

# Compile just the iPXE binaries to build/dist/ (and refresh the sha256 record)
./build/build.sh
```

The chain URL is baked into the binaries at build time via `ARG IPXE_SERVER_URL`
(passed to the builder stage / `build.sh`). The runtime `-e IPXE_SERVER_URL` only
affects the legacy user-class fallback in `dnsmasq.conf.template`, not the
embedded script.

No unit tests in this repo — the container is tested by booting a machine. The Worker service has Vitest tests (`bun run test` in the services/ipxe repo).

## CI/CD

GitHub Actions (`.github/workflows/build-push.yml`) triggers on changes to `bootstrap/**` or `build/**`:
- Builds multi-arch image (`linux/amd64`, `linux/arm64`) via QEMU + buildx (the iPXE builder stage compiles under emulation; ~a few min per target)
- Pushes to `ghcr.io/fairchild/ipxe-bootstrap` with branch/SHA/latest tags
- PRs build but don't push

## Boot Chain

```
1. Machine PXE boots → dnsmasq responds (proxy DHCP)
2. Firmware downloads custom iPXE binary via TFTP (arch auto-detected: BIOS/UEFI x86-64/ARM64)
3. iPXE runs its embedded script: retry DHCP, then chain to
   https://ipxe.cloudcompute.com/boot.ipxe?arch=${buildarch} over HTTPS
   (TLS validated against pinned root CAs — no ca.ipxe.org callout)
4. Worker returns the arch-filtered boot menu
5. User selects OS → Worker returns per-distro iPXE script → machine boots
```

The `?arch=${buildarch}` param matches the Worker's arch-detector convention (`services/ipxe/src/scripts/templates.ts`) so menu filtering still works. The dnsmasq user-class stanza (second-DHCP → boot URL) stays as a fallback for stock binaries but is no longer load-bearing.

The architecture detection in `dnsmasq.conf.template` maps PXE client-arch options to binaries:
- `0` → `undionly.kpxe` (BIOS x86; `ipxe.pxe` is also built as a broken-UNDI fallback)
- `7`, `9` → `ipxe.efi` (UEFI x86-64)
- `11` → `ipxe-arm64.efi` (UEFI ARM64)

## Key Design Decisions

- **Custom iPXE, compiled from pinned source** (`build/`): the container's builder stage compiles iPXE from a pinned upstream commit (v2.0.0, `12798ec2…`) via the shared `build/compile-ipxe.sh`. Each binary embeds a boot script (retry DHCP → chain to the menu over HTTPS) and pinned root-CA fingerprints (`TRUST=`), so there is no `boot.ipxe.org` download and no `ca.ipxe.org` trust dependency. Supply-chain integrity is the pinned commit (the build aborts if the clone's HEAD differs); iPXE stamps build metadata so the binaries are not bit-reproducible, and `bootstrap/ipxe-binaries.sha256` is a regenerated reference record, not a gate. To bump the iPXE version, change `IPXE_REF`/`IPXE_COMMIT` in `compile-ipxe.sh` and rerun `build/build.sh`.
- **Trusted roots** (`build/certs/`): iPXE trusts by fingerprint and can only anchor on a cert the server actually presents. `ipxe.cloudcompute.com` (Cloudflare) presents a Google Trust Services ECDSA chain whose top cert is GTS Root R4 **cross-signed by GlobalSign** — a different DER (and fingerprint) from the self-signed R4 root, so the cross-signed cert is the load-bearing anchor (`gtsr4-globalsign.pem`). Self-signed R4, GTS R1, and ISRG Root X1 are embedded as inert hedges against CDN cert rotation. This cross-sign subtlety was caught by the QEMU boot test — see `build/certs/README.md`.
- **Build context is the repo root**: `bootstrap/Dockerfile` COPYs `build/` for the builder stage, so build with `docker build -f bootstrap/Dockerfile .` (CI passes `context: .`). The builder is pinned to `linux/amd64` and cross-compiles each target (i686 for BIOS, native for x86_64-efi, aarch64 for arm64-efi).
- **Proxy DHCP** (`port=0`, `dhcp-range=...,proxy`): Never assigns IPs. Works alongside any existing DHCP server.
- **envsubst templating**: `dnsmasq.conf.template` uses `${IPXE_SERVER_URL}` and `${DHCP_RANGE}` — substituted at container startup, not build time.
- **Alpine 3.20 runtime**: minimal final image — only `dnsmasq` and `envsubst`; all compilation happens in the discarded Debian builder stage.
