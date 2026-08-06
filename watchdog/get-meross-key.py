#!/usr/bin/env -S uv run --script --quiet
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Recover the account-wide Meross device key, locally.

The key is what lets the watchdog sign requests straight to a plug on the LAN,
with no cloud in the recovery path. Meross only hands it out through their login
API, so this asks for your credentials once, keeps them in memory, and prints
nothing but the key.

    watchdog/get-meross-key.py                     # prompt, print the key
    watchdog/get-meross-key.py --write             # append to ~/.config/ipxe-lab.env

The password is read with getpass — never echoed, never in shell history, never
written anywhere. Only the returned key is persisted, and only with --write.

Meross has no public API and changes this endpoint without notice; if the login
starts failing, the protocol moved rather than your credentials being wrong.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import json
import os
import random
import string
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Constant baked into the Meross apps; not a secret, just a protocol parameter.
SIGN_SECRET = "23x17ahWarFH6w29"
REGIONS = {
    "us": "https://iotx-us.meross.com",
    "eu": "https://iotx-eu.meross.com",
    "ap": "https://iotx-ap.meross.com",
}
ENV_PATH = Path.home() / ".config" / "ipxe-lab.env"


def sign_in(base_url: str, email: str, password: str, mfa: str | None) -> dict:
    nonce = "".join(random.choices(string.ascii_uppercase + string.digits, k=16))
    timestamp = int(time.time() * 1000)
    payload: dict[str, object] = {
        "email": email,
        "password": hashlib.md5(password.encode()).hexdigest(),
        "accountCountryCode": "",
        "encryption": 1,
        "agree": 0,
    }
    if mfa:
        payload["mfaCode"] = mfa

    params = base64.b64encode(json.dumps(payload).encode()).decode()
    signature = hashlib.md5(
        f"{SIGN_SECRET}{timestamp}{nonce}{params}".encode()
    ).hexdigest()

    body = json.dumps(
        {"params": params, "sign": signature, "timestamp": timestamp, "nonce": nonce}
    ).encode()
    request = urllib.request.Request(
        f"{base_url}/v1/Auth/signIn",
        data=body,
        headers={
            "Content-Type": "application/json",
            "AppVersion": "3.26.2",
            "AppType": "MerossIOT",
            "vender": "meross",
            "User-Agent": "MerossIOT/0.4",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", choices=sorted(REGIONS), default="us")
    parser.add_argument("--email")
    parser.add_argument("--mfa", help="6-digit code, if your account has MFA on")
    parser.add_argument(
        "--write",
        action="store_true",
        help=f"append MEROSS_KEY to {ENV_PATH} instead of printing it",
    )
    args = parser.parse_args()

    email = args.email or input("Meross account email: ").strip()
    password = getpass.getpass("Meross password (not echoed, not stored): ")

    try:
        result = sign_in(REGIONS[args.region], email, password, args.mfa)
    except urllib.error.HTTPError as exc:
        print(f"login failed: HTTP {exc.code} {exc.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"could not reach {REGIONS[args.region]}: {exc.reason}", file=sys.stderr)
        return 1
    finally:
        del password

    status = result.get("apiStatus")
    if status != 0:
        info = result.get("info", "no detail")
        hint = ""
        if status == 1030:
            hint = "  (wrong region — try --region eu or --region ap)"
        elif status in {1022, 1033}:
            hint = "  (account needs an MFA code — pass --mfa 123456)"
        print(f"login rejected: apiStatus={status} {info}{hint}", file=sys.stderr)
        return 1

    key = result.get("data", {}).get("key")
    if not key:
        print(f"login succeeded but no key in response: {result}", file=sys.stderr)
        return 1

    if not args.write:
        print(key)
        return 0

    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = ENV_PATH.read_text() if ENV_PATH.exists() else ""
    lines = [ln for ln in existing.splitlines() if not ln.startswith("MEROSS_KEY=")]
    lines.append(f"MEROSS_KEY={key}")
    ENV_PATH.write_text("\n".join(lines) + "\n")
    os.chmod(ENV_PATH, 0o600)
    print(f"MEROSS_KEY written to {ENV_PATH} (mode 600)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
