# Fetch stage: download stock iPXE binaries and verify against pinned sha256s.
# Build fails on mismatch — the runtime image never touches the network.
FROM alpine:3.20 AS fetch

RUN apk add --no-cache curl

COPY download-ipxe.sh /usr/local/bin/download-ipxe.sh
COPY ipxe-binaries.sha256 /ipxe-binaries.sha256

RUN sh /usr/local/bin/download-ipxe.sh && \
    cd /tftpboot && sha256sum -c /ipxe-binaries.sha256

FROM alpine:3.20

RUN apk add --no-cache dnsmasq envsubst

COPY dnsmasq.conf.template /etc/dnsmasq.conf.template
COPY --from=fetch /tftpboot /tftpboot

ENV IPXE_SERVER_URL=https://ipxe.cloudcompute.com
ENV DHCP_RANGE=192.168.1.0

EXPOSE 69/udp 4011/udp

ENTRYPOINT ["/bin/sh", "-c", "envsubst < /etc/dnsmasq.conf.template > /etc/dnsmasq.conf && exec dnsmasq --no-daemon --log-queries"]
