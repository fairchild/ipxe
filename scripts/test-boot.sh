#!/usr/bin/env bash
# End-to-end PXE boot test. Builds the production bootstrap image, builds the
# lab image on top of it, then boots QEMU guests through the full chain and
# prints a per-guest PASS/FAIL table. Nonzero exit on any failure.
#
#   scripts/test-boot.sh                 # local mode (deterministic), all guests
#   MODE=live scripts/test-boot.sh       # chain to the real Worker over HTTPS
#   GUESTS="bios uefi" scripts/test-boot.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

MODE="${MODE:-local}"
GUESTS="${GUESTS:-bios uefi arm64}"
GUEST_TIMEOUT="${GUEST_TIMEOUT:-300}"
IPXE_SERVER_URL="${IPXE_SERVER_URL:-https://ipxe.cloudcompute.com}"
BOOTSTRAP_IMAGE="${BOOTSTRAP_IMAGE:-ipxe-bootstrap:lab}"
LAB_IMAGE="${LAB_IMAGE:-ipxe-boot-lab:latest}"
OUT="$REPO/lab/out"

echo "==> building bootstrap image ($BOOTSTRAP_IMAGE)"
docker build -t "$BOOTSTRAP_IMAGE" "$REPO/bootstrap"

echo "==> building lab image ($LAB_IMAGE)"
docker build --build-arg BOOTSTRAP_IMAGE="$BOOTSTRAP_IMAGE" -t "$LAB_IMAGE" "$REPO/lab"

rm -rf "$OUT"; mkdir -p "$OUT"

echo "==> running lab (mode=$MODE guests='$GUESTS')"
set +e
docker run --rm --privileged \
  -e MODE="$MODE" \
  -e GUESTS="$GUESTS" \
  -e GUEST_TIMEOUT="$GUEST_TIMEOUT" \
  -e IPXE_SERVER_URL="$IPXE_SERVER_URL" \
  -v "$OUT:/lab/out" \
  "$LAB_IMAGE"
rc=$?
set -e

echo
echo "logs under $OUT/"
exit $rc
