#!/usr/bin/env bash
set -euo pipefail

# build-display-bundle.sh — build the frame's display stack as one versioned,
# self-contained artifact this project controls.
#
# Why this exists. A frame is a RAM node: every boot starts from nothing and
# installs its display stack from scratch. Doing that from public APK mirrors
# and PyPI makes every boot in the fleet depend on two third parties being up,
# and makes "what is actually running" a function of the day it booted. This
# script resolves the stack once, records exactly what it resolved, and ships
# it as a tarball served from our own bucket. A boot then needs nothing but
# this project's own endpoint.
#
# What is inside. The Alpine packages carrying the Python runtime, Pillow and
# numpy (numpy is not optional — the framebuffer sink packs RGB565 with it),
# plus the Python wheels the inky driver needs and that Alpine does not carry:
# inky itself, gpiodevice, gpiod, smbus2 and spidev. The last three are C
# extensions, so the bundle is specific to one Alpine release, one CPU
# architecture and one CPython minor version, and says so in its name.
#
# Reproducibility. Package versions drift inside an Alpine release branch, so
# a rebuild months later is a different artifact unless it is pinned. The lock
# file records every resolved name=version with its SHA-256; a build that has
# a lock fetches those exact versions and verifies each hash, and fails rather
# than silently substituting. Refresh deliberately with --update-lock.
#
# Usage:
#   ./build-display-bundle.sh                 # build from the lock (verifying)
#   ./build-display-bundle.sh --update-lock   # re-resolve and rewrite the lock
#   ./build-display-bundle.sh --arch armv7    # a different target
#
# Output: dist/display-bundle-<alpine>-<arch>-<pyver>-<lockid>.tar.gz + .sha256

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="${SCRIPT_DIR}/dist"

ALPINE_VERSION="${ALPINE_VERSION:-3.22}"
ARCH="aarch64"
UPDATE_LOCK=0

while [ $# -gt 0 ]; do
	case "$1" in
	--update-lock) UPDATE_LOCK=1 ;;
	--arch) ARCH="$2"; shift ;;
	-h | --help) sed -n '3,30p' "$0"; exit 0 ;;
	*) echo "unknown argument: $1" >&2; exit 2 ;;
	esac
	shift
done

# Alpine's name for the CPU, Docker's name for the same CPU. They disagree,
# and the bundle is named for Alpine's because that is what `apk --print-arch`
# reports on the node that has to match it.
case "${ARCH}" in
aarch64) PLATFORM="linux/arm64" ;;
armv7) PLATFORM="linux/arm/v7" ;;
*) echo "ERROR: unsupported arch '${ARCH}' (aarch64|armv7)" >&2; exit 2 ;;
esac

LOCK="${SCRIPT_DIR}/display-bundle-${ARCH}.lock"

# The APK closure. python3 for the runtime, py3-pillow to decode and scale the
# photograph, py3-numpy for the RGB565 pack, ca-certificates because every
# fetch this node makes is HTTPS. Deliberately absent: py3-setuptools (inky
# guards its pkg_resources import and falls back to pkgutil), py3-pip (wheels
# are installed by unzipping, so no resolver runs on the node at all), and
# every -pyc/-tests/-doc subpackage.
APK_WANT="python3 py3-pillow py3-numpy ca-certificates"

# The wheels Alpine does not carry. Pinned as a set: inky's own dependency
# metadata names the rest, but resolving it on the node would need a network
# and a resolver, which is the dependency this bundle removes.
WHEEL_WANT="inky gpiodevice gpiod smbus2 spidev"

command -v docker >/dev/null 2>&1 || {
	echo "ERROR: docker is required (cross-arch build of musl C extensions)" >&2
	exit 1
}

mkdir -p "${DIST_DIR}"
WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

if [ "${UPDATE_LOCK}" -eq 0 ] && [ ! -f "${LOCK}" ]; then
	echo "ERROR: no lock at ${LOCK}" >&2
	echo "       run with --update-lock to resolve versions and create one." >&2
	exit 1
fi

