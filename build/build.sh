#!/usr/bin/env bash
# Build the custom iPXE binaries locally and record their sha256s.
# Output lands in build/dist/ (gitignored); the manifest is written to
# bootstrap/ipxe-binaries.sha256 as a reference record.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="${SCRIPT_DIR}/dist"
MANIFEST="${SCRIPT_DIR}/../bootstrap/ipxe-binaries.sha256"

rm -rf "${DIST_DIR}"
mkdir -p "${DIST_DIR}"

echo "==> Building iPXE binaries via docker"
docker build --pull -o "${DIST_DIR}" "${SCRIPT_DIR}"

echo "==> dist/"
ls -lh "${DIST_DIR}"

echo "==> Writing reference manifest to ${MANIFEST}"
{
  echo "# Reference sha256s of the custom iPXE binaries. iPXE stamps build"
  echo "# metadata, so these are NOT bit-reproducible — supply-chain integrity"
  echo "# is enforced by the pinned upstream commit in build/compile-ipxe.sh,"
  echo "# not by these hashes. Regenerate with build/build.sh."
  cd "${DIST_DIR}"
  sha256sum undionly.kpxe ipxe.pxe ipxe.efi ipxe-arm64.efi
} > "${MANIFEST}"

cat "${MANIFEST}"
