---
priority: 5
timeout: 3d
arc: boot-breadth
dependencies:
  pin-ipxe-binaries-plan: "changes how binaries get into the container image; build on the pinned layout"
---

# Custom iPXE build: embedded chain URL + real CA trust

**Repos: both.** The services repo already has `scripts/build-ipxe.sh` as a starting point.

Stock iPXE from boot.ipxe.org validates TLS via ipxe.org's cross-signing CA infrastructure — a runtime dependency on ca.ipxe.org and a trust root of ipxe.org rather than the real cert chain. A custom build fixes both, plus the flaky-UNDI class of BIOS NICs, by embedding:
- an embedded script (`#!ipxe` → dhcp → chain https://ipxe.cloudcompute.com/boot.ipxe with retry loop) so the second-DHCP user-class dance stops being load-bearing,
- `TRUST=<ISRG Root X1 and needed roots>` so HTTPS validates against real CAs with no ca.ipxe.org callout,
- targets: bios/undionly.kpxe, bios/ipxe.pxe (fallback for broken UNDI), x86_64-efi/ipxe.efi, arm64-efi/ipxe.efi.

Steps:
1. Review/extend `services/ipxe/scripts/build-ipxe.sh`; build inside Docker with ipxe upstream pinned to a tag/commit so it's reproducible on macOS.
2. Bake the custom binaries into the bootstrap container with pinned hashes (build job in `~/code/ipxe` CI), and publish to R2 so `/boot/:filename` serves the same ones for UEFI HTTP boot later.
3. dnsmasq.conf.template: user-class stanza stays (harmless; keeps stock-binary compatibility), embedded-script binaries won't need it.
4. Update CLAUDE.md Key Design Decisions (currently: "stock binaries, not built from source").

Verify: QEMU BIOS and UEFI boots chain to boot.ipxe over HTTPS with no ca.ipxe.org traffic (iPXE console output or tcpdump), and a network without proxy-DHCP second response still reaches the menu.

Outcome: merge-ready PR(s), cross-linked if both repos change.

---
- 2026-07-04T18:29:31Z advanced to=doing claimer=fairchild@blue branch=main
- 2026-07-04T18:30:12Z progress | agent dispatched, stacked worktree on fix/pin-ipxe-binaries, branch feat/custom-ipxe-build
- 2026-07-05T22:22:44Z progress | QEMU slirp boot caught real bug: CF presents GlobalSign-cross-signed GTS R4, iPXE pins exact fingerprint -> self-signed R4 anchor never matches; fixed w/ cross-signed anchor + hedges; rebuilding
- 2026-07-05T22:48:22Z advanced to=done
- 2026-07-05T22:48:23Z progress | PR=https://github.com/fairchild/ipxe/pull/4 iPXE v2.0.0 pinned @12798ec; QEMU BIOS boot chains HTTPS to LIVE menu, no ca.ipxe.org, anchor=GlobalSign-cross-signed GTS R4 (self-signed R4 never matches — iPXE pins presented certs); dnsmasq template edit dropped in favor of PR#3 pxe-service fix
