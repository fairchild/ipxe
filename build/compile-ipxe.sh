#!/bin/sh
# Compile custom iPXE binaries with an embedded chain script and pinned
# trusted-root fingerprints. Shared by build/Dockerfile (standalone, exports
# to build/dist/) and bootstrap/Dockerfile (builder stage feeding the runtime
# image) so the compile logic lives in exactly one place.
#
# Supply-chain integrity comes from pinning IPXE_COMMIT and refusing to build
# if the clone's HEAD does not match. The binaries themselves are NOT
# bit-reproducible (iPXE stamps build metadata), so their sha256s are recorded
# as a reference, never gated.
set -eu

: "${IPXE_REF:=v2.0.0}"
: "${IPXE_COMMIT:=12798ec29aa8a64d8675c4378b99f5fe28447afb}"

INPUT_DIR="${INPUT_DIR:-$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)}"
SRC_DIR="${SRC_DIR:-/opt/ipxe}"
OUTPUT_DIR="${OUTPUT_DIR:-/out}"

mkdir -p "${OUTPUT_DIR}"

echo "==> Cloning iPXE ${IPXE_REF}"
git clone --depth 1 --branch "${IPXE_REF}" \
  https://github.com/ipxe/ipxe.git "${SRC_DIR}"

HEAD="$(git -C "${SRC_DIR}" rev-parse HEAD)"
if [ "${HEAD}" != "${IPXE_COMMIT}" ]; then
  echo "FATAL: iPXE HEAD ${HEAD} != pinned ${IPXE_COMMIT}" >&2
  exit 1
fi
echo "==> Verified pinned commit ${HEAD}"

# Embedded boot script: retry DHCP, then chain to the filename supplied by the
# managed proxy. Endpoint and credential policy stay runtime configuration.
cp "${INPUT_DIR}/embed.ipxe.template" "${SRC_DIR}/src/embed.ipxe"
echo "==> Embedded script:"
sed 's/^/    /' "${SRC_DIR}/src/embed.ipxe"

# Enable HTTPS (pulls in TLS + the ECDSA/ECDHE/P-256/P-384 crypto that the
# Google Trust Services chain needs — all on by default in iPXE's crypto.h).
# NTP_CMD is required on RTC-less boards (Raspberry Pi): without a clock sync
# the OCSP validity window can never bracket the firmware's bogus date and
# every TLS handshake dies with "Stale (or premature) OCSP response" (022fe4).
# Everything else stays at upstream defaults to keep the feature set minimal.
{
  echo "#define DOWNLOAD_PROTO_HTTPS"
  echo "#define NTP_CMD"
} > "${SRC_DIR}/src/config/local/general.h"

# Trusted root fingerprints. iPXE bakes in the SHA256 fingerprint of each cert
# and trusts a chain the moment it reaches a *presented* cert whose fingerprint
# matches (x509_check_root just fingerprint-matches — the anchor need not be
# self-signed). So the fingerprint must be of a cert the server actually sends.
# ipxe.cloudcompute.com (Cloudflare) presents leaf -> WE1 -> GTS Root R4 where
# that R4 is the GlobalSign-*cross-signed* R4, NOT the self-signed root — a
# different DER, hence a different fingerprint. Trusting only the self-signed
# R4 fails with "Untrusted root certificate". See certs/README.md.
#   gtsr4-globalsign - load-bearing: the cross-signed R4 actually presented
#   gtsr4            - hedge if the CDN switches to presenting the self-signed root
#   gtsr1            - hedge for a GTS RSA (WR-series) reissue
#   isrgrootx1       - hedge if the CDN ever switches to Let's Encrypt
TRUST="${INPUT_DIR}/certs/gtsr4-globalsign.pem,${INPUT_DIR}/certs/gtsr4.pem,${INPUT_DIR}/certs/gtsr1.pem,${INPUT_DIR}/certs/isrgrootx1.pem"

cd "${SRC_DIR}/src"
JOBS="$(nproc 2>/dev/null || echo 2)"

# The builder runs as linux/amd64 (pinned in the Dockerfiles) regardless of the
# target image's platform, so each iPXE target picks its toolchain explicitly:
#   BIOS i386      -> i686-linux-gnu cross (avoids the gcc-multilib/cross clash)
#   UEFI x86_64    -> native amd64 gcc
#   UEFI arm64     -> aarch64-linux-gnu cross

# BIOS x86: undionly.kpxe (firmware UNDI driver) + ipxe.pxe (iPXE's own NIC
# drivers, the fallback when a NIC's UNDI stack is broken).
echo "==> Building BIOS undionly.kpxe"
make -j"${JOBS}" CROSS_COMPILE=i686-linux-gnu- \
  bin/undionly.kpxe   EMBED=embed.ipxe TRUST="${TRUST}"
echo "==> Building BIOS ipxe.pxe"
make -j"${JOBS}" CROSS_COMPILE=i686-linux-gnu- \
  bin/ipxe.pxe        EMBED=embed.ipxe TRUST="${TRUST}"
echo "==> Building UEFI x86_64 ipxe.efi"
make -j"${JOBS}" bin-x86_64-efi/ipxe.efi EMBED=embed.ipxe TRUST="${TRUST}"
echo "==> Building UEFI arm64 ipxe.efi (cross-compile)"
make -j"${JOBS}" CROSS_COMPILE=aarch64-linux-gnu- \
  bin-arm64-efi/ipxe.efi EMBED=embed.ipxe TRUST="${TRUST}"

cp bin/undionly.kpxe        "${OUTPUT_DIR}/undionly.kpxe"
cp bin/ipxe.pxe            "${OUTPUT_DIR}/ipxe.pxe"
cp bin-x86_64-efi/ipxe.efi "${OUTPUT_DIR}/ipxe.efi"
cp bin-arm64-efi/ipxe.efi  "${OUTPUT_DIR}/ipxe-arm64.efi"

echo "==> Built binaries (sha256, informational — not reproducible):"
cd "${OUTPUT_DIR}"
sha256sum undionly.kpxe ipxe.pxe ipxe.efi ipxe-arm64.efi
