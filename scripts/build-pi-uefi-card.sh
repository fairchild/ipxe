#!/usr/bin/env bash
set -euo pipefail

# build-pi-uefi-card.sh — build the SD card image a frame Pi boots from.
#
# The card's whole job is to hand control to our iPXE binary and get out of the
# way. It carries no secret, no site configuration and no identity: everything
# that makes a particular machine a particular frame arrives later, over the
# authenticated boot chain. That is what makes one image cloneable across every
# Pi in the fleet, and why this script writes an *image file* rather than a
# disk — flashing is a separate, deliberate act.
#
#   ./build-pi-uefi-card.sh                 # -> dist/pi4-frame-card-<ver>.img.gz
#   ./build-pi-uefi-card.sh --verify-only IMG
#
# What goes on it, and where each piece comes from:
#
#   pftf/RPi4 v1.38        the UEFI firmware, unmodified, by pinned SHA-256.
#   our variable store     the UEFI settings, spliced into that firmware.
#   spi0-0cs.dtbo          raspberrypi/firmware at a pinned commit.
#   ipxe-arm64.efi         built by build/build.sh from pinned iPXE sources.
#
# The variable store deserves its own note. The Pi has no NVRAM chip, so UEFI
# settings live inside RPI_EFI.fd on this partition — and SystemTableMode=2 is
# not optional: in ACPI mode the arm64 kernel cannot find the genet PHY, so
# Ethernet never comes up, and gpiodevice refuses to run without a devicetree.
# A factory pftf image cannot be given that setting offline, because its
# variable store is empty and the editor only changes what already exists. So
# the store is seeded from a Pi that boots correctly and then scrubbed of that
# machine's identity (patch-rpi-uefi-vars.py scrub). We publish only the store;
# the firmware code region is upstream's bytes, unmodified, and this script
# checks that it still is.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${REPO_DIR}/dist"

CARD_VERSION="1"
IMAGE_MB="256"

# --- pinned inputs -----------------------------------------------------------
PFTF_VERSION="v1.38"
PFTF_URL="https://github.com/pftf/RPi4/releases/download/${PFTF_VERSION}/RPi4_UEFI_Firmware_${PFTF_VERSION}.zip"
PFTF_SHA256="f82404a1f52497308aaed69040baa663a2d8c5c4fd78edddc6132ee8ca62ae2b"

RPIFW_COMMIT="12eeaa12865869b07db760f4bbb7507ec6f1976c"
DTBO_URL="https://raw.githubusercontent.com/raspberrypi/firmware/${RPIFW_COMMIT}/boot/overlays/spi0-0cs.dtbo"
DTBO_SHA256="01340ab9d04daa52c867964f50aa0632991023f40fc581ff6452764857010619"

VARSTORE_NAME="rpi4-uefi-${PFTF_VERSION}-varstore.bin"
VARSTORE_SHA256="60f45041d447cf0d244bba1a05d7c3218eb6425ca8dddccd6fe3899e9a23d511"
BOOT_BASE="${IPXE_SERVER_URL:-https://ipxe.cloudcompute.com}"
VARSTORE_URL="${BOOT_BASE}/assets/discovery/${VARSTORE_NAME}"

IPXE_BINARY="${IPXE_BINARY:-${REPO_DIR}/build/dist/ipxe-arm64.efi}"

# A fixed timestamp and volume id, so two builds of the same inputs are the
# same bytes. FAT stores mtimes and a volume serial; both default to "now".
FIXED_DATE="198001010000.00"
VOLUME_ID="F4A3E100"

VERIFY_ONLY=""
while [ $# -gt 0 ]; do
	case "$1" in
	--verify-only) VERIFY_ONLY="$2"; shift ;;
	-h | --help) sed -n '3,32p' "$0"; exit 0 ;;
	*) echo "unknown argument: $1" >&2; exit 2 ;;
	esac
	shift
done

sha256_of() { { shasum -a 256 "$1" 2>/dev/null || sha256sum "$1"; } | awk '{print $1}'; }

# Is this one of ours? Our binaries carry the boot script compiled in; a stock
# iPXE does not, and booting one leaves a frame at an interactive prompt.
#
# Searched as raw bytes rather than through `strings | grep -q`: that pipeline
# lies under `set -o pipefail`, because grep exits at the first match, strings
# dies of SIGPIPE, and the pipeline reports failure for a binary that *did*
# match. It rejected every good build until it was replaced.
has_embedded_script() {
	python3 - "$1" <<'PY'
import sys
sys.exit(0 if b"embed.ipxe" in open(sys.argv[1], "rb").read() else 1)
PY
}

