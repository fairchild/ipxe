# Raspberry Pi 4 UEFI netboot — field notes

The path: SD card holding [pftf/RPi4](https://github.com/pftf/RPi4) UEFI firmware
with our `ipxe-arm64.efi` as `EFI/BOOT/BOOTAA64.EFI` → iPXE's embedded script
(DHCP, NTP, chain over pinned TLS) → boot menu → an Alpine diskless node whose
kernel, initramfs and modloop are fetched from our own origin and run entirely
in RAM.

Everything below was learned booting real Pi 4 hardware. None of it reproduces
under QEMU or UTM; the last section says why.

## The card is a shim, not an installation

Pi 4 firmware has no UEFI and no HTTP boot of its own, so something has to be on
local storage. That something is deliberately minimal: firmware plus one EFI
binary, written once. Which OS, which kernel, which role — every subsequent
decision is served over HTTPS, so a card built today keeps working as the fleet
changes.

That framing decides where fixes go. The temptation when a boot fails is to fix
it on the card: drop a script, an `autoexec.ipxe`, a config file. Of the
failures below, three are fixed in the binary or on the server and exactly one
genuinely lives on the card — and it's a firmware variable, not a file.

## A board without a clock can't validate a certificate

First boot died at `022fe4`, "Stale (or premature) OCSP response". The Pi 4 has
no RTC; it comes up somewhere near the epoch, and an OCSP response has a
validity window that a clock that far off can't land inside. Nothing about the
certificate chain is wrong — the machine just doesn't know what time it is.

The fix is `#define NTP_CMD` in the build config plus `ntp` between DHCP and
chain in [`build/embed.ipxe.template`](../build/embed.ipxe.template):

```
dhcp || goto retry
ntp time.cloudflare.com || ntp pool.ntp.org || echo No NTP - TLS may fail on RTC-less boards
chain __IPXE_SERVER_URL__/boot.ipxe?arch=${buildarch} || goto retry
```

Ordering is the whole point: the clock has to be set after there's a lease and
before anything reaches for TLS. The `||` fallthrough to a warning rather than a
hard failure is deliberate too — a board that *does* have a working clock
shouldn't be bricked by an unreachable time server.

## Pinned trust decides where you're allowed to fetch from

Second boot: `0216eb`, "No usable certificates", fetching the installer kernel
from `deb.debian.org`. That is not a bug, it's the direct consequence of
`TRUST=`. iPXE trusts a chain the moment it reaches a *presented* certificate
whose SHA256 is compiled in, so the reachable universe is exactly "hosts whose
chains anchor on one of our pins". Third-party mirrors are not in it, and
neither is `dl-cdn.alpinelinux.org` — same failure, later, for Alpine.

Two ways out, and the choice is about who owns the maintenance. Pinning the
mirrors' roots as well is cheap today and makes every one of those CDNs' root
rotations a rebuild-and-reflash event on our side, forever. Mirroring the assets
to our own origin keeps a single trust surface and costs the sync job. We took
the second: kernel, initramfs and modloop are synced to R2, verified against
upstream signatures at sync time, and served from the same host as the menu.

The pinning subtlety that bites before any of this — the load-bearing anchor is
a *cross-signed* root, not the self-signed one with the same public key — is in
[`build/certs/README.md`](../build/certs/README.md).

## genet has no PHY under ACPI

The third failure looks the most like dead hardware. Alpine's kernel boots,
mounts modloop, and then:

```
bcmgenet BCM6E4E:00 eth0: failed to connect to PHY
Unable to find mii
udhcpc: read error: Network is down, reopening socket   (forever)
```

`BCM6E4E` is the ACPI `_HID`. pftf's firmware presents ACPI tables by default,
and `bcmgenet` can't find its PHY over MDIO there — the MAC binds, no link ever
comes up, and udhcpc spins on a down interface.

The fix is in UEFI setup (Esc at the splash) → Device Manager → Raspberry Pi
Configuration → Advanced → set the system table mode to **Devicetree**, F10,
reboot. The Pi DTB describes the PHY. Confirmed on hardware: after the toggle
the same card booted straight through to a DHCP-leased RAM node.

## That setting lives in a file on the card you just flashed

pftf's firmware has no NVRAM chip. Every UEFI variable is written back into
`RPI_EFI.fd` on the FAT partition, which has three consequences worth knowing
before you debug the same thing twice:

- Reflashing the card reverts to ACPI, the broken mode. The fix lives on the
  card, not in the Pi.
- There is no `config.txt` knob for it — `config.txt` only influences UART
  overlay selection — so it cannot be set declaratively by dropping a file at
  image-build time.
- Firmware upgrades replace `RPI_EFI.fd` and wipe it again.

The variable is `SystemTableMode`: `0=ACPI`, `1=ACPI+Devicetree`,
`2=Devicetree`, defaulting to 0 on RPi4 (edk2-platforms `RPi4.dsc`). It can be
edited offline — the EDK2 authenticated variable store lives inside the `.fd`
(GUID `aaf32c78-947b-439a-a180-2e144ec37792`), and a same-size scalar edit moves
nothing, so the firmware-volume checksum stays valid; Devicetree is a two-byte
change. `RamLimitTo3GB` is worth clearing in the same pass for a RAM-booting
node, since Alpine's tmpfs is sized from available RAM.

Two caveats found the hard way. A virgin `RPI_EFI.fd` straight from the pftf
release zip has **zero** active variables — the firmware writes its default set
on first boot — so a card must boot once before it can be patched, or the image
has to be seeded from an already-booted one. And mtime is not a signal for
whether settings were saved: the firmware writes the varstore at block level
without touching FAT metadata, so the file can keep its archive timestamp while
holding dozens of written variables. Compare against a pristine release image
instead. (`virt-fw-vars` cannot parse the combined CODE+VARS layout, which is
why this gets hand-parsed.)

## An embedded script and an on-disk autoexec.ipxe are mutually exclusive

Relevant to anyone considering routing boot logic through the card.
[`build/compile-ipxe.sh`](../build/compile-ipxe.sh) passes `EMBED=embed.ipxe` to
all four binaries. The embedded image registers during `initialise()`;
`efi_autoexec_load()` runs later and only *registers* — there is no
`image_exec()` in it at all — and `first_image()` returns the head of the
registration list. So the embedded script always wins, and the
`file:autoexec.ipxe... Not found` on the boot screen is cosmetic.

The landmine is what happens when an autoexec *is* present. A registered but
never-executed image gets concatenated into the synthetic EFI `initrd.magic`
payload, and because `cpio_name()` returns the image's `cmdline` (NULL here) it
lands with no CPIO header — raw bytes prepended to the archive. That presents as
a corrupt initramfs during Linux boot with nothing pointing back at iPXE. Stock
non-EMBED builds never hit it, because `image_exec()` temporarily unregisters
the image it runs. It's latent for us today and arms the moment someone drops an
autoexec on the FAT partition.

If an on-card override is ever wanted anyway, the correct form is
`chain --autofree autoexec.ipxe || …`, which reuses the already-registered copy
and unregisters it on exit. Note that it also grants anyone with physical card
access arbitrary pre-boot script execution ahead of the pinned-CA chain, and
autoexec images don't get the unconditional `image_trust()` the embedded one
does. (Verified against the pinned v2.0.0 source: `usr/autoboot.c`,
`interface/efi/efi_autoexec.c`, `core/image.c`.)

Also settled while reading that source: iPXE 2.0.0 supports ECDSA fully —
`CRYPTO_PUBKEY_ECDSA` is on by default and the ECDHE-ECDSA suites are
registered. Claims that iPXE can't do ECC certificates are years stale; the
working GTS ECDSA chain is the proof.

## Keep the serial console, and point it at the right device

The failure above — network never comes up — is exactly the one where the
network isn't available to debug with, which makes serial the only view in. On
aarch64 that's a PL011 at `ttyAMA0`; `ttyS0` is the mini-UART and doesn't exist
unless its overlay is enabled. A kernel cmdline that hardcodes
`console=ttyS0,115200` for every architecture silently drops Pi serial output
and leaves a camera pointed at an HDMI monitor as the diagnostic.

## What the emulators can and can't tell you

[`lab/`](../lab/) and its QEMU guests validate the chain logic and the proxy
contract, and they're what caught the cross-signed-root problem before any
hardware did. They cannot catch anything on this page:

- a virt guest's RTC inherits host time, so the OCSP failure never appears;
- `-M virt` gives you virtio-net, not genet, so the ACPI/PHY failure cannot
  exist there — a VM reports a confident false pass;
- SLIRP user-networking may not route out at all depending on the host, which
  reads as a chain failure that isn't one.

Emulation is where the deterministic parts stay regression-tested. The clock,
the trust surface, and the NIC are hardware questions.

## Open threads

- **Devicetree by default in the card image.** Until the varstore edit above is
  part of the build, every Pi 4 needs the manual UEFI toggle once — the standing
  blocker to a genuinely zero-touch Pi.
- **"Operational" for the RAM node** — sshd, admin key, self-report — is
  [issue #6](https://github.com/fairchild/ipxe/issues/6). The node boots and
  takes a lease today; it isn't yet reachable.
