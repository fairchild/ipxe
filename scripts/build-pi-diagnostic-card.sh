#!/usr/bin/env bash
set -euo pipefail

# Build a Raspberry Pi OS card that comes up headless with SSH already working.
#
# This is a diagnostic platform, not part of the boot chain. When a netboot fails
# before userspace there is nothing left to ask — no console on the rpi kernel
# (no EFI framebuffer), no beacons (they need the userspace we never reach), and
# no shell. A normal SD boot on the same hardware, same switch port, same DHCP
# server, gives back the ability to just run a command and look.
#
#   sudo ./build-pi-diagnostic-card.sh /dev/disk10 [--key ~/.ssh/id_ed25519.pub]
#
# Everything is staged on the FAT boot partition, because macOS cannot write the
# ext4 root: `ssh` to enable sshd, `userconf.txt` for the account, and a
# firstrun.sh invoked from cmdline.txt to install the authorized key — the same
# mechanism Raspberry Pi Imager uses for headless setup.

DISK="${1:?usage: build-pi-diagnostic-card.sh /dev/diskN [--key path.pub]}"
KEY_PATH="${HOME}/.ssh/id_ed25519.pub"
USERNAME="pi"
IMAGE="${IMAGE:-}"

shift || true
while [ $# -gt 0 ]; do
  case "$1" in
    --key) KEY_PATH="$2"; shift 2 ;;
    --user) USERNAME="$2"; shift 2 ;;
    --image) IMAGE="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

DISK="${DISK#/dev/}"
DISK="${DISK#r}"
RAW="/dev/r${DISK}"

[ -f "${KEY_PATH}" ] || { echo "no public key at ${KEY_PATH}" >&2; exit 1; }
if [ -z "${IMAGE}" ] || [ ! -f "${IMAGE}" ]; then
	echo "set IMAGE=/path/to/raspios.img.xz" >&2
	exit 1
fi

if diskutil info "${DISK}" | grep -q "Internal:.*Yes"; then
  echo "REFUSING: ${DISK} is an internal disk." >&2
  exit 1
fi

echo "==> Target"
diskutil info "${DISK}" | grep -E "Device / Media Name|Disk Size|Removable Media" || true
echo
echo "This ERASES ${RAW} completely."
printf "Type the disk identifier (%s) to confirm: " "${DISK}"
read -r confirm
[ "${confirm}" = "${DISK}" ] || { echo "aborted"; exit 1; }

echo "==> Unmounting"
diskutil unmountDisk "${DISK}"

echo "==> Writing image (this takes a few minutes; Ctrl-T for progress)"
xzcat "${IMAGE}" | dd of="${RAW}" bs=4m

echo "==> Waiting for the boot partition to mount"
diskutil unmountDisk "${DISK}" >/dev/null 2>&1 || true
diskutil mountDisk "${DISK}" >/dev/null
BOOT=""
for _ in $(seq 1 20); do
  for candidate in /Volumes/bootfs /Volumes/boot; do
    [ -d "${candidate}" ] && BOOT="${candidate}" && break 2
  done
  sleep 1
done
[ -n "${BOOT}" ] || { echo "boot partition did not mount" >&2; exit 1; }
echo "    ${BOOT}"

echo "==> Enabling ssh"
touch "${BOOT}/ssh"

PASSWORD="$(openssl rand -hex 12)"
HASH="$(openssl passwd -6 "${PASSWORD}")"
printf '%s:%s\n' "${USERNAME}" "${HASH}" > "${BOOT}/userconf.txt"

echo "==> Installing authorized key from ${KEY_PATH}"
PUBKEY="$(cat "${KEY_PATH}")"
cat > "${BOOT}/firstrun.sh" <<FIRSTRUN
#!/bin/bash
set +e
install -d -m 0700 -o ${USERNAME} -g ${USERNAME} /home/${USERNAME}/.ssh
echo '${PUBKEY}' > /home/${USERNAME}/.ssh/authorized_keys
chown ${USERNAME}:${USERNAME} /home/${USERNAME}/.ssh/authorized_keys
chmod 0600 /home/${USERNAME}/.ssh/authorized_keys
systemctl enable ssh
systemctl start ssh
rm -f /boot/firmware/firstrun.sh
sed -i 's# systemd.run=[^ ]*##g; s# systemd.run_success_action=[^ ]*##g; s# systemd.unit=kernel-command-line.target##g' /boot/firmware/cmdline.txt
exit 0
FIRSTRUN
chmod 0755 "${BOOT}/firstrun.sh"

CMDLINE="${BOOT}/cmdline.txt"
if ! grep -q "systemd.run=" "${CMDLINE}"; then
  # cmdline.txt must stay a single line; appending a newline makes the kernel
  # silently ignore everything after it.
  printf '%s systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target\n' \
    "$(tr -d '\n' < "${CMDLINE}")" > "${CMDLINE}.new"
  mv "${CMDLINE}.new" "${CMDLINE}"
fi

echo "==> Recording credentials"
ENVFILE="${HOME}/.config/ipxe-lab.env"
touch "${ENVFILE}"; chmod 600 "${ENVFILE}"
grep -v '^PI_DIAG_' "${ENVFILE}" > "${ENVFILE}.tmp" 2>/dev/null || true
{ cat "${ENVFILE}.tmp" 2>/dev/null
  echo "PI_DIAG_USER=${USERNAME}"
  echo "PI_DIAG_PASSWORD=${PASSWORD}"
} > "${ENVFILE}"
rm -f "${ENVFILE}.tmp"
chmod 600 "${ENVFILE}"

sync
diskutil eject "${DISK}" >/dev/null

echo
echo "==> Done. Card ejected — safe to remove."
echo "    user:     ${USERNAME}"
echo "    password: recorded in ${ENVFILE} as PI_DIAG_PASSWORD"
echo "    key:      ${KEY_PATH}"
echo
echo "Swap it into the Pi and power on. First boot runs firstrun.sh and reboots"
echo "once, so give it ~2 minutes before expecting ssh."
