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

## What else is in here

The bootstrap container is the oldest thing in this repo but no longer the
largest. Four other directories carry work a cold reader will otherwise miss:

- **`discovery/`** — the Alpine diskless overlay (apkovl) the netbooted node
  runs: beacons, heartbeat, clock, sshd, and the inventory/register script.
  Built by `discovery/build-overlay.sh`, uploaded to the Worker's R2 bucket.
  `discovery/README.md` is the reference for what the node reports and why.
- **`watchdog/`** — `node_watchdog.py`, out-of-band recovery via a Meross plug
  when a node stops answering. It reads plug state back after every command,
  which this hardware requires; see its README.
- **`scripts/`** — one-shot host tooling: build a diagnostic SD card, rescue a
  card, patch Raspberry Pi UEFI variables offline (`patch-rpi-uefi-vars.py`).
- **`lab/`** — a QEMU boot lab in a container (`run-lab.sh`). It runs an
  authoritative dnsmasq standing in for a site's existing DHCP server *and* the
  production proxy image beside it, so a boot test exercises the real bootstrap
  config and proves proxy DHCP still coexists with a server that knows nothing
  about it.
- **`backlog/`** — deferred work as one file per task, plus
  `backlog/doing/STATE.md`, which is the fastest path back into context after a
  break. Read it before planning anything.

### This repo is public

`fairchild/ipxe` is a public repository, and the lab notes in `backlog/` are the
easiest place to forget it. **No LAN addresses, Tailscale addresses, real MAC
addresses, hostnames, serial numbers, or credentials in committed files** —
including in a handoff document that only you expect to read. Write the role
instead (`<relay>`, `<node>`, "the plug's outlet channel"); the actual values
belong in `~/.config/ipxe-lab.env`, which is outside the tree and holds
`MEROSS_HOST`, `MEROSS_KEY`, `MEROSS_CHANNEL` and `DASHBOARD_TOKEN` already.

Example MACs in docs and tests should stay obviously fake — `52:54:00:…` is
QEMU's OUI, and `watchdog/` uses `dc:a6:32:11:22:33`. `discovery/authorized_keys`
is gitignored on purpose and must stay that way: the built overlay is published
to a world-readable bucket.

Worth internalising rather than checking at commit time: git history is public
too, so a scrub in a later commit narrows exposure but never undoes it.

### The cross-repo wire contract

The node and the Worker share a contract that nothing tests: the heartbeat's
`detail` field is what the Worker's stall detector parses. **Deploy the Worker
before uploading the overlay, and rebuild the overlay immediately before
uploading it.** Out of order, or from a stale `discovery/dist/`, the node speaks
a format the Worker cannot read while every test on both sides stays green,
because the tests live in different repositories and neither crosses the wire.
Verify a publish by comparing hashes rather than by trusting the upload:

```bash
curl -s http://ipxe.cloudcompute.com/discovery/discovery.apkovl.tar.gz | shasum -a 256
shasum -a 256 discovery/dist/discovery.apkovl.tar.gz
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
