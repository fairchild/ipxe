# Builder stage: compile the custom iPXE binaries from a pinned upstream commit
# (see build/compile-ipxe.sh — shared with the standalone build/Dockerfile so
# the container ships exactly what build/build.sh produces). Each binary embeds
# a retry→chain-to-DHCP-filename boot script and pinned root-CA fingerprints, so there
# is no boot.ipxe.org download and no ca.ipxe.org trust dependency.
#
# Build context is the repo root: docker build -f bootstrap/Dockerfile .
# Pinned to amd64 so the builder cross-compiles every iPXE target identically
# for both the amd64 and arm64 runtime images (arm64 runs it under emulation).
FROM --platform=linux/amd64 debian:bookworm-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential liblzma-dev git perl ca-certificates \
      gcc-i686-linux-gnu binutils-i686-linux-gnu \
      gcc-aarch64-linux-gnu binutils-aarch64-linux-gnu \
    && rm -rf /var/lib/apt/lists/*

COPY build /build
RUN OUTPUT_DIR=/tftpboot sh /build/compile-ipxe.sh

FROM alpine:3.20

RUN apk add --no-cache ca-certificates dnsmasq envsubst python3

COPY bootstrap/dnsmasq.conf.template /etc/dnsmasq.conf.template
COPY bootstrap/boot-proxy.py /usr/local/bin/boot-proxy
COPY bootstrap/entrypoint.sh /usr/local/bin/bootstrap-entrypoint
COPY --from=builder /tftpboot /tftpboot
COPY bootstrap/boot.ipxe /tftpboot/bootstrap.ipxe

RUN chmod +x /usr/local/bin/boot-proxy /usr/local/bin/bootstrap-entrypoint

ENV IPXE_SERVER_URL=https://ipxe.cloudcompute.com
ENV DHCP_RANGE=192.168.1.0
ENV BOOTSTRAP_CLIENT_CIDR=192.168.1.0/24

EXPOSE 69/udp 4011/udp 8080/tcp

ENTRYPOINT ["/usr/local/bin/bootstrap-entrypoint"]
