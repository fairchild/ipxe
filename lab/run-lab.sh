#!/usr/bin/env bash
# QEMU boot lab orchestrator — runs INSIDE the privileged lab container.
#
# Topology (all inside this container's network namespace):
#
#   qemu guest(s) --tap--> br0 (10.77.0.1/24) <--- authoritative dnsmasq (leases)
#                                              <--- proxy dnsmasq (boot info)
#                            br0 --NAT--> eth0 --> internet (live HTTPS chain)
#                            10.77.0.1:8080 python http (local chain stub)
#
# The proxy runs the PRODUCTION config (bootstrap image, COPY --from), so the lab
# exercises the real thing. The authoritative server stands in for the existing
# LAN/home-router DHCP and deliberately serves NO boot options.
#
# What each guest proves, and why the boot methods differ:
#   - BIOS x86  : SeaBIOS network boot via qemu's iPXE option ROM. SeaBIOS honors
#                 -boot n reliably. iPXE's console is VGA-only here, so the chain
#                 is confirmed from the HTTP stub's access log, not serial.
#   - UEFI x86-64 / arm64 : Debian's OVMF/AAVMF ship no UEFI network stack, so the
#                 stock ipxe.efi / ipxe-arm64.efi is booted from a virtual ESP.
#                 Their EFI console maps to serial, so the full chain is visible.
#
# In every case qemu's firmware/loader is already iPXE and sends user-class "iPXE"
# on its first DHCP, so the stage-1 "dumb firmware TFTP-fetches stock iPXE" step
# is asserted separately (dhcp-archmap.py + TFTP sha256), not via a guest boot.
#
# Modes:
#   local (default) — chain to a plain-HTTP stub served from the container.
#                     Deterministic; the CI path.
#   live            — chain to ${IPXE_SERVER_URL} (the real Worker over HTTPS).
set -uo pipefail

MODE="${MODE:-local}"
GUESTS="${GUESTS:-bios uefi arm64}"
GUEST_TIMEOUT="${GUEST_TIMEOUT:-300}"
OUT=/lab/out
BR=br0
BR_IP=10.77.0.1
SUBNET=10.77.0.0

mkdir -p "$OUT"; : > "$OUT/lab.log"
log() { echo "[lab] $*" | tee -a "$OUT/lab.log"; }
die() { log "FATAL: $*"; exit 1; }

find_first() { for f in "$@"; do [ -f "$f" ] && { echo "$f"; return 0; }; done; return 1; }
OVMF_CODE="$(find_first /usr/share/OVMF/OVMF_CODE_4M.fd /usr/share/OVMF/OVMF_CODE.fd)" || true
OVMF_VARS="$(find_first /usr/share/OVMF/OVMF_VARS_4M.fd /usr/share/OVMF/OVMF_VARS.fd)" || true
AAVMF_CODE="$(find_first /usr/share/AAVMF/AAVMF_CODE.fd /usr/share/qemu-efi-aarch64/QEMU_EFI.fd)" || true
AAVMF_VARS="$(find_first /usr/share/AAVMF/AAVMF_VARS.fd /usr/share/qemu-efi-aarch64/QEMU_VARS.fd)" || true

# ---------------------------------------------------------------------------
# Network + servers
# ---------------------------------------------------------------------------
setup_network() {
  log "bridge $BR ($BR_IP/24)"
  [ -e /dev/net/tun ] || { mkdir -p /dev/net; mknod /dev/net/tun c 10 200; }
  ip link add name "$BR" type bridge
  ip addr add "$BR_IP/24" dev "$BR"
  ip link set "$BR" up
  local uplink; uplink="$(ip route show default | awk '{print $5; exit}')"
  echo 1 > /proc/sys/net/ipv4/ip_forward
  if [ -n "${uplink:-}" ]; then
    iptables -t nat -A POSTROUTING -s "$SUBNET/24" -o "$uplink" -j MASQUERADE 2>/dev/null || true
    iptables -A FORWARD -i "$BR" -o "$uplink" -j ACCEPT 2>/dev/null || true
    iptables -A FORWARD -i "$uplink" -o "$BR" -m state --state RELATED,ESTABLISHED -j ACCEPT 2>/dev/null || true
    log "NAT $SUBNET/24 -> $uplink"
  else
    log "no default route; live egress unavailable (local mode unaffected)"
  fi
}

