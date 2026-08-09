#!/bin/sh
# Behavioural regression test for secrets delivered through discovery HTTP.

set -eu

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
START_SCRIPT="$SCRIPT_DIR/overlay/etc/local.d/discovery.start"
CASE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/discovery-secret-test.XXXXXX")"
trap 'rm -rf "$CASE_DIR"' EXIT HUP INT TERM

HTTP_BODY_FILE="$CASE_DIR/http-body"
ROLE_CONFIG_FILE="$CASE_DIR/role-config"
export HTTP_BODY_FILE ROLE_CONFIG_FILE

# Load the production functions without invoking main. Keeping the test against
# the shipped script avoids a second implementation of the secret lifecycle.
sed '$d' "$START_SCRIPT" > "$CASE_DIR/discovery-functions.sh"
# shellcheck source=/dev/null
. "$CASE_DIR/discovery-functions.sh"

# Simulate Alpine's ordinary service umask. The production helpers must make
# their own secret files private without relying on a restrictive caller.
umask 022

file_mode() {
	stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"
}

fail() {
	echo "FAIL: $*" >&2
	exit 1
}

# Fake curl creates its output exactly as the real helper requests, so this
# proves the helper protects a response at creation time.
curl() {
	out=""
	while [ "$#" -gt 0 ]; do
		case "$1" in
		-o) out="$2"; shift 2 ;;
		-w|--connect-timeout|-m|-X|-H|--data) shift 2 ;;
		*) shift ;;
		esac
	done
	[ -n "$out" ] || fail "fake curl received no output path"
	printf '%s' '{"ok":true}' > "$out"
	printf '%s' 200
}

printf '%s' old-response > "$HTTP_BODY_FILE"
chmod 644 "$HTTP_BODY_FILE"
status="$(http_post 'https://example.invalid/register' '{}')"
[ "$status" = 200 ] || fail "http_post returned $status"
[ "$(file_mode "$HTTP_BODY_FILE")" = 600 ] || \
	fail "HTTP response mode is $(file_mode "$HTTP_BODY_FILE"), expected 600"

# Replace only the network/cmdline edges and exercise the production ack
# parser, config write, and raw-response cleanup.
cmdline_value() {
	case "$1" in
	machine_id) printf '%s\n' machine-test ;;
	role_nonce) printf '%s\n' nonce-test ;;
	*) return 1 ;;
	esac
}

http_post() {
	printf '%s' '{"config":{"source":"https://trips.example/manifest","token":"test-secret"}}' > "$HTTP_BODY_FILE"
	printf '%s\n' 200
}

log() { :; }

command -v jq >/dev/null 2>&1 || fail "jq is required for this test"
rm -f "$HTTP_BODY_FILE"
ack_ram_role || fail "role ack failed"

[ -f "$ROLE_CONFIG_FILE" ] || fail "role config was not written"
[ "$(file_mode "$ROLE_CONFIG_FILE")" = 600 ] || \
	fail "role config mode is $(file_mode "$ROLE_CONFIG_FILE"), expected 600"
[ "$(jq -r '.token' "$ROLE_CONFIG_FILE")" = test-secret ] || \
	fail "role config content was not preserved"
[ ! -e "$HTTP_BODY_FILE" ] || fail "raw role ack response still exists"

echo "PASS: discovery HTTP secrets are private and raw role config is removed"
