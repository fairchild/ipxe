---
priority: 4
timeout: 2w
arc: closed-loop
---

# A per-download claim token in the card image

Deferred from the setup-page work (2026-08-16). The card download is
authenticated, so in principle each download could carry a secret that ties
the machine that boots it to the operator session that fetched it: the watch
step would then know *which* new machine is yours instead of inferring it from
"appeared after you pressed the button", and a first boot could pre-select the
frame role without an unauthenticated auto-assign — the download itself is the
authenticated act.

Not straightforward, for four independent reasons; each is a small project.

1. **Nothing on the card is read at runtime.** UEFI loads `EFI/BOOT/BOOTAA64.EFI`
   and iPXE runs its embedded script; the FAT partition is never opened again.
   iPXE can read a local file only if built with `DOWNLOAD_PROTO_FILE`, and the
   embedded script would then `imgfetch file:/claim.ipxe` (or read `claim.txt`
   into a variable) before chaining, so the claim rides `?claim=` to
   `/boot.ipxe`. That is an iPXE rebuild and an `embed.ipxe` change.
2. **The image is gzipped, and the Worker cannot patch inside gzip.** The
   builder would reserve a fixed-length placeholder file at build time and the
   Worker would splice the token over it — which requires streaming the *raw*
   image from R2 and gzipping in the Worker (`CompressionStream` exists there;
   256 MB of mostly zeros compresses fast, but the image should first shrink
   to 64 MB). Storing raw and compressing per download also ends the
   byte-identical download: every copy differs by design.
3. **The Worker needs a claim table.** Mint at download (hash stored, TTL a
   day, bound to nothing but the fact of an authenticated download), redeem at
   `/boot.ipxe?claim=` → mark the machine "claimed by this download" and,
   optionally, carry a role chosen at download time into the assignment. The
   redemption must be single-use, and a claim seen twice is a cloned card —
   which is exactly the property the generic image was designed to have and
   would now be a signal instead.
4. **It changes what the card is.** Today one image is identical for every Pi
   in the fleet and carries nothing operational; the setup page says so and
   the matrix leans on it. A claim token is deliberately not a secret that
   grants anything by itself, but it is per-copy state on the card, and the
   documentation would have to say so honestly.

What it buys, against that: disambiguation when several machines boot at once
(today: an OUI tag and a timestamp), and one fewer click. The single-operator
model makes the first rare. Revisit if the fleet or the operator count grows;
until then the setup page's watch step is the answer.