fetch_verified() { # url, dest, want_sha, label
	local url="$1" dest="$2" want="$3" label="$4" got
	if [ ! -f "${dest}" ]; then
		echo "==> fetching ${label}"
		curl -fsSL --retry 3 -o "${dest}.part" "${url}" || {
			echo "ERROR: could not fetch ${label} from ${url}" >&2
			rm -f "${dest}.part"
			exit 1
		}
		mv "${dest}.part" "${dest}"
	fi
	got="$(sha256_of "${dest}")"
	if [ "${got}" != "${want}" ]; then
		echo "ERROR: ${label} hash mismatch" >&2
		echo "       want ${want}" >&2
		echo "       got  ${got}" >&2
		# Discard it: a cached wrong file would make every later run "succeed".
		rm -f "${dest}"
		exit 1
	fi
	echo "    verified ${label} (${got})"
}

# --- verification ------------------------------------------------------------
# Written to read an image it did not build. Every check re-derives its answer
# from the bytes on disk rather than from a variable this script set earlier —
# a builder that verifies its own intentions proves nothing.
verify_image() {
	local img="$1" tmp
	tmp="$(mktemp -d)"
	echo "==> verifying ${img}"

	local plain="${img}"
	case "${img}" in
	*.gz) plain="${tmp}/card.img"; gzip -dc "${img}" > "${plain}" ;;
	esac

	# 1. Partition table, read straight out of the MBR.
	local part_info
	part_info="$(python3 - "${plain}" <<'PY'
import struct, sys
with open(sys.argv[1], "rb") as f:
    mbr = f.read(512)
if mbr[510:512] != b"\x55\xaa":
    sys.exit("no MBR boot signature (0x55AA) at offset 510")
entries = []
for i in range(4):
    e = mbr[446 + i * 16 : 462 + i * 16]
    status, ptype = e[0], e[4]
    lba, sectors = struct.unpack("<II", e[8:16])
    if ptype:
        entries.append((i, status, ptype, lba, sectors))
if len(entries) != 1:
    sys.exit(f"expected exactly 1 partition, found {len(entries)}")
i, status, ptype, lba, sectors = entries[0]
if ptype not in (0x0B, 0x0C):
    sys.exit(f"partition type 0x{ptype:02X} is not FAT32 (0x0B/0x0C)")
if status != 0x80:
    sys.exit(f"partition is not marked bootable (status 0x{status:02X})")
print(f"{lba} {sectors} {ptype:02X} {status:02X}")
PY
)" || { echo "FAIL: partition table"; rm -rf "${tmp}"; exit 1; }
	local lba sectors ptype status
	read -r lba sectors ptype status <<<"${part_info}"
	echo "    partition: type 0x${ptype}, bootable 0x${status}, start LBA ${lba}, ${sectors} sectors"

	# 2. A real FAT filesystem at exactly that offset — not merely somewhere.
	python3 - "${plain}" "${lba}" <<'PY' || { echo "FAIL: filesystem"; exit 1; }
import sys
plain, lba = sys.argv[1], int(sys.argv[2])
off = lba * 512
with open(plain, "rb") as f:
    f.seek(off)
    bs = f.read(512)
if bs[510:512] != b"\x55\xaa":
    sys.exit(f"no FAT boot sector signature at partition offset {off}")
if bs[82:87] != b"FAT32":
    sys.exit(f"filesystem at {off} is not FAT32 (type field {bs[82:87]!r})")
bps = int.from_bytes(bs[11:13], "little")
if bps != 512:
    sys.exit(f"unexpected bytes-per-sector {bps}")
