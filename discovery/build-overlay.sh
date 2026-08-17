#!/usr/bin/env bash
set -euo pipefail

# build-overlay.sh — package discovery/overlay into an Alpine apkovl tarball.
#
# An apkovl is just a gzipped tar of an overlay filesystem rooted at "/". The
# Alpine diskless/netboot initramfs downloads it (apkovl=<url> on the kernel
# cmdline) and extracts it over the tmpfs root before openrc starts. Our overlay
# ships:
#   etc/local.d/discovery.start        the inventory+register+poll script (+x)
#   usr/local/bin/discovery-*          clock, beacon, heartbeat, sshd (+x)
#   etc/init.d/discovery-*             openrc units for the above (+x)
#   etc/runlevels/*/                   symlinks enabling each of them
#   etc/network/interfaces             eth0 via dhcp
#   etc/hostname                       "discovery"
#   root/.ssh/authorized_keys          the operator key dropbear accepts (0600),
#                                      staged from AUTHORIZED_KEYS below
#
# Output: dist/discovery.apkovl.tar.gz — upload to R2 (see discovery/README.md).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OVERLAY_DIR="${SCRIPT_DIR}/overlay"
DIST_DIR="${SCRIPT_DIR}/dist"
OUT="${DIST_DIR}/discovery.apkovl.tar.gz"

START_SCRIPT="${OVERLAY_DIR}/etc/local.d/discovery.start"

# The key that opens a shell on every discovery node is a build input, copied in
# here rather than kept in the tree: rotating it is a cp, not an edit, and the
# built overlay is published to a bucket the world can read. Point AUTHORIZED_KEYS
# at any public key file; the default is discovery/authorized_keys.
AUTHORIZED_KEYS="${AUTHORIZED_KEYS:-${SCRIPT_DIR}/authorized_keys}"
SSH_STAGE="${OVERLAY_DIR}/root"

if [ ! -d "${OVERLAY_DIR}" ]; then
	echo "ERROR: overlay dir not found: ${OVERLAY_DIR}" >&2
	exit 1
fi

# Host debris is not source, and everything under overlay/ ships to every
# booting node. Two kinds have actually got in: a local py_compile once left a
# __pycache__, and a `sed -i -e` on macOS left `frame-render.py-e` beside the
# script it edited — byte-identical, harmless to run, and published for a
# week before anyone listed the tarball. Fail closed on both shapes: an
# accidental file in the overlay is a file nobody reviewed.
DEBRIS="$(find "${OVERLAY_DIR}" \( -type d -name __pycache__ \
	-o -type f \( -name '*.pyc' -o -name '*.pyo' \
		-o -name '*-e' -o -name '*.orig' -o -name '*.rej' -o -name '*.bak' -o -name '*~' \
		-o -name '.DS_Store' -o -name '*.swp' \) \) -print -quit)"
if [ -n "${DEBRIS}" ]; then
	echo "ERROR: build debris would enter overlay: ${DEBRIS}" >&2
	echo "       (editor backup, Python cache or Finder file — remove it; nothing in overlay/ is incidental)" >&2
	exit 1
fi

# Anything openrc or the local service executes must carry its exec bit inside
# the tar, or it is silently skipped at boot with no error anywhere. Git does
# preserve the bit, but a fresh checkout on a filesystem that does not (or an
# unzip, or a copy through a FAT volume) loses it — and the failure mode is a
# node that boots fine and simply never reports. Cheap to enforce here.
chmod 0755 "${START_SCRIPT}"
for exe in "${OVERLAY_DIR}"/usr/local/bin/* "${OVERLAY_DIR}"/etc/init.d/*; do
	# An unmatched glob comes through literally, and `set -e` would take the
	# whole build down with it on the last iteration.
	[ -f "${exe}" ] || continue
	chmod 0755 "${exe}"
done

# Restage from scratch every build, so a key that was removed from the input
# cannot survive in the artifact as a copy nobody remembers making.
rm -rf "${SSH_STAGE}"
if [ -s "${AUTHORIZED_KEYS}" ]; then
	mkdir -p "${SSH_STAGE}/.ssh"
	cp "${AUTHORIZED_KEYS}" "${SSH_STAGE}/.ssh/authorized_keys"
	# dropbear ignores an authorized_keys — or a .ssh, or a home — that anyone
	# but the owner can write, and the refusal surfaces only as a login that
	# keeps asking for a password on a node that has none.
	chmod 0700 "${SSH_STAGE}" "${SSH_STAGE}/.ssh"
	chmod 0600 "${SSH_STAGE}/.ssh/authorized_keys"
	echo "==> ssh: staged $(grep -c . "${AUTHORIZED_KEYS}") key(s) from ${AUTHORIZED_KEYS}"
else
	# Fail closed: no key in the overlay, and discovery-sshd declines to start a
	# daemon nobody could log into.
	echo "==> ssh: no key at ${AUTHORIZED_KEYS} — this overlay boots without sshd" >&2
fi

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
