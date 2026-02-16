#!/bin/sh
# Download stock iPXE binaries from boot.ipxe.org if not already present.
# These work with our dnsmasq config which tells iPXE to chain to our service.
set -e

TFTP_DIR="${TFTP_DIR:-/tftpboot}"

download() {
  local url="$1" dest="$2"
  if [ ! -f "$dest" ]; then
    echo "Downloading $url ..."
    curl -fsSL -o "$dest" "$url"
  fi
}

download "https://boot.ipxe.org/undionly.kpxe"             "$TFTP_DIR/undionly.kpxe"
download "https://boot.ipxe.org/x86_64-efi/ipxe.efi"       "$TFTP_DIR/ipxe.efi"
download "https://boot.ipxe.org/arm64-efi/ipxe.efi"        "$TFTP_DIR/ipxe-arm64.efi"

echo "iPXE binaries ready in $TFTP_DIR"
