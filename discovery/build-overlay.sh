#!/usr/bin/env bash
set -euo pipefail

# build-overlay.sh — package discovery/overlay into an Alpine apkovl tarball.
#
# An apkovl is just a gzipped tar of an overlay filesystem rooted at "/". The
# Alpine diskless/netboot initramfs downloads it (apkovl=<url> on the kernel
# cmdline) and extracts it over the tmpfs root before openrc starts. Our overlay
# ships:
#   etc/local.d/discovery.start        the inventory+register+poll script (+x)
#   etc/runlevels/default/local        symlink enabling the local service
#   etc/runlevels/boot/networking      symlink enabling networking
#   etc/runlevels/boot/hostname        symlink enabling hostname
#   etc/network/interfaces             eth0 via dhcp
#   etc/hostname                       "discovery"
#
# Output: dist/discovery.apkovl.tar.gz — upload to R2 (see discovery/README.md).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OVERLAY_DIR="${SCRIPT_DIR}/overlay"
DIST_DIR="${SCRIPT_DIR}/dist"
OUT="${DIST_DIR}/discovery.apkovl.tar.gz"

START_SCRIPT="${OVERLAY_DIR}/etc/local.d/discovery.start"

if [ ! -d "${OVERLAY_DIR}" ]; then
	echo "ERROR: overlay dir not found: ${OVERLAY_DIR}" >&2
	exit 1
fi

# Anything openrc or the local service executes must carry its exec bit inside
# the tar, or it is silently skipped at boot with no error anywhere. Git does
# preserve the bit, but a fresh checkout on a filesystem that does not (or an
# unzip, or a copy through a FAT volume) loses it — and the failure mode is a
# node that boots fine and simply never reports. Cheap to enforce here.
chmod 0755 "${START_SCRIPT}"
chmod 0755 "${OVERLAY_DIR}/usr/local/bin/discovery-beacon"
for svc in "${OVERLAY_DIR}"/etc/init.d/*; do
	[ -f "${svc}" ] && chmod 0755 "${svc}"
done

mkdir -p "${DIST_DIR}"
rm -f "${OUT}"

# Tar from inside the overlay so paths are relative to "/". Preserve symlinks
# (no -h) — the runlevel entries must stay links to /etc/init.d/*. Force POSIX
# ustar with a stable owner so the artifact is reproducible across machines.
TAR_OPTS=(--format=ustar)
if tar --version 2>/dev/null | grep -qi 'gnu tar'; then
	TAR_OPTS+=(--owner=0 --group=0 --numeric-owner --sort=name --mtime=@0)
else
	# bsdtar (macOS): uid/gid override, no --sort/--mtime.
	TAR_OPTS+=(--uid 0 --gid 0 --numeric-owner)
fi

( cd "${OVERLAY_DIR}" && tar "${TAR_OPTS[@]}" -cf - . ) | gzip -n -9 > "${OUT}"

echo "==> Built ${OUT}"
echo "    contents:"
tar -tzvf "${OUT}" | sed 's/^/      /'
echo "    sha256: $( { shasum -a 256 "${OUT}" 2>/dev/null || sha256sum "${OUT}"; } | awk '{print $1}')"
echo
echo "Upload with:"
echo "  wrangler r2 object put ipxe-boot-assets/discovery/discovery.apkovl.tar.gz \\"
echo "    --file ${OUT} --content-type application/gzip"
