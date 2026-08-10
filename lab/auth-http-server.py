#!/usr/bin/env python3
"""Local upstream stub that accepts only the bootstrap proxy's bearer."""

from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from urllib.parse import urlsplit


class AuthenticatedBootHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if urlsplit(self.path).path == "/boot.ipxe":
            expected = f"Bearer {os.environ['BOOTSTRAP_TOKEN']}"
            if self.headers.get("Authorization") != expected:
                self.send_error(HTTPStatus.UNAUTHORIZED)
                return
        super().do_GET()


if __name__ == "__main__":
    bind = os.environ.get("AUTH_STUB_BIND", "10.77.0.1")
    port = int(os.environ.get("AUTH_STUB_PORT", "8081"))
    ThreadingHTTPServer((bind, port), AuthenticatedBootHandler).serve_forever()
