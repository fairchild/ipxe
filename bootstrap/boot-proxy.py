#!/usr/bin/env python3
"""Narrow LAN-to-control-plane proxy for authenticated iPXE boot scripts."""

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import os
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAC_RE = re.compile(r"^[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}$")
ARCH_RE = re.compile(r"^[A-Za-z0-9_+-]{1,32}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32,}$")
MAX_RESPONSE_BYTES = 256 * 1024


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


UPSTREAM = os.environ["IPXE_SERVER_URL"].rstrip("/")
TOKEN = os.environ["BOOTSTRAP_TOKEN"]
if not TOKEN_RE.fullmatch(TOKEN):
    raise SystemExit("BOOTSTRAP_TOKEN must be 32+ URL-safe characters")
CLIENT_CIDR = ipaddress.ip_network(os.environ["BOOTSTRAP_CLIENT_CIDR"])
ALLOWED_MACS = {
    mac.lower() for mac in os.environ["BOOTSTRAP_ALLOWED_MACS"].split(",")
}
if not ALLOWED_MACS or any(not MAC_RE.fullmatch(mac) for mac in ALLOWED_MACS):
    raise SystemExit("BOOTSTRAP_ALLOWED_MACS must be comma-separated MAC addresses")
UPSTREAM_PARTS = urlsplit(UPSTREAM)
if not UPSTREAM_PARTS.hostname or UPSTREAM_PARTS.username or UPSTREAM_PARTS.password:
    raise SystemExit("IPXE_SERVER_URL must be an origin without credentials")
if UPSTREAM_PARTS.query or UPSTREAM_PARTS.fragment:
    raise SystemExit("IPXE_SERVER_URL must not contain a query or fragment")
if UPSTREAM_PARTS.scheme != "https" and os.environ.get("BOOTSTRAP_ALLOW_INSECURE_UPSTREAM") != "1":
    raise SystemExit("IPXE_SERVER_URL must use https")

OPENER = build_opener(NoRedirects)


class BootProxyHandler(BaseHTTPRequestHandler):
    server_version = "ipxe-boot-proxy/1"

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if not self._client_allowed():
            self.send_error(HTTPStatus.FORBIDDEN)
            return

        parsed = urlsplit(self.path)
        if parsed.path == "/healthz":
            self._reply(HTTPStatus.OK, b"ok\n", "text/plain; charset=utf-8")
            return
        if parsed.path != "/boot.ipxe":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) != {"arch", "mac"} or any(len(values) != 1 for values in query.values()):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        arch = query["arch"][0]
        mac = query["mac"][0]
        if not ARCH_RE.fullmatch(arch) or not MAC_RE.fullmatch(mac):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        normalized_mac = mac.lower()
        if normalized_mac not in ALLOWED_MACS:
            self.send_error(HTTPStatus.FORBIDDEN)
            return

        upstream_url = f"{UPSTREAM}/boot.ipxe?{urlencode({'arch': arch, 'mac': normalized_mac})}"
        request = Request(
            upstream_url,
            headers={"Authorization": f"Bearer {TOKEN}", "Accept": "text/plain"},
        )
        try:
            with OPENER.open(request, timeout=15) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    self.send_error(HTTPStatus.BAD_GATEWAY)
                    return
                content_type = response.headers.get_content_type()
                self._reply(response.status, body, content_type)
        except HTTPError as error:
            body = error.read(MAX_RESPONSE_BYTES)
            self._reply(error.code, body, error.headers.get_content_type())
        except (URLError, TimeoutError, ssl.SSLError):
            self.send_error(HTTPStatus.BAD_GATEWAY)

    def _client_allowed(self) -> bool:
        try:
            return ipaddress.ip_address(self.client_address[0]) in CLIENT_CIDR
        except ValueError:
            return False

    def _reply(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    bind = os.environ.get("BOOTSTRAP_PROXY_BIND", "0.0.0.0")
    port = int(os.environ.get("BOOTSTRAP_PROXY_PORT", "8080"))
    ThreadingHTTPServer((bind, port), BootProxyHandler).serve_forever()