print(f"    filesystem: FAT32 at byte offset {off}, {bps} B/sector")
PY

	# 3. The files, listed out of the filesystem itself.
	local offset=$((lba * 512))
	docker run --rm -v "${tmp}:/t" -v "$(cd "$(dirname "${plain}")" && pwd):/i:ro" \
		-e OFFSET="${offset}" -e IMG="/i/$(basename "${plain}")" \
		alpine:3.22 sh -euc '
		apk add --no-cache mtools >/dev/null
		printf "drive z: file=\"%s\" offset=%s\n" "$IMG" "$OFFSET" > /etc/mtools.conf
		mdir -/ -b z: > /t/listing.txt
	' >/dev/null 2>&1 || { echo "FAIL: could not read the filesystem"; rm -rf "${tmp}"; exit 1; }

	# mdir writes the drive it was given ("z:/x" or "::/x" depending on version),
	# so compare on the path and let the prefix be whatever it is.
	sed -E 's#^(::|[A-Za-z]:)##' "${tmp}/listing.txt" | tr -d '\r' > "${tmp}/paths.txt"

	local missing=0 f
	for f in /RPI_EFI.fd /config.txt /start4.elf /fixup4.dat \
		/bcm2711-rpi-4-b.dtb /overlays/spi0-0cs.dtbo /EFI/BOOT/BOOTAA64.EFI; do
		if grep -qxF "${f}" "${tmp}/paths.txt"; then
			echo "    present: ${f#/}"
		else
			echo "    MISSING: ${f#/}"
			missing=1
		fi
	done
	if [ "${missing}" -ne 0 ]; then
		echo "    --- what the filesystem actually contains ---"
		sed 's/^/      /' "${tmp}/paths.txt" | head -20
	fi
	# AppleDouble sidecars are what a macOS copy leaves behind; they are junk on
	# a FAT boot partition and a sign the image was assembled outside this script.
	if grep -qE '/\._' "${tmp}/listing.txt"; then
		echo "    FAIL: AppleDouble (._*) files on the boot partition"
		missing=1
	fi
	[ "${missing}" -eq 0 ] || { echo "FAIL: file set"; rm -rf "${tmp}"; exit 1; }

	# 4. The two files whose *contents* decide whether this card can work.
	docker run --rm -v "${tmp}:/t" -v "$(cd "$(dirname "${plain}")" && pwd):/i:ro" \
		-e OFFSET="${offset}" -e IMG="/i/$(basename "${plain}")" \
		alpine:3.22 sh -euc '
		apk add --no-cache mtools >/dev/null
		printf "drive z: file=\"%s\" offset=%s\n" "$IMG" "$OFFSET" > /etc/mtools.conf
		mcopy z:/RPI_EFI.fd /t/RPI_EFI.fd
		mcopy z:/EFI/BOOT/BOOTAA64.EFI /t/BOOTAA64.EFI
		mcopy z:/config.txt /t/config.txt
	' >/dev/null 2>&1

	# iPXE: ours, or nothing. A stock binary boots to a shell and waits for a
	# human, which on a wall-mounted frame is indistinguishable from dead.
	if ! has_embedded_script "${tmp}/BOOTAA64.EFI"; then
		echo "    FAIL: BOOTAA64.EFI has no embedded script — not our iPXE build"
		rm -rf "${tmp}"; exit 1
	fi
	echo "    iPXE: custom build (embedded script present), sha256 $(sha256_of "${tmp}/BOOTAA64.EFI")"

	python3 "${SCRIPT_DIR}/patch-rpi-uefi-vars.py" list "${tmp}/RPI_EFI.fd" \
		| grep -q "SystemTableMode.*Devicetree" || {
		echo "    FAIL: RPI_EFI.fd is not set to Devicetree mode"
		rm -rf "${tmp}"; exit 1
	}
	echo "    firmware: SystemTableMode=2 (Devicetree)"

	# No identity of any kind rode along in the firmware image.
	python3 - "${tmp}/RPI_EFI.fd" <<'PY' || { echo "FAIL: identity leak"; exit 1; }
import re, sys
blob = open(sys.argv[1], "rb").read()
hits = {m.group(0).decode("utf-16-le") for m in re.finditer(rb"(?:[0-9A-F]\x00){12}", blob)}
hits |= {b.hex().upper() for b in re.findall(rb"\xdc\xa6\x32...", blob)}
if hits:
    sys.exit(f"MAC-shaped identifiers present in RPI_EFI.fd: {sorted(hits)}")
print("    firmware: no MAC-shaped identifier present")
PY

	grep -q "^dtparam=spi=on" "${tmp}/config.txt" || { echo "    FAIL: SPI not enabled"; rm -rf "${tmp}"; exit 1; }
	grep -q "^dtparam=i2c_arm=on" "${tmp}/config.txt" || { echo "    FAIL: I2C not enabled"; rm -rf "${tmp}"; exit 1; }
	grep -q "^dtoverlay=spi0-0cs" "${tmp}/config.txt" || { echo "    FAIL: spi0-0cs overlay not selected"; rm -rf "${tmp}"; exit 1; }
	echo "    config.txt: SPI + I2C enabled, spi0-0cs selected"

	rm -rf "${tmp}"
	echo "==> verification passed"
}

