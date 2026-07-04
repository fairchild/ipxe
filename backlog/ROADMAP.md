# ROADMAP

## Intent

A network-boot system that takes any machine Michael owns — modern UEFI, old BIOS, Raspberry Pi, VM — from bare metal to a registered, configured server. Power on a blank box at any site; it boots a discovery OS, inventories itself, and appears in a dashboard as *pending*; assign it a role; it installs unattended and reboots as a working server holding a per-machine token. Zero-touch to registered, one click to role, zero-touch from role to working server.

Two repos form the system: this one builds the bootstrap container (dnsmasq proxy DHCP + TFTP, `ghcr.io/fairchild/ipxe-bootstrap`); `~/code/services/ipxe` is the Cloudflare Worker at `ipxe.cloudcompute.com` serving menus, scripts, binaries, and the registry. Most roadmap work lands in the Worker; this backlog coordinates both.

## Principles

- Identity before automation: a machine must prove who it is (first-boot token, TOFU) before registration drives what it installs. Retrofitting auth after machines depend on the flow is the expensive order.
- The bootstrap container stays minimal — proxy DHCP + TFTP and nothing else. Intelligence lives in the Worker.
- Runs on multiple sites Michael operates; not (yet) a product others deploy. Threat model follows from that.
- Pets and ephemeral boots, not an immutable fleet: unattended installers (preseed/autoinstall) + netboot-to-RAM, not Talos/Flatcar.
- Know the trust chain at every hop; where a hop is unauthenticated (TFTP), say so rather than pretend.

## Current Focus

Wave 1 — pay the security debts that get more expensive later, and land machine identity: pin iPXE binaries into the container image, put auth on the fleet-listing endpoint, and build first-boot token issuance in the Worker (with a D1 machine registry — KV latest-event-per-device is already creaking).

Wave 2 — the pipeline spine: a RAM-booting discovery OS that inventories hardware, registers as pending, and polls for a role; then role assignment serving per-machine Debian preseed with the token delivered to disk and a firstboot check-in closing the loop.

## Priorities

0. `validation` — QEMU boot lab: repeatable end-to-end PXE verification from this Mac (raised to top 2026-07-04 — verify as much as possible without hardware)
1. `security-debts` — binary pinning, /api/boots auth, repo hygiene
2. `machine-identity` — first-boot token, D1 registry
3. `discovery-os` — RAM-boot inventory image + pending-machine flow
4. `unattended-install` — role assignment → preseed → token on disk → stage=os check-in
5. `boot-breadth` — custom iPXE build (embedded chain URL + CA), Pi netboot, UEFI HTTP boot, more distros
6. `secure-boot` — deferred: document disable-SB per vendor now; MOK enrollment via signed shim later. Hard edges: SBAT revocation churn, key custody, MokManager needs console.

## Non-goals

- Kubernetes/immutable fleet provisioning (Talos, Flatcar) — the fleet is pets and lab boots.
- TPM-backed attestation — right answer for hostile networks, wrong cost now; TOFU tokens suffice for sites we run.
- Secure Boot signing infrastructure in the near term (see priority 6).
- Public product hardening (multi-tenant, docs for strangers) until the pipeline works for us.
