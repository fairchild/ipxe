---
priority: 1
timeout: 2w
arc: closed-loop
---

# Own the discovery node's boot image

**Decided 2026-08-01.** Supersedes the "consume Alpine's diskless netboot" shape
for the discovery node. See also `pi-closed-loop-plan.md`.

## Intent

Build the ephemeral node's boot image ourselves, so what a machine runs is an
explicit, auditable manifest rather than the emergent result of six network
fetches. Michael's framing: full control and explicit knowledge of what we have.

## Why, beyond the bugs

The boot chain today is kernel → initramfs → DHCP → modloop → apkovl → apk repo.
Six hops before the node can say anything, several circular: the driver that
brings up networking arrives *over* the network. Every hop fails invisibly, and
on a board with no console (Alpine's rpi kernel has no EFI framebuffer; `-lts`
has no `mdio-bcm-unimac` in its initramfs) invisible means a camera pointed at a
dark monitor. A night was spent there.

That chain is Alpine's diskless *workstation* model — a general-purpose machine
reconstituting itself from a mirror. The discovery node brings up a NIC,
inventories itself, POSTs once, and polls. It has been paying for a model it does
not use.

## Target

**Kernel + one initramfs, both from our R2, nothing else fetched before the node
can talk.** No modloop, no apkovl, no `alpine_repo` at boot.

The saving is real, not aesthetic: modloop is 53 MB of the 82 MB pulled per boot;
kernel + initramfs is 29 MB.

## Approach

Keep Alpine's signed, pinned kernel and extract the *specific* modules each arch
needs from its signed modloop into an initramfs we assemble — busybox, a CA
bundle, the discovery script, and a named list of drivers. Alpine's signing and
reproducibility are retained; the module set becomes a reviewable manifest
instead of a blob arriving at the worst possible moment.

Buildroot is the logical endpoint if assembling against Alpine's artifacts proves
awkward. The cost is owning a build system for a handful of machines, so it is
not the starting point.

Must hold for both arches — x86_64 and aarch64 — and the arm64 image must carry
`bcmgenet`, `mdio-bcm-unimac`, `broadcom` and `bcm_phy_lib`, whose absence from
`initramfs-lts` is the whole reason a Pi 4 could never bring up Ethernet.

## Sequencing note

This likely makes the current unexplained stall *moot* rather than solved: the
node dies somewhere in Alpine's diskless init, and that init does not exist in
this design. Diagnose it anyway — an unexplained failure carried into a new
architecture is a bug you get to find twice. The diagnostic SD card
(`scripts/build-pi-diagnostic-card.sh`) exists for exactly this.

## Non-goals

NixOS netboot is **parked as a future experiment**, not rejected — the one option
here that is genuinely reproducible, and interesting if provisioning ever goes
declarative. It fixes nothing currently blocking. Talos/Flatcar remain excluded by
the ROADMAP's own non-goals. Debian live with `toram` trades 300 MB for a working
EFI framebuffer; revisit only if console blindness bites harder than image size.

## Verify

A machine boots from kernel + initramfs alone, brings up its NIC, registers, and
appears in the dashboard — with no other network fetch in between, and with the
driver list in the image reviewable in the repo.