if [ -n "${VERIFY_ONLY}" ]; then
	verify_image "${VERIFY_ONLY}"
	exit 0
fi

# --- build -------------------------------------------------------------------
command -v docker >/dev/null 2>&1 || { echo "ERROR: docker is required" >&2; exit 1; }

# Refuse early and loudly. Substituting a stock iPXE here would produce a card
# that boots to an iPXE shell and waits forever — the exact silent failure this
# whole chain is built to avoid.
if [ ! -f "${IPXE_BINARY}" ]; then
	cat >&2 <<EOF
ERROR: no custom iPXE binary at ${IPXE_BINARY}

  Build it first:  ./build/build.sh

  This card must carry the binary with our boot script and pinned trust roots
  embedded. A stock iPXE drops to a shell and waits for a human, so there is
  deliberately no fallback here.
EOF
	exit 1
fi
if ! has_embedded_script "${IPXE_BINARY}"; then
	echo "ERROR: ${IPXE_BINARY} has no embedded script — that is a stock build." >&2
	echo "       Rebuild with ./build/build.sh" >&2
	exit 1
fi

CACHE="${REPO_DIR}/build/cache"
mkdir -p "${CACHE}" "${DIST_DIR}"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

fetch_verified "${PFTF_URL}" "${CACHE}/RPi4_UEFI_Firmware_${PFTF_VERSION}.zip" \
	"${PFTF_SHA256}" "pftf/RPi4 ${PFTF_VERSION}"
fetch_verified "${DTBO_URL}" "${CACHE}/spi0-0cs.dtbo" \
	"${DTBO_SHA256}" "spi0-0cs.dtbo (raspberrypi/firmware ${RPIFW_COMMIT:0:12})"
# VARSTORE_FILE points the build at a local store instead of the published one.
# It is how the store is built and checked before it is published, and it takes
# the same hash check as the download — a local path is a convenience, not a
# reason to trust bytes less.
if [ -n "${VARSTORE_FILE:-}" ]; then
	echo "==> using local variable store ${VARSTORE_FILE}"
	got="$(sha256_of "${VARSTORE_FILE}")"
	if [ "${got}" != "${VARSTORE_SHA256}" ]; then
		echo "ERROR: local variable store hash mismatch" >&2
		echo "       want ${VARSTORE_SHA256}" >&2
		echo "       got  ${got}" >&2
		exit 1
	fi
	install -m 0644 "${VARSTORE_FILE}" "${CACHE}/${VARSTORE_NAME}"
	echo "    verified UEFI variable store (${got})"
else
	fetch_verified "${VARSTORE_URL}" "${CACHE}/${VARSTORE_NAME}" \
		"${VARSTORE_SHA256}" "UEFI variable store"
fi

STAGE="${WORK}/stage"
mkdir -p "${STAGE}"
( cd "${STAGE}" && unzip -q "${CACHE}/RPi4_UEFI_Firmware_${PFTF_VERSION}.zip" )

# Splice our settings into upstream's firmware, and check that the code region
# is still upstream's. This is the supply-chain claim the card rests on: we
# ship unmodified firmware plus a settings blob, never modified firmware.
python3 - "${STAGE}/RPI_EFI.fd" "${CACHE}/${VARSTORE_NAME}" "${SCRIPT_DIR}" <<'PY'
import importlib.util, sys
fd_path, store_path, script_dir = sys.argv[1:4]
spec = importlib.util.spec_from_file_location(
    "uefivars", f"{script_dir}/patch-rpi-uefi-vars.py"
)
uefivars = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uefivars)

pristine = open(fd_path, "rb").read()
store = open(store_path, "rb").read()
base, _, _ = uefivars.find_store(pristine)
if len(pristine) != base + len(store):
    sys.exit(
        f"variable store is {len(store)} bytes; this firmware expects "
        f"{len(pristine) - base}. The store and the pftf release must match."
    )
spliced = pristine[:base] + store
assert spliced[:base] == pristine[:base], "firmware code region changed"
open(fd_path, "wb").write(spliced)
active = [v for v in uefivars.parse(spliced) if v.state == uefivars.VAR_ADDED]
mode = next((v for v in active if v.name == "SystemTableMode"), None)
if mode is None or int.from_bytes(mode.value(spliced), "little") != 2:
    sys.exit("spliced firmware is not in Devicetree mode")
