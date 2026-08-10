#!/bin/sh
set -eu

: "${IPXE_SERVER_URL:?IPXE_SERVER_URL is required}"
: "${DHCP_RANGE:?DHCP_RANGE is required}"
: "${BOOTSTRAP_CLIENT_CIDR:?BOOTSTRAP_CLIENT_CIDR is required}"
: "${BOOTSTRAP_TOKEN:?BOOTSTRAP_TOKEN is required}"
: "${BOOTSTRAP_ALLOWED_MACS:?BOOTSTRAP_ALLOWED_MACS is required}"

if [ "${#BOOTSTRAP_TOKEN}" -lt 32 ]; then
  echo "FATAL: BOOTSTRAP_TOKEN must be at least 32 characters" >&2
  exit 1
fi
case "${BOOTSTRAP_TOKEN}" in
  *[!A-Za-z0-9_-]*)
    echo "FATAL: BOOTSTRAP_TOKEN must contain only URL-safe characters" >&2
    exit 1
    ;;
esac

envsubst "\$DHCP_RANGE" \
  < /etc/dnsmasq.conf.template > /etc/dnsmasq.conf

# dnsmasq must not advertise PXE to arbitrary machines on the LAN. Generate
# the managed-client tags at runtime so site-specific MACs never enter the
# image or repository, then ignore every client without that tag.
old_ifs=$IFS
IFS=,
for mac in $BOOTSTRAP_ALLOWED_MACS; do
  if ! printf '%s\n' "$mac" | grep -Eq '^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$'; then
    echo "FATAL: BOOTSTRAP_ALLOWED_MACS must be comma-separated MAC addresses" >&2
    exit 1
  fi
  printf 'dhcp-mac=set:managed,%s\n' "$mac" >> /etc/dnsmasq.conf
done
IFS=$old_ifs
printf 'dhcp-ignore=tag:!managed\n' >> /etc/dnsmasq.conf

# Invoked indirectly by the signal/exit trap.
# shellcheck disable=SC2329
cleanup() {
  kill "${proxy_pid:-}" "${dnsmasq_pid:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

/usr/local/bin/boot-proxy &
proxy_pid=$!
dnsmasq --no-daemon --log-queries &
dnsmasq_pid=$!

while kill -0 "$proxy_pid" 2>/dev/null && kill -0 "$dnsmasq_pid" 2>/dev/null; do
  sleep 1
done

echo "FATAL: bootstrap proxy or dnsmasq exited unexpectedly" >&2
exit 1
