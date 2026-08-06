#!/usr/bin/env bash
set -euo pipefail

# Repair SSH access on an already-burned Raspberry Pi OS card, without reburning.
#
# Everything happens on the FAT boot partition, which macOS can write — the ext4
# root, where authorized_keys actually lives, it cannot. So the key is installed
# indirectly: a firstrun.sh dropped on the boot partition, invoked by systemd.run
# from cmdline.txt on the next boot. That is the same mechanism Raspberry Pi
# Imager uses, and unlike userconf.txt it re-runs on an already-provisioned card
# rather than only on first boot.
#
#   ./fix-pi-card-ssh.sh                      # report what the card is set up for
#   ./fix-pi-card-ssh.sh --apply              # install key for the existing user
#   ./fix-pi-card-ssh.sh --apply --user bob --key ~/.ssh/id_ed25519.pub
#
# Put the card in a reader first. Runs as your user; no sudo needed.

APPLY=0
USERNAME=""
KEY_PATH="${HOME}/.ssh/id_ed25519.pub"

while [ $# -gt 0 ]; do
  case "$1" in
    --apply) APPLY=1; shift ;;
    --user) USERNAME="$2"; shift 2 ;;
    --key) KEY_PATH="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

BOOT=""
for candidate in /Volumes/bootfs /Volumes/boot /Volumes/system-boot; do
  [ -d "${candidate}" ] && BOOT="${candidate}" && break
done
[ -n "${BOOT}" ] || {
  echo "No Raspberry Pi boot partition mounted." >&2
  echo "Insert the card; macOS mounts it as /Volumes/bootfs (or /Volumes/boot)." >&2
  exit 1
}
echo "==> Boot partition: ${BOOT}"

echo "==> What this card is currently set up for"
if [ -f "${BOOT}/userconf.txt" ]; then
  echo "    userconf.txt user: $(cut -d: -f1 "${BOOT}/userconf.txt")"
else
  echo "    userconf.txt:      absent (account was set some other way)"
fi
[ -f "${BOOT}/ssh" ] && echo "    ssh flag:          present" || echo "    ssh flag:          absent"
[ -f "${BOOT}/firstrun.sh" ] && echo "    firstrun.sh:       present (pending)" || echo "    firstrun.sh:       absent"
if grep -q "systemd.run=" "${BOOT}/cmdline.txt" 2>/dev/null; then
  echo "    cmdline.txt:       already chains a firstrun"
fi

if [ "${APPLY}" != 1 ]; then
  echo
  echo "Report only. Re-run with --apply to install a key."
  exit 0
fi

if [ -z "${USERNAME}" ]; then
  if [ -f "${BOOT}/userconf.txt" ]; then
    USERNAME="$(cut -d: -f1 "${BOOT}/userconf.txt")"
  else
    echo "Could not infer the username; pass --user." >&2
    exit 1
  fi
fi
[ -f "${KEY_PATH}" ] || { echo "no public key at ${KEY_PATH}" >&2; exit 1; }

echo "==> Installing $(basename "${KEY_PATH}") for user '${USERNAME}' on next boot"
PUBKEY="$(cat "${KEY_PATH}")"

# Creates the account if it is somehow missing, so this also recovers a card
# whose user was never provisioned. Every step is tolerant of already being done.
cat > "${BOOT}/firstrun.sh" <<FIRSTRUN
#!/bin/bash
set +e
id -u ${USERNAME} >/dev/null 2>&1 || useradd -m -s /bin/bash ${USERNAME}
install -d -m 0700 -o ${USERNAME} -g ${USERNAME} /home/${USERNAME}/.ssh
grep -qxF '${PUBKEY}' /home/${USERNAME}/.ssh/authorized_keys 2>/dev/null || \\
  echo '${PUBKEY}' >> /home/${USERNAME}/.ssh/authorized_keys
chown ${USERNAME}:${USERNAME} /home/${USERNAME}/.ssh/authorized_keys
chmod 0600 /home/${USERNAME}/.ssh/authorized_keys
systemctl enable ssh
systemctl start ssh
rm -f /boot/firmware/firstrun.sh
sed -i 's# systemd.run=[^ ]*##g; s# systemd.run_success_action=[^ ]*##g; s# systemd.unit=kernel-command-line.target##g' /boot/firmware/cmdline.txt
exit 0
FIRSTRUN
chmod 0755 "${BOOT}/firstrun.sh"
touch "${BOOT}/ssh"

CMDLINE="${BOOT}/cmdline.txt"
cp "${CMDLINE}" "${CMDLINE}.bak"
if ! grep -q "systemd.run=" "${CMDLINE}"; then
  # cmdline.txt must remain exactly one line — a stray newline makes the kernel
  # silently ignore everything after it, and the symptom is a Pi that boots
  # normally and does nothing you asked for.
  printf '%s systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target\n' \
    "$(tr -d '\n' < "${CMDLINE}")" > "${CMDLINE}.new"
  mv "${CMDLINE}.new" "${CMDLINE}"
fi

sync
echo
echo "==> Done. Eject, put the card back, power on."
echo "    First boot runs the fixup and reboots itself once — allow ~2 minutes."
echo "    Then: ssh ${USERNAME}@<pi-ip>"
