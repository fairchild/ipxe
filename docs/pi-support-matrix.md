# Which Raspberry Pi can be a frame

The frame boots through UEFI. That one sentence decides this whole table, so it
is worth stating plainly before the rows: the boot chain is

```
UEFI firmware → our iPXE binary (EFI/BOOT/BOOTAA64.EFI) → HTTPS → boot menu → Alpine
```

iPXE is an EFI application. It needs UEFI to be loaded at all, and it needs
UEFI's Simple Network Protocol to reach the network. So a Pi can run this chain
only where somebody maintains UEFI firmware for it — which is a much narrower
set than the Pis that can "network boot" in the Raspberry Pi sense.

Those are different mechanisms and the distinction matters. Raspberry Pi's own
netboot has the SoC's ROM pull `start.elf` over TFTP; it yields the Pi firmware
booting a kernel from a TFTP server on the local segment. Ours yields iPXE
fetching a signed menu over HTTPS from a public endpoint, with pinned trust
roots and no LAN infrastructure at all. A model supporting the first tells you
nothing about whether it supports the second.

## The matrix

| Model | CPU / userland | UEFI firmware | Boot chain | Display stack | Physical proof |
|---|---|---|---|---|---|
| **Pi 4 Model B** | BCM2711, AArch64 | pftf/RPi4 **v1.38**, Devicetree mode | **Works** | `aarch64` bundle | **Proven** — netboots, registers, renders |
| **Pi 400 / CM4** | BCM2711, AArch64 | same image (DTBs shipped) | Expected | same bundle | **Unverified** — no hardware tested |
| **Pi 5** | BCM2712, AArch64 | none maintained | **No path** | bundle would fit | **Unverified** — blocked upstream |
| **Pi 3B / 3B+** | BCM2837, AArch64 | pftf/RPi3 v1.52 | Unproven | `aarch64` bundle | **Unverified** — see below |
| **Pi 2B v1.2** | BCM2837, AArch64-capable | none targets it | Unproven | `aarch64` bundle | **Unverified** |
| **Pi 2B v1.0/v1.1** | BCM2836, ARMv7 only | none exists | **No path** | needs `armv7` bundle | **Unverified** |

"Proven" means this hardware did it, observed. Everything else is
documentation-derived and labelled as such — no row is inferred from the row
above it.

One distinction inside the proven row. What has been observed on the Pi 4 is
the *chain*: pftf v1.38 in Devicetree mode, our iPXE binary, HTTPS to the boot
endpoint, Alpine to a rendered picture — netbooted from a bench Pi whose card
carried stock pftf firmware and a PXE-first boot order. The card image that
`scripts/build-pi-uefi-card.sh` assembles from those parts (SD/MMC first in its
boot order, iPXE loaded from the card's own `EFI/BOOT/`) has been verified
structurally but not yet booted from a written card. Until one is, the image
counts as assembled from proven parts, not proven itself; the setup page says
the same, and the note comes out of both when the first card boot lands.

## Why each unsupported row is unsupported

**Pi 5** has no maintained UEFI. The one EDK2 port, `worproject/rpi5-uefi`, was
archived in February 2025; its last release reports problems on D0 boards and
with newer EEPROM firmware. Nothing about the Pi 5 makes it unsuitable — the
display bundle would run on it unchanged — but without firmware to load iPXE
there is no chain to join. This is the row most likely to change, and it
changes upstream, not here.

**Pi 3B / 3B+** is the genuinely open question. UEFI exists and is AArch64, so
the architecture is right and the display bundle would not need rebuilding. Two
things are unverified and neither can be settled from a desk: the pftf/RPi3
Readme does not document network boot, and the Pi 3's Ethernet hangs off the
USB controller (LAN9514 on 3B, LAN7515 on 3B+) rather than being a memory-
mapped genet, so whether the firmware exposes a UEFI network device for iPXE to
use is a question about that firmware's driver set. Answering it takes one card
and one boot, which is the recommended next experiment if the fleet needs to
grow onto Pi 3 hardware.

**Pi 2B** splits on revision, which is why the row is split. The v1.2 revision
is BCM2837 — the same silicon as the Pi 3B, and the reason Raspberry Pi's own
documentation groups them for USB-host and Ethernet boot. But no UEFI project
targets a Pi 2, and pftf/RPi3's images are built and tested for the Pi 3. A
v1.2 might run them; nobody has said so, and we have not tried. The v1.0 and
v1.1 revisions are BCM2836, ARMv7 only, and are out on two independent counts:
no UEFI, and a 32-bit userland the current bundle does not target.

## The ARMv7 question

Supporting a 32-bit Pi is not one decision but two, and only the cheap one is
done.

The display bundle is already architecture-parameterised —
`build-display-bundle.sh --arch armv7` builds against `linux/arm/v7` and writes
its own lock, because the C extensions (`spidev`, `gpiod`) are compiled against
one musl and one CPython and cannot be shared across architectures. That much
is a flag away, and the resulting bundle is untested because nothing can boot
to run it.

The expensive half is the boot path, and it does not exist. Until some UEFI
firmware loads an ARMv7 iPXE on a Pi 2, an `armv7` bundle has nothing to run
on. This is why the `frame` role still accepts `aarch64` only: the guard is not
a limitation to be removed but an accurate statement of where the chain has
been proven to end in a picture. Removing it would let an operator assign
`frame` to a machine that will register, report healthy, and never display
anything.

## The panel is not the constraint

Pimoroni documents the Inky Impression 4" (PIM600, 640×400, 7-colour) as
working with any Raspberry Pi carrying a 40-pin header, which includes every
model in the table — Pi 2B included. The panel is not what limits this list;
the firmware is.

Two panel-side details that do vary by Pi generation, for whenever a second
model does boot:

- **GPIO backend.** The driver reaches GPIO through `gpiod` (libgpiod v2),
  which talks to the kernel's character device and is SoC-agnostic. It needs a
  devicetree to identify the platform, which is the same requirement that makes
  Devicetree mode non-negotiable on the Pi 4.
- **SPI chip-select.** `dtoverlay=spi0-0cs` leaves GPIO8 unclaimed so the
  library can drive chip-select itself; it refuses a pin the kernel owns. The
  overlay is generic across Pi models.

## What would move a row

For any Pi in the "Unverified" column, the evidence that would change it is the
same and is not a document: flash a card, boot it, and watch for a registration
inside that boot. The setup page on the boot endpoint (`/frame`, operator token
required) is built for exactly that observation — it offers the published card,
then shows every boot event and registration since power-on, grouped by MAC. A machine that answers ping, or that heartbeats, has proven
neither — only a registration belonging to the current boot shows the chain
completed. For the display half, only an observed refresh on the panel counts;
a preview PNG and an HDMI framebuffer both render from the same pipeline
without the e-ink being present at all, which is exactly why the status report
names every active sink.