start_servers() {
  envsubst < /etc/dnsmasq-auth.conf.template > "$OUT/dnsmasq-auth.conf"
  log "authoritative dnsmasq (fake router: leases only)"
  dnsmasq --conf-file="$OUT/dnsmasq-auth.conf" --pid-file="$OUT/auth.pid" \
    || die "authoritative dnsmasq failed to start"

  export DHCP_RANGE="$SUBNET"
  envsubst < /etc/dnsmasq.conf.template > "$OUT/dnsmasq-proxy.conf"
  cat >> "$OUT/dnsmasq-proxy.conf" <<EOF

# --- lab-only additions (pin to br0, separate log; no boot-semantics change) ---
interface=$BR
bind-interfaces
except-interface=lo
log-facility=$OUT/proxy.log
EOF
  log "proxy dnsmasq (production config; IPXE_SERVER_URL=$IPXE_SERVER_URL)"
  dnsmasq --conf-file="$OUT/dnsmasq-proxy.conf" --pid-file="$OUT/proxy.pid" \
    || die "proxy dnsmasq failed to start (config rejected)"

  if [ "$MODE" = "local" ]; then
    log "http chain stub on $BR_IP:8080"
    ( cd /lab/www && python3 -m http.server 8080 --bind "$BR_IP" ) > "$OUT/http.log" 2>&1 &
    echo $! > "$OUT/http.pid"
  fi
}

# ---------------------------------------------------------------------------
# Assertions
# ---------------------------------------------------------------------------
declare -A RESULT
PASS_N=0; FAIL_N=0
check() {                       # check <name> <logfile> <pattern>
  local name="$1" file="$2" pat="$3"
  if grep -qaE "$pat" "$file" 2>/dev/null; then
    printf '    %-38s PASS\n' "$name"; PASS_N=$((PASS_N+1)); return 0
  fi
  printf '    %-38s FAIL  (/%s/ in %s)\n' "$name" "$pat" "$(basename "$file")"; FAIL_N=$((FAIL_N+1)); return 1
}
note_pass() { printf '    %-38s PASS\n' "$1"; PASS_N=$((PASS_N+1)); }
note_fail() { printf '    %-38s FAIL\n' "$1"; FAIL_N=$((FAIL_N+1)); }

http_hits() { local n; n="$(grep -ca 'GET /boot.ipxe' "$OUT/http.log" 2>/dev/null || true)"; echo "${n:-0}"; }

# ---------------------------------------------------------------------------
# Proxy contract (arch->binary handoff qemu can't exercise directly)
# ---------------------------------------------------------------------------
verify_tftp() {
  local ok=0 f
  echo "  proxy contract — TFTP integrity:"
  for f in undionly.kpxe ipxe.efi ipxe-arm64.efi; do
    if curl -s --max-time 15 "tftp://$BR_IP/$f" -o "$OUT/tftp-$f" 2>/dev/null \
       && [ -s "$OUT/tftp-$f" ] \
       && [ "$(sha256sum < "$OUT/tftp-$f" | cut -d' ' -f1)" = "$(sha256sum < /tftpboot/$f | cut -d' ' -f1)" ]; then
      note_pass "TFTP serves $f (sha256 ok)"
    else
      note_fail "TFTP serves $f (sha256 ok)"; ok=1
    fi
  done
  return $ok
}

