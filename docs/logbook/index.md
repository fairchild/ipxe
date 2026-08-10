---
title: Logbook
---

Development notes, newest first. Each entry is written after something worked —
or after something failed in a way that took long enough to be worth the paper.

These are about building the system, not about what any particular machine is
doing right now. What they have in common: the interesting failures were all
things a passing test suite said nothing about.

---

### [First boot on metal: what a Raspberry Pi 4 taught](2026-07-20-pi4-first-boot-on-metal.md)

**2026-07-20** · Five boots to get a blank Pi 4 to a running RAM node. A board
with no clock can't validate a certificate; pinned trust quietly decides which
mirrors you're allowed to fetch from; and the onboard NIC has no PHY under ACPI.
Four of the five failures are invisible to the QEMU lab — a virt guest inherits
host time and gives you virtio-net, so it reports a confident false pass.

---

More entries as the hardware keeps teaching. The
[frame role field guide](../frame-role.html) covers the picture-frame path in
depth, and [the backlog](https://github.com/fairchild/ipxe/tree/main/backlog)
tracks what's in flight.