print(f"    firmware: upstream code + {len(active)} settings, Devicetree mode")
PY

install -m 0644 "${CACHE}/spi0-0cs.dtbo" "${STAGE}/overlays/spi0-0cs.dtbo"

# SPI for the panel, I2C for its EEPROM, and spi0-0cs so the kernel leaves
# GPIO8 alone — the inky library drives chip-select itself and refuses a pin
# the kernel has claimed.
cat >> "${STAGE}/config.txt" <<'EOF'

# Added by build-pi-uefi-card.sh — the Inky panel's bus requirements.
dtparam=spi=on
dtparam=i2c_arm=on
dtoverlay=spi0-0cs
EOF

mkdir -p "${STAGE}/EFI/BOOT"
install -m 0644 "${IPXE_BINARY}" "${STAGE}/EFI/BOOT/BOOTAA64.EFI"

# Nothing on this card is written by a person, so nothing on it should carry a
# person's clock. Fixed mtimes are also what makes two builds identical.
find "${STAGE}" -exec touch -t "${FIXED_DATE}" {} +

IMG="${WORK}/card.img"
# SOURCE_DATE_EPOCH is what makes two builds the same bytes. Fixed mtimes on
# the staged files are not enough: a FAT directory entry also records creation
# and last-access times, and mtools stamps those with the clock unless told
# otherwise. mkfs.vfat reads it too, for the same reason.
docker run --rm -v "${WORK}:/w" -e IMAGE_MB="${IMAGE_MB}" -e VOLUME_ID="${VOLUME_ID}" \
	-e SOURCE_DATE_EPOCH=315532800 \
	alpine:3.22 sh -euc '
	apk add --no-cache dosfstools mtools sfdisk >/dev/null

	# The partition is built as its own file and then placed at the offset, so
	# no loop device is needed and this runs unprivileged.
	PART_MB=$((IMAGE_MB - 1))
	dd if=/dev/zero of=/w/part.img bs=1M count="${PART_MB}" status=none
	# --invariant because the volume label is stored as a directory entry and
	# mkfs stamps it with the clock; -i alone fixes the serial but not that.
	mkfs.vfat -F 32 -n BOOT -i "${VOLUME_ID}" --invariant /w/part.img >/dev/null

	printf "drive z: file=\"/w/part.img\"\n" > /etc/mtools.conf
	cd /w/stage
	for entry in *; do
		if [ -d "${entry}" ]; then
			mcopy -s -Q -i /w/part.img "${entry}" ::/
		else
			mcopy -Q -i /w/part.img "${entry}" ::/
		fi
	done

	dd if=/dev/zero of=/w/card.img bs=1M count=1 status=none
	cat /w/part.img >> /w/card.img
	rm -f /w/part.img

	# One bootable FAT32 partition starting at 1MiB (LBA 2048). label-id is
	# pinned because sfdisk otherwise generates a random MBR disk signature,
	# which lands in bytes 440-443 and makes every build a different image.
	printf "label: dos\nlabel-id: 0xf4a3e100\nunit: sectors\n2048,,c,*\n" \
		| sfdisk -q /w/card.img >/dev/null
' >/dev/null 2>&1 || { echo "ERROR: image assembly failed" >&2; exit 1; }

verify_image "${IMG}"

OUT="${DIST_DIR}/pi4-frame-card-v${CARD_VERSION}-${PFTF_VERSION}.img.gz"
# Keep the previous artifact rather than overwriting it: the known-good card is
# the rollback, and it is only known-good until the next one is proven.
if [ -f "${OUT}" ]; then
	mv "${OUT}" "${OUT%.img.gz}.previous.img.gz"
	mv "${OUT}.sha256" "${OUT%.img.gz}.previous.img.gz.sha256" 2>/dev/null || true
	echo "==> kept previous artifact as $(basename "${OUT%.img.gz}.previous.img.gz")"
fi
gzip -n -9 -c "${IMG}" > "${OUT}"
SHA="$(sha256_of "${OUT}")"
echo "${SHA}  $(basename "${OUT}")" > "${OUT}.sha256"

echo
echo "==> Built ${OUT}"
echo "    size:   $(du -h "${OUT}" | cut -f1)"
echo "    sha256: ${SHA}"
echo
echo "Flash it (identify the disk first — this destroys everything on it):"
echo "  diskutil list"
echo "  gzip -dc ${OUT} | sudo dd of=/dev/rdiskN bs=4m"