verify_archmap() {
  local ok=0
  echo "  proxy contract — dumb-firmware boot offer per arch:"
  if python3 /usr/local/bin/dhcp-archmap.py; then PASS_N=$((PASS_N+1)); else FAIL_N=$((FAIL_N+1)); ok=1; fi
  # Confirm the proxy assigned the right arch tag for each client-arch the probe
  # sent (MAC suffix encodes the arch). a9 (UEFI arch 9) is covered by no guest.
  echo "  proxy contract — arch -> tag (from proxy log):"
  grep -qaA1 '77:a0 proxy' "$OUT/proxy.log" && grep -aA1 '77:a0 proxy' "$OUT/proxy.log" | grep -qa 'tags: bios'  && note_pass "arch 0  -> tag bios"  || { note_fail "arch 0  -> tag bios";  ok=1; }
  grep -qaA1 '77:a7 proxy' "$OUT/proxy.log" && grep -aA1 '77:a7 proxy' "$OUT/proxy.log" | grep -qa 'tags: efi64' && note_pass "arch 7  -> tag efi64" || { note_fail "arch 7  -> tag efi64"; ok=1; }
  grep -qaA1 '77:a9 proxy' "$OUT/proxy.log" && grep -aA1 '77:a9 proxy' "$OUT/proxy.log" | grep -qa 'tags: efi64' && note_pass "arch 9  -> tag efi64" || { note_fail "arch 9  -> tag efi64"; ok=1; }
  grep -qaA1 '77:ab proxy' "$OUT/proxy.log" && grep -aA1 '77:ab proxy' "$OUT/proxy.log" | grep -qa 'tags: arm64' && note_pass "arch 11 -> tag arm64" || { note_fail "arch 11 -> tag arm64"; ok=1; }
  return $ok
}

# ---------------------------------------------------------------------------
# Guests
# ---------------------------------------------------------------------------
make_tap() { ip tuntap add dev "$1" mode tap; ip link set "$1" master "$BR"; ip link set "$1" up; }
del_tap()  { ip link del "$1" 2>/dev/null || true; }

