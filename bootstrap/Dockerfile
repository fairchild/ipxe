FROM alpine:3.20

RUN apk add --no-cache dnsmasq envsubst curl

COPY dnsmasq.conf.template /etc/dnsmasq.conf.template
COPY download-ipxe.sh /usr/local/bin/download-ipxe.sh
RUN chmod +x /usr/local/bin/download-ipxe.sh

RUN mkdir -p /tftpboot

ENV IPXE_SERVER_URL=https://ipxe.cloudcompute.com
ENV DHCP_RANGE=192.168.1.0

EXPOSE 69/udp 4011/udp

ENTRYPOINT ["/bin/sh", "-c", "/usr/local/bin/download-ipxe.sh && envsubst < /etc/dnsmasq.conf.template > /etc/dnsmasq.conf && exec dnsmasq --no-daemon --log-queries"]
