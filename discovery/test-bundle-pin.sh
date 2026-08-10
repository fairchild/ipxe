#!/bin/sh
# Check that the bundle the overlay pins is the one the lock describes.
#
# The failure this catches: someone edits display-bundle-<arch>.lock (a version
# bump, a new dependency), rebuilds, and forgets to update frame-bundle.conf.
# Every test stays green, the overlay ships, and each frame boots, fetches a
# bundle name that no longer exists — or worse, one that does and whose hash no
# longer matches — and shows nothing. The node fails closed, which is right,
# but it fails on a wall instead of here.
#
# The bundle filename embeds the first 12 hex of the lock's own SHA-256, so the
# name is a checkable claim about which lock produced it. That is all this
# checks: verifying BUNDLE_SHA256 itself would mean rebuilding 27MB of musl
# wheels under emulation, which belongs in the builder, not in CI.

set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
CONF="${DIR}/overlay/etc/frame-bundle.conf"

fail() {
	echo "FAIL: $*" >&2
	exit 1
}

[ -f "${CONF}" ] || fail "no ${CONF}"

# shellcheck disable=SC1090
. "${CONF}"

[ -n "${BUNDLE_NAME:-}" ] || fail "BUNDLE_NAME is unset in ${CONF}"
[ -n "${BUNDLE_SHA256:-}" ] || fail "BUNDLE_SHA256 is unset in ${CONF}"

case "${BUNDLE_SHA256}" in
[0-9a-f][0-9a-f]*) ;;
*) fail "BUNDLE_SHA256 is not a lowercase hex digest: ${BUNDLE_SHA256}" ;;
esac
[ "${#BUNDLE_SHA256}" -eq 64 ] || fail "BUNDLE_SHA256 is not 64 hex chars"

# display-bundle-alpine3.22-aarch64-py3.12-<lockid>.tar.gz
ARCH="$(echo "${BUNDLE_NAME}" | sed -n 's/^display-bundle-alpine[^-]*-\([^-]*\)-py.*/\1/p')"
[ -n "${ARCH}" ] || fail "cannot read an architecture out of ${BUNDLE_NAME}"

LOCK="${DIR}/display-bundle-${ARCH}.lock"
[ -f "${LOCK}" ] || fail "${BUNDLE_NAME} names arch '${ARCH}' but there is no ${LOCK}"

WANT="$( { shasum -a 256 "${LOCK}" 2>/dev/null || sha256sum "${LOCK}"; } | awk '{print substr($1,1,12)}')"
GOT="$(echo "${BUNDLE_NAME}" | sed -n 's/.*-\([0-9a-f]\{12\}\)\.tar\.gz$/\1/p')"

[ -n "${GOT}" ] || fail "${BUNDLE_NAME} carries no lock id"
[ "${WANT}" = "${GOT}" ] || fail "$(
	printf '%s pins lock id %s, but %s hashes to %s.\n' \
		"$(basename "${CONF}")" "${GOT}" "$(basename "${LOCK}")" "${WANT}"
	printf '       Rebuild the bundle and update BUNDLE_NAME/BUNDLE_SHA256 together.'
)"

echo "PASS: ${BUNDLE_NAME} matches ${ARCH} lock (${WANT})"
