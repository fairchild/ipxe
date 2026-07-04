---
priority: 1
timeout: 1d
arc: validation
dependencies:
  pin-ipxe-binaries-plan: "lab boots the binaries baked by the pinned image; stack on fix/pin-ipxe-binaries"
---

# QEMU boot lab: repeatable end-to-end PXE validation

**Repo: `~/code/ipxe`, new `lab/` directory.** Priority raised 2026-07-04: Michael wants maximum verification possible from this machine (macOS + OrbStack + QEMU) — real hardware boots are expensive, the lab makes everything short of firmware quirks machine-checkable.

Problem: Docker on macOS can't put the bootstrap container on the real LAN (`--net=host` = OrbStack's VM, not the Mac's L2), so until now "verified" stopped at "dnsmasq starts". The novel behavior — proxy DHCP coexisting with a real DHCP server, arch-detection tags, TFTP handoff, iPXE's second DHCP user-class dance, HTTPS chain — was only testable by booting hardware.

Design: one self-contained privileged Linux container ("boot lab") that reproduces the production topology inside its own network namespace:
1. Creates `br0`, no connection to outside needed except HTTPS egress for the chain step.
2. Runs an *authoritative* dnsmasq (DHCP assigning IPs on 10.77.0.0/24) — stands in for the home router.
3. Runs the bootstrap container's dnsmasq *proxy* config verbatim (COPY /tftpboot + dnsmasq.conf.template from the `ipxe-bootstrap` image via multi-stage `COPY --from`; envsubst with DHCP_RANGE=10.77.0.0 — the lab must consume the production image, not reimplement it).
4. PXE-boots QEMU guests (TCG, no KVM needed) attached to br0 via tap, capturing serial/console output:
   - BIOS x86: `qemu-system-x86_64 -M pc -boot n` → expects undionly.kpxe path
   - UEFI x86-64: OVMF firmware → expects ipxe.efi path
   - UEFI arm64: qemu-system-aarch64 + AAVMF → expects ipxe-arm64.efi path
5. Asserts, per guest, greppable milestones in the captured output: (a) firmware got an IP from the *authoritative* dnsmasq while boot info came from the proxy (check both logs), (b) correct binary was fetched via TFTP for the arch, (c) iPXE started and its second DHCP got the boot URL (user-class tag in proxy log), (d) chain to ${IPXE_SERVER_URL}/boot.ipxe succeeded — menu text visible in serial output. Default IPXE_SERVER_URL=https://ipxe.cloudcompute.com (live Worker); overridable to a wrangler-dev tunnel.

Deliverables:
- `lab/Dockerfile`, `lab/run-lab.sh` (bridge + dnsmasq×2 + qemu orchestration), `lab/expect/*.txt` or grep assertions in the script.
- `Makefile` or `scripts/test-boot.sh` at repo root: `make test-boot` builds bootstrap image + lab image and runs all three guests, exit code = pass/fail, per-guest logs to `lab/out/`.
- CI job (separate workflow or job in build-push.yml) running the BIOS + UEFI x86 guests at minimum (TCG in GitHub Actions is slow; time-box and mark arm64 optional if >10 min).
- README section: how to run, what it proves, what it can't (firmware quirks, Secure Boot).

Timeouts matter: TCG iPXE boot should reach chain in tens of seconds; give each guest a hard 5-min cap so CI can't hang.

Verify locally on OrbStack before PR. Branch from fix/pin-ipxe-binaries (PR #1) since it consumes that image layout; `gh pr create --base fix/pin-ipxe-binaries`.

Outcome: merge-ready PR with the three guest logs pasted/attached showing the full chain.

---
- 2026-07-04T18:38:35Z advanced to=doing claimer=fairchild@blue branch=main
