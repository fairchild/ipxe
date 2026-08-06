#!/usr/bin/env bash
set -euo pipefail

# Image a removable card to a compressed archive before it gets reused.
# Reads the raw device, so it captures every partition, the GPT, and any
# unallocated space — nothing is interpreted, nothing is skipped.
#
#   ./rescue-sdcard.sh disk10 [destination-dir]
#
# Press Ctrl-T during the run for a progress line (BSD dd reports on SIGINFO).

DISK="${1:?usage: rescue-sdcard.sh <diskN> [dest-dir]}"
DEST="${2:-$HOME/sdcard-rescue-$(date +%Y%m%d)}"
RAW="/dev/r${DISK}"
OUT="${DEST}/${DISK}.img.zst"

command -v zstd >/dev/null || { echo "zstd not found: brew install zstd"; exit 1; }

if ! diskutil info "${DISK}" >/dev/null 2>&1; then
  echo "No such disk: ${DISK}"; exit 1
fi

echo "==> Source"
diskutil info "${DISK}" | grep -E "Device / Media Name|Disk Size|Removable Media|Media Read-Only"

if diskutil info "${DISK}" | grep -q "Internal:.*Yes"; then
  echo "REFUSING: ${DISK} is an internal disk." >&2
  exit 1
fi

mkdir -p "${DEST}"
echo "==> Unmounting any mounted volumes (ignore 'was not mounted')"
diskutil unmountDisk "${DISK}" || true

echo "==> Imaging ${RAW} -> ${OUT}"
echo "    Ctrl-T for progress."
sudo dd if="${RAW}" bs=4m | zstd -T0 -3 -o "${OUT}"

echo "==> Recording layout and checksum"
diskutil list "${DISK}" > "${DEST}/${DISK}-partitions.txt"
zstd -dc "${OUT}" | shasum -a 256 | sed "s|-|${DISK}.img (uncompressed)|" > "${DEST}/${DISK}.sha256"

echo "==> Partition labels found in the image"
zstd -dc "${OUT}" | dd bs=512 skip=2 count=32 2>/dev/null | python3 -c '
import sys
d = sys.stdin.buffer.read()
for i in range(0, len(d), 128):
    e = d[i:i+128]
    if len(e) < 128 or e[:16] == b"\x00" * 16:
        continue
    name = e[56:128].decode("utf-16-le").rstrip("\x00")
    first = int.from_bytes(e[32:40], "little")
    last = int.from_bytes(e[40:48], "little")
    print(f"  {i//128+1:2d}  {name:28s} {(last-first+1)*512/1e6:9.1f} MB")
' || echo "  (no GPT names readable)"

echo ""
echo "==> Done. Archive: ${OUT}"
ls -lh "${OUT}"
echo "    Restore with: zstd -dc ${OUT} | sudo dd of=/dev/r${DISK} bs=4m"
