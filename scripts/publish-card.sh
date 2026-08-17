#!/usr/bin/env bash
set -euo pipefail

# publish-card.sh — put a built card image where the setup page can offer it.
#
#   ./publish-card.sh                       # newest dist/*-frame-card-*.img.gz
#   ./publish-card.sh dist/pi4-frame-card-v1-v1.38.img.gz
#   ./publish-card.sh --bucket ipxe-boot-assets-preview IMG
#   ./publish-card.sh --dry-run IMG
#
# The image and its .sha256 sidecar go to `cards/` in the boot-assets bucket,
# and the control plane lists whatever is there — no deploy on that side. Three
# things this script insists on, in order:
#
#   1. The sidecar exists and matches the image. The sidecar is what the setup
#      page shows an operator and what their browser verifies the download
#      against; publishing an image without it means offering a file nobody
#      can check, and the page declines to.
#   2. The image passes the builder's own structural verification
#      (build-pi-uefi-card.sh --verify-only): partition table, FAT32 at the
#      right offset, the file set, our iPXE and not a stock one, Devicetree
#      mode, no identity in the firmware image. Publishing is the last moment
#      this can be caught cheaply.
#   3. The upload is read back and hashed. Wrangler reporting success is the
#      upload's opinion of itself; the bytes on the far side are the evidence.
#
# Bundles and overlays follow the same rule elsewhere in this repository:
# verify a publish by comparing hashes, never by trusting the upload.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

BUCKET="ipxe-boot-assets"
PREFIX="cards"
DRY_RUN=""
IMG=""
WRANGLER="${WRANGLER:-wrangler}"

while [ $# -gt 0 ]; do
	case "$1" in
	--bucket) BUCKET="$2"; shift ;;
	--dry-run) DRY_RUN=1 ;;
	-h | --help) sed -n '3,30p' "$0"; exit 0 ;;
	-*) echo "unknown argument: $1" >&2; exit 2 ;;
	*) IMG="$1" ;;
	esac
	shift
done

sha256_of() { { shasum -a 256 "$1" 2>/dev/null || sha256sum "$1"; } | awk '{print $1}'; }

if [ -z "${IMG}" ]; then
	# Newest by name is newest by version: the builder names images
	# <family>-frame-card-v<N>-<firmware>.img.gz and keeps the superseded one
	# as *.previous.img.gz, which this glob leaves alone.
	IMG=""
	for candidate in "${REPO_DIR}"/dist/*-frame-card-v*.img.gz; do
		[ -f "${candidate}" ] || continue
		case "${candidate}" in *.previous.img.gz) continue ;; esac
		IMG="${candidate}"   # the glob is sorted; the last one standing is the newest name
	done
	[ -n "${IMG}" ] || { echo "ERROR: no card image in ${REPO_DIR}/dist — build one with scripts/build-pi-uefi-card.sh" >&2; exit 1; }
fi
[ -f "${IMG}" ] || { echo "ERROR: no such image: ${IMG}" >&2; exit 1; }
NAME="$(basename "${IMG}")"
case "${NAME}" in
*.img.gz) ;;
*) echo "ERROR: ${NAME} is not a .img.gz" >&2; exit 1 ;;
esac
SIDECAR="${IMG}.sha256"
[ -f "${SIDECAR}" ] || { echo "ERROR: no checksum sidecar at ${SIDECAR}; rebuild the image rather than writing one by hand" >&2; exit 1; }

# 1. The sidecar names this file and carries its hash.
WANT="$(awk 'NR==1{print $1}' "${SIDECAR}")"
NAMED="$(awk 'NR==1{print $2}' "${SIDECAR}" | sed 's/^\*//')"
GOT="$(sha256_of "${IMG}")"
if [ "${NAMED}" != "${NAME}" ]; then
	echo "ERROR: sidecar names '${NAMED}', not '${NAME}'" >&2; exit 1
fi
if [ "${WANT}" != "${GOT}" ]; then
	echo "ERROR: sidecar hash does not match the image" >&2
	echo "       sidecar ${WANT}" >&2
	echo "       image   ${GOT}" >&2
	exit 1
fi
echo "==> ${NAME}"
echo "    sha256 ${GOT} (sidecar agrees)"

# 2. The image is structurally what a card must be.
"${SCRIPT_DIR}/build-pi-uefi-card.sh" --verify-only "${IMG}"

if [ -n "${DRY_RUN}" ]; then
	echo "==> dry run: would upload to ${BUCKET}/${PREFIX}/${NAME} (+ .sha256)"
	exit 0
fi

command -v "${WRANGLER}" >/dev/null 2>&1 || { echo "ERROR: ${WRANGLER} not found (set WRANGLER=... or install it)" >&2; exit 1; }

# 3. Upload, then read back and compare. The sidecar goes second so a listing
# never shows a checksum for bytes that are not there yet.
echo "==> uploading to ${BUCKET}/${PREFIX}/"
"${WRANGLER}" r2 object put "${BUCKET}/${PREFIX}/${NAME}" --file "${IMG}" --content-type application/gzip >/dev/null
"${WRANGLER}" r2 object put "${BUCKET}/${PREFIX}/${NAME}.sha256" --file "${SIDECAR}" --content-type text/plain >/dev/null

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT
"${WRANGLER}" r2 object get "${BUCKET}/${PREFIX}/${NAME}" --file "${TMP}/${NAME}" >/dev/null
BACK="$(sha256_of "${TMP}/${NAME}")"
if [ "${BACK}" != "${GOT}" ]; then
	echo "ERROR: read-back hash differs from what was uploaded" >&2
	echo "       uploaded  ${GOT}" >&2
	echo "       read back ${BACK}" >&2
	exit 1
fi
echo "    read back and verified ${BACK}"
echo
echo "==> Published. The setup page lists it now:"
echo "    https://ipxe.cloudcompute.com/frame"
echo "    (an operator downloads it there, or with the token:"
echo "     curl -H \"Authorization: Bearer \$DASHBOARD_TOKEN\" -o ${NAME} https://ipxe.cloudcompute.com/api/cards/${NAME})"