# The two halves of the closure are pinned by different mechanisms, because
# their tools allow different things:
#
#   wheels — pinned by request. pip takes `name==version`, so a locked build
#            asks for exactly the versions the lock names.
#   apks   — pinned by verification. `apk fetch` accepts package names but not
#            `name=version` constraints (only `apk add` does), so the fetch
#            resolves normally and the lock diff below is what refuses the
#            result if anything moved.
#
# Both end at the same guarantee — these bytes or a failed build — but only
# the wheel half can re-fetch an older version once the branch has moved on.
# When an APK drifts, the recorded SHA-256 is what tells you, and the built
# artifact you already published is what you keep serving until you choose to
# move (see "Rollback" in discovery/README.md).
if [ "${UPDATE_LOCK}" -eq 0 ]; then
	WHEEL_PIN="$(awk '/^wheel /{printf "%s==%s ", $2, $3}' "${LOCK}")"
	echo "==> building from lock: ${LOCK}"
else
	WHEEL_PIN="${WHEEL_WANT}"
	echo "==> re-resolving versions (--update-lock)"
fi

echo "==> alpine ${ALPINE_VERSION}, arch ${ARCH} (${PLATFORM})"

# Everything below runs inside the target-architecture container: the C
# extensions must be compiled against the same musl and CPython the node runs,
# and "the same" is only guaranteed by using the same image.
docker run --rm --platform "${PLATFORM}" \
	-v "${WORK}:/out" \
	-e APK_WANT="${APK_WANT}" \
	-e WHEEL_PIN="${WHEEL_PIN}" \
	-e SOURCE_DATE_EPOCH=315532800 \
	-e PYTHONHASHSEED=0 \
	-e CFLAGS="-g0 -ffile-prefix-map=/tmp=/build" \
	-e LDFLAGS="-Wl,--build-id=none" \
	"alpine:${ALPINE_VERSION}" sh -euc '
	apk update >/dev/null

	mkdir -p /out/apks /out/wheels
	cd /out/apks
	# -R pulls the transitive closure. Names only: see the note above on why
	# the apk half is pinned by verifying what arrives rather than by asking
	# for a version.
	apk fetch -R ${APK_WANT} >/dev/null
	# Bytecode, tests and docs trade ~12MB of every boot for nothing the frame
	# uses. Removed after the fetch because apk resolves them as part of the
	# closure and refuses to fetch the parent without them.
	rm -f ./*-pyc-*.apk ./*-pycache-*.apk ./*-tests-*.apk ./*-doc-*.apk 2>/dev/null || true

	# Build deps for the C extensions. These stay in the container: the node
	# never compiles anything, which is the point of shipping wheels.
	apk add --no-cache python3 py3-pip gcc python3-dev musl-dev linux-headers \
		libgpiod-dev pkgconf >/dev/null

	cd /out/wheels
	# --no-deps because the closure is declared above, not discovered here: a
	# resolver would pull numpy and pillow as wheels and shadow the Alpine
	# builds we deliberately install as packages.
	#
	# spidev has no musllinux wheel upstream, so pip compiles it here. A wheel
	# is a zip, and a zip records mtimes: without SOURCE_DATE_EPOCH (set on the
	# container above, and clamped by the zip format to 1980) two builds of the
	# same source produce different bytes. The lock catches that — this is why
	# it is set, and the lock is how we found out it was needed.
	pip wheel --no-deps --wheel-dir /out/wheels ${WHEEL_PIN} >/dev/null

	python3 -c "import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}\")" > /out/pyver
	chmod -R a+rw /out
'

PYVER="$(cat "${WORK}/pyver")"
rm -f "${WORK}/pyver"

# The lock is the record of what this build actually resolved: one line per
# member, carrying the hash the node will re-verify. Sorted so a diff between
# two lock files is a version diff and not a filesystem-order diff.
NEW_LOCK="${WORK}/lock.new"
{
	echo "# display-bundle lock — generated by build-display-bundle.sh"
	echo "# Regenerate with: ./build-display-bundle.sh --update-lock --arch ${ARCH}"
	echo "alpine ${ALPINE_VERSION}"
	echo "arch ${ARCH}"
	echo "python ${PYVER}"
	for f in "${WORK}"/apks/*.apk; do
		base="$(basename "${f}")"
		# name-1.2.3-r0.apk -> name, 1.2.3-r0
		ver="$(echo "${base%.apk}" | sed -E 's/^.*-([0-9][^-]*-r[0-9]+)$/\1/')"
		name="$(echo "${base%.apk}" | sed -E 's/-[0-9][^-]*-r[0-9]+$//')"
		printf 'apk %s %s %s\n' "${name}" "${ver}" \
			"$( { shasum -a 256 "${f}" 2>/dev/null || sha256sum "${f}"; } | awk '{print $1}')"
	done | sort
	for f in "${WORK}"/wheels/*.whl; do
		base="$(basename "${f}")"
		name="$(echo "${base}" | cut -d- -f1)"
		ver="$(echo "${base}" | cut -d- -f2)"
		printf 'wheel %s %s %s %s\n' "${name}" "${ver}" \
			"$( { shasum -a 256 "${f}" 2>/dev/null || sha256sum "${f}"; } | awk '{print $1}')" \
			"${base}"
	done | sort
} > "${NEW_LOCK}"

if [ "${UPDATE_LOCK}" -eq 0 ]; then
	# Verify rather than trust: a pinned fetch that returned different bytes
	# under the same version is exactly the substitution this check exists for.
	if ! diff -u <(grep -vE '^#' "${LOCK}") <(grep -vE '^#' "${NEW_LOCK}") > "${WORK}/lock.diff"; then
		echo "ERROR: build does not match ${LOCK}:" >&2
		sed 's/^/    /' "${WORK}/lock.diff" >&2
		echo "    (rerun with --update-lock if this change is intended)" >&2
		exit 1
	fi
	echo "==> lock verified: $(grep -c '^apk ' "${LOCK}") apks, $(grep -c '^wheel ' "${LOCK}") wheels"
else
	cp "${NEW_LOCK}" "${LOCK}"
	echo "==> wrote ${LOCK}"
fi

# A short digest of the lock names the artifact, so a bundle filename is a
# claim about its contents that can be checked, and two different resolutions
# can never collide on one name.
LOCKID="$( { shasum -a 256 "${LOCK}" 2>/dev/null || sha256sum "${LOCK}"; } | awk '{print substr($1,1,12)}')"
NAME="display-bundle-alpine${ALPINE_VERSION}-${ARCH}-py${PYVER}-${LOCKID}"
OUT="${DIST_DIR}/${NAME}.tar.gz"

# The manifest travels inside the bundle so an unpacked copy is still
# self-describing — the lock lives in git, the manifest lives in the artifact.
python3 - "${WORK}" "${LOCK}" "${ALPINE_VERSION}" "${ARCH}" "${PYVER}" > "${WORK}/MANIFEST.json" <<'PY'
import hashlib, json, pathlib, sys

work, lock, alpine, arch, pyver = sys.argv[1:6]
root = pathlib.Path(work)


def members(kind: str) -> list[dict]:
    return sorted(
        (
            {
                "file": f"{kind}/{p.name}",
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "size": p.stat().st_size,
            }
            for p in (root / kind).iterdir()
        ),
        key=lambda m: m["file"],
    )


json.dump(
    {
        "schema": 1,
        "alpine": alpine,
        "arch": arch,
        "python": pyver,
        "lock_sha256": hashlib.sha256(pathlib.Path(lock).read_bytes()).hexdigest(),
        "apks": members("apks"),
        "wheels": members("wheels"),
    },
    sys.stdout,
    indent=2,
    sort_keys=True,
)
PY

TAR_OPTS=(--format=ustar)
if tar --version 2>/dev/null | grep -qi 'gnu tar'; then
	TAR_OPTS+=(--owner=0 --group=0 --numeric-owner --sort=name --mtime=@0)
else
	TAR_OPTS+=(--uid 0 --gid 0 --numeric-owner)
fi

rm -f "${OUT}"
( cd "${WORK}" && tar "${TAR_OPTS[@]}" -cf - MANIFEST.json apks wheels ) | gzip -n -9 > "${OUT}"

SHA="$( { shasum -a 256 "${OUT}" 2>/dev/null || sha256sum "${OUT}"; } | awk '{print $1}')"
echo "${SHA}  ${NAME}.tar.gz" > "${OUT}.sha256"

echo "==> Built ${OUT}"
echo "    size:   $(du -h "${OUT}" | cut -f1)"
echo "    sha256: ${SHA}"
echo
echo "Pin it in the overlay (discovery/overlay/etc/frame-bundle.conf):"
echo "  BUNDLE_NAME=${NAME}.tar.gz"
echo "  BUNDLE_SHA256=${SHA}"
echo
echo "Publish it:"
echo "  wrangler r2 object put ipxe-boot-assets/assets/discovery/${NAME}.tar.gz \\"
echo "    --file ${OUT} --content-type application/gzip"
