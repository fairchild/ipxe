#!/bin/sh
# Fetch stock iPXE binaries from boot.ipxe.org at image build time.
# The Dockerfile verifies them against ipxe-binaries.sha256 after this runs.
set -e

TFTP_DIR="${TFTP_DIR:-/tftpboot}"
mkdir -p "$TFTP_DIR"

download() {
  local url="$1" dest="$2"
  echo "Downloading $url ..."
  curl -fsSL -o "$dest" "$url"
}

download "https://boot.ipxe.org/undionly.kpxe"             "$TFTP_DIR/undionly.kpxe"
download "https://boot.ipxe.org/x86_64-efi/ipxe.efi"       "$TFTP_DIR/ipxe.efi"
download "https://boot.ipxe.org/arm64-efi/ipxe.efi"        "$TFTP_DIR/ipxe-arm64.efi"

echo "iPXE binaries ready in $TFTP_DIR"
