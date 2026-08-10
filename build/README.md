# build/ — custom iPXE compilation

Compiles iPXE from a pinned upstream commit into four binaries, each with an
embedded boot script (retry DHCP → chain to its DHCP filename) and a pinned set
of trusted root-CA fingerprints. The runtime bootstrap script, control-plane
origin, and proof stay outside the binary. This replaces the stock binaries
that used to be downloaded from `boot.ipxe.org` and validated TLS via
`ca.ipxe.org`.

## What gets built

| Output | Target | Use |
|--------|--------|-----|
| `undionly.kpxe` | `bin/undionly.kpxe` | BIOS x86, uses firmware UNDI NIC driver |
| `ipxe.pxe` | `bin/ipxe.pxe` | BIOS x86, iPXE's own NIC drivers (broken-UNDI fallback) |
| `ipxe.efi` | `bin-x86_64-efi/ipxe.efi` | UEFI x86-64 |
| `ipxe-arm64.efi` | `bin-arm64-efi/ipxe.efi` | UEFI ARM64 (cross-compiled) |

## Pinning

- **Upstream:** iPXE `v2.0.0`, commit `12798ec29aa8a64d8675c4378b99f5fe28447afb`
  (set in `compile-ipxe.sh`). The build aborts if the clone's HEAD differs —
  this pinned commit is the supply-chain integrity anchor.
- **Config:** only `#define DOWNLOAD_PROTO_HTTPS` is added; ECDSA, ECDHE, and
  the P-256/P-384 curves the Google Trust Services chain needs are already on
  by default in iPXE's `config/crypto.h`.
- **Trust:** root CAs in `certs/` (see `certs/README.md`).

## Reproducibility

iPXE stamps build metadata, so the binaries are **not** bit-for-bit
reproducible — their sha256s are recorded in `../bootstrap/ipxe-binaries.sha256`
as a reference, not enforced. Integrity is enforced by the pinned commit.

## Building locally

```sh
./build.sh    # → build/dist/ + refreshes the sha256 record
```

`build.sh` shells out to `docker build -o dist build`. The same
`compile-ipxe.sh` runs inside `bootstrap/Dockerfile`'s builder stage, so the
container ships exactly these binaries.
