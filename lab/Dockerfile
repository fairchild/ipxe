# QEMU boot lab — reproduces the production PXE topology inside one privileged
# container so the full boot chain (proxy DHCP + TFTP handoff + iPXE second DHCP
# + HTTP(S) chain) is machine-checkable without real hardware.
#
# The lab CONSUMES the production bootstrap image: the pinned iPXE binaries and
# the proxy dnsmasq config are copied verbatim, never reimplemented.
ARG BOOTSTRAP_IMAGE=ipxe-bootstrap:lab
FROM ${BOOTSTRAP_IMAGE} AS bootstrap

FROM debian:stable-slim

# qemu (TCG, no KVM), UEFI firmware for x86-64 (OVMF) and arm64 (AAVMF),
# dnsmasq for both the fake home router and the proxy, plus the plumbing to
# build a bridge/tap network namespace and NAT egress out for the HTTPS chain.
RUN apt-get update && apt-get install -y --no-install-recommends \
      qemu-system-x86 \
      qemu-system-arm \
      ovmf \
      qemu-efi-aarch64 \
      dnsmasq \
      iproute2 \
      iptables \
      gettext-base \
      procps \
      python3 \
      curl \
      ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Production artifacts, straight from the bootstrap image.
COPY --from=bootstrap /tftpboot /tftpboot
COPY --from=bootstrap /etc/dnsmasq.conf.template /etc/dnsmasq.conf.template

COPY run-lab.sh /usr/local/bin/run-lab.sh
COPY dhcp-archmap.py /usr/local/bin/dhcp-archmap.py
COPY dnsmasq-auth.conf.template /etc/dnsmasq-auth.conf.template
COPY www /lab/www
RUN chmod +x /usr/local/bin/run-lab.sh

ENV IPXE_SERVER_URL=https://ipxe.cloudcompute.com

ENTRYPOINT ["/usr/local/bin/run-lab.sh"]