# run_qemu <serial_log> <marker> -- <qemu...>  (kills on marker or timeout)
run_qemu() {
  local slog="$1" marker="$2"; shift 2; [ "$1" = "--" ] && shift
  ( "$@" ) > "$OUT/qemu-$$.log" 2>&1 &
  local qpid=$! deadline=$(( $(date +%s) + GUEST_TIMEOUT ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    kill -0 "$qpid" 2>/dev/null || break
    [ -n "$marker" ] && grep -qa "$marker" "$slog" 2>/dev/null && break
    sleep 2
  done
  kill "$qpid" 2>/dev/null; wait "$qpid" 2>/dev/null
}

serial_chain_pat() {
  if [ "$MODE" = "local" ]; then echo 'LAB_CHAIN_OK'
  else echo 'boot\.ipxe\.\.\. ok|Operating Systems|Debian 13'; fi
}

assert_common() {               # assert_common <guest> <archtag>
  local g="$1" tag="$2" ok=0
  check "authoritative leased an IP (DHCPACK)"  "$OUT/auth.log"  "DHCPACK" || ok=1
  check "proxy tagged arch ($tag)"              "$OUT/proxy.log" "tags:.*\\b$tag\\b" || ok=1
  check "proxy handed iPXE the boot URL"        "$OUT/proxy.log" "bootfile name: .*/boot\\.ipxe" || ok=1
  return $ok
}

boot_bios() {
  local slog="$OUT/serial-bios.log" tap=tapbios mac=52:54:00:00:77:01
  : > "$slog"; local before; before="$(http_hits)"
  make_tap "$tap"; log "boot BIOS x86 (SeaBIOS network boot)"
  run_qemu "$slog" "" -- \
    qemu-system-x86_64 -machine pc -m 512 -boot n \
    -netdev tap,id=net0,ifname="$tap",script=no,downscript=no \
    -device e1000,netdev=net0,mac="$mac" \
    -display none -serial "file:$slog" -monitor none -no-reboot
  del_tap "$tap"
  echo "  assertions [bios]:"
  local ok=0; assert_common bios bios || ok=1
  if [ "$MODE" = "local" ]; then
    if [ "$(http_hits)" -gt "$before" ]; then note_pass "chain reached HTTP stub (access log)"; else note_fail "chain reached HTTP stub (access log)"; ok=1; fi
  else
    check "iPXE resolved the Worker host (DNS)" "$OUT/auth.log" "query.*cloudcompute\\.com" || ok=1
  fi
  [ $ok -eq 0 ] && RESULT[bios]=PASS || RESULT[bios]=FAIL
}

boot_efi_guest() {              # boot_efi_guest <guest> <archtag> <bootname> <code> <vars> <qemu-bin> <machine> <cpu>
  local g="$1" tag="$2" bootname="$3" code="$4" vars="$5" qbin="$6" mach="$7" cpu="$8"
  local slog="$OUT/serial-$g.log" tap="tap$g" mac="52:54:00:00:77:0${9}"
  [ -n "$code" ] && [ -n "$vars" ] || { log "$g firmware not found"; RESULT[$g]=SKIP; return; }
  local esp="/lab/esp-$g"; mkdir -p "$esp/EFI/BOOT"
  cp "/tftpboot/$bootname" "$esp/EFI/BOOT/${10}"
  cp "$vars" "$OUT/$g-vars.fd"; cp "$code" "$OUT/$g-code.fd"
  # arm64 "virt" pflash requires 64MiB images; x86 OVMF must keep its exact size.
  if [ "$mach" = "virt" ]; then
    truncate -s 64M "$OUT/$g-code.fd"; truncate -s 64M "$OUT/$g-vars.fd"
  fi
  : > "$slog"; local before; before="$(http_hits)"
  make_tap "$tap"; log "boot $g (stock $bootname from ESP)"
  run_qemu "$slog" "$(serial_chain_pat)" -- \
    "$qbin" -machine "$mach" ${cpu:+-cpu $cpu} -m 512 \
    -drive if=pflash,format=raw,unit=0,readonly=on,file="$OUT/$g-code.fd" \
    -drive if=pflash,format=raw,unit=1,file="$OUT/$g-vars.fd" \
    -drive file=fat:rw:"$esp",format=raw,if=virtio \
    -netdev tap,id=net0,ifname="$tap",script=no,downscript=no \
    -device virtio-net-pci,netdev=net0,mac="$mac",romfile= \
    -display none -serial "file:$slog" -monitor none -no-reboot
  del_tap "$tap"
  echo "  assertions [$g]:"
  local ok=0; assert_common "$g" "$tag" || ok=1
  check "iPXE banner on serial"        "$slog" "iPXE .* Open Source Network Boot" || ok=1
  check "iPXE fetched the boot script" "$slog" "boot\\.ipxe\\.\\.\\. ok" || ok=1
  check "chain reached target (serial)" "$slog" "$(serial_chain_pat)" || ok=1
  [ "$MODE" = "local" ] && { [ "$(http_hits)" -gt "$before" ] && note_pass "chain reached HTTP stub (access log)" || { note_fail "chain reached HTTP stub (access log)"; ok=1; }; }
  [ $ok -eq 0 ] && RESULT[$g]=PASS || RESULT[$g]=FAIL
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
[ "$MODE" = "local" ] && export IPXE_SERVER_URL="http://$BR_IP:8080"
: "${IPXE_SERVER_URL:=https://ipxe.cloudcompute.com}"
log "mode=$MODE guests='$GUESTS' timeout=${GUEST_TIMEOUT}s"

setup_network
start_servers
sleep 1

echo; echo "==================== PROXY CONTRACT ===================="
CONTRACT=PASS
verify_tftp    || CONTRACT=FAIL
verify_archmap || CONTRACT=FAIL

echo; echo "====================== GUEST BOOTS ====================="
for g in $GUESTS; do
  case "$g" in
    bios)  boot_bios ;;
    uefi)  boot_efi_guest uefi  efi64 ipxe.efi        "$OVMF_CODE"  "$OVMF_VARS"  qemu-system-x86_64 q35  ""          2 BOOTX64.EFI ;;
    arm64) boot_efi_guest arm64 arm64 ipxe-arm64.efi  "$AAVMF_CODE" "$AAVMF_VARS" qemu-system-aarch64 virt cortex-a57 3 BOOTAA64.EFI ;;
    *) log "unknown guest '$g'" ;;
  esac
done

echo; echo "================ BOOT LAB RESULTS (mode=$MODE) ================"
printf '  %-16s %s\n' "proxy-contract" "$CONTRACT"
fail=0; [ "$CONTRACT" = "FAIL" ] && fail=1
for g in $GUESTS; do
  r="${RESULT[$g]:-FAIL}"; printf '  %-16s %s\n' "guest:$g" "$r"
  [ "$r" = "FAIL" ] && fail=1
done
printf '  %-16s %d passed, %d failed\n' "checks" "$PASS_N" "$FAIL_N"
echo "=============================================================="
exit $fail
