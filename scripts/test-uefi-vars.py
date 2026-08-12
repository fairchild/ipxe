#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Tests for the offline RPI_EFI.fd editor, on a synthetic variable store.

Synthetic because the only real seeded images we have belong to actual Pis and
carry their MAC addresses, which is the thing this code exists to remove and
the last thing this public repository should contain. The store is built here
to the same EDK2 layout the tool parses, so the tests exercise the real walk.

What matters and is therefore tested: a scrubbed image keeps the settings that
make the platform boot (SystemTableMode above all), loses every trace of the
machine it came from — including records the firmware deleted but never
erased — and stays parseable afterwards, because a store whose traversal
breaks is a brick.
"""

from __future__ import annotations

import importlib.util
import struct
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

TOOL = Path(__file__).parent / "patch-rpi-uefi-vars.py"
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("uefivars", TOOL)
assert SPEC and SPEC.loader
uefivars = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(uefivars)

VENDOR_GUID = uuid.UUID("8be4df61-93ca-11d2-aa0d-00e098032b8c")


def variable(name: str, data: bytes, state: int = 0x3F) -> bytes:
    """One EDK2 authenticated variable header plus its name and data."""
    encoded = name.encode("utf-16-le") + b"\x00\x00"
    header = struct.pack("<HBB", 0x55AA, state, 0)  # StartId, State, reserved
    header += b"\x00" * 32  # MonotonicCount + TimeStamp + PubKeyIndex
    header += struct.pack("<II", len(encoded), len(data))
    header += VENDOR_GUID.bytes_le
    assert len(header) == uefivars.AUTH_HDR_LEN, len(header)
    body = header + encoded + data
    return body + b"\x00" * (-len(body) % 4)  # entries are 4-byte aligned


def build_store(entries: list[bytes]) -> bytes:
    """A firmware image: some padding, the store header, then the variables."""
    payload = b"".join(entries)
    size = 28 + len(payload) + 4096  # trailing free space, as real stores have
    header = uefivars.AUTH_STORE_GUID.bytes_le + struct.pack("<I", size)
    header += struct.pack("<BBH", 0x5A, 0xFE, 0)  # Format, State, reserved
    header += b"\x00" * (28 - len(header))
    return b"\x00" * 4096 + header + payload + b"\xff" * 4096


class ScrubTests(unittest.TestCase):
    MAC = "AABBCCDDEEFF"

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.fd = Path(self.tmp.name) / "RPI_EFI.fd"
        self.fd.write_bytes(
            build_store(
                [
                    variable("SystemTableMode", (2).to_bytes(4, "little")),
                    variable("RamLimitTo3GB", (0).to_bytes(4, "little")),
                    variable("ClientId", bytes.fromhex("0102") + bytes.fromhex(self.MAC)),
                    variable(self.MAC, b"per-nic state"),
                    variable(
                        "Boot0002",
                        b"\x01\x00\x00\x00\x44\x00"
                        + f"UEFI PXEv4 (MAC:{self.MAC})".encode("utf-16-le"),
                    ),
                    variable("Boot0001", b"\x01\x00\x00\x00\x18\x00" + "SD/MMC".encode("utf-16-le")),
                    # A record the firmware deleted but never erased: state is
                    # not VAR_ADDED, yet the MAC is still sitting in the bytes.
                    variable(f"{self.MAC}", b"stale nic record", state=0x3C),
                ]
            )
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def scrub(self):
        return subprocess.run(
            [sys.executable, str(TOOL), "scrub", str(self.fd)],
            capture_output=True,
            text=True,
        )

    def test_scrub_removes_every_trace_of_the_machine(self) -> None:
        result = self.scrub()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        blob = self.fd.read_bytes()
        self.assertNotIn(bytes.fromhex(self.MAC), blob, "raw MAC survived")
        self.assertNotIn(
            self.MAC.encode("utf-16-le"), blob, "MAC survived as UTF-16 text"
        )

    def test_scrub_keeps_the_settings_that_make_it_boot(self) -> None:
        """The whole point of seeding from a working Pi. SystemTableMode=2 is
        what lets the arm64 kernel find the genet PHY; lose it and the card
        boots to a machine with no network."""
        self.scrub()
        active = {
            v.name: v.value(self.fd.read_bytes())
            for v in uefivars.parse(self.fd.read_bytes())
            if v.state == uefivars.VAR_ADDED
        }
        self.assertEqual(active["SystemTableMode"], (2).to_bytes(4, "little"))
        self.assertIn("RamLimitTo3GB", active)
        self.assertIn("Boot0001", active, "the SD/MMC boot option is not machine-specific")

    def test_scrub_deactivates_only_the_machine_specific_variables(self) -> None:
        self.scrub()
        active = {
            v.name for v in uefivars.parse(self.fd.read_bytes())
            if v.state == uefivars.VAR_ADDED
        }
        self.assertNotIn("ClientId", active)
        self.assertNotIn(self.MAC, active)
        self.assertNotIn("Boot0002", active)

    def test_store_still_walks_after_scrubbing(self) -> None:
        """Zeroing must never change a size field: the walk steps by
        name_size/data_size, so a wrong length loses every later variable and
        the firmware boots to defaults — the failure that looks like the patch
        silently not applying."""
        before = len(uefivars.parse(self.fd.read_bytes()))
        self.scrub()
        after = len(uefivars.parse(self.fd.read_bytes()))
        self.assertEqual(before, after, "variable count changed — traversal broke")

    def test_delete_on_its_own_also_erases_the_contents(self) -> None:
        """`delete` is usable without `scrub`, and its contract is that the
        bytes go too. Inside scrub the later purge would cover for it, which
        is exactly why this needs its own test: a delete that only flipped the
        state byte would still leave a MAC in any image deleted directly."""
        self.assertIn(b"per-nic state", self.fd.read_bytes())

        result = subprocess.run(
            [sys.executable, str(TOOL), "delete", str(self.fd), "ClientId", self.MAC],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        blob = self.fd.read_bytes()
        # The payload of each named variable, and its name where the name is
        # itself the MAC. Variables not named — Boot0002 here — are untouched
        # by delete; removing those is scrub's policy, not delete's.
        self.assertNotIn(b"per-nic state", blob, "deleted variable's data survived")
        self.assertNotIn(
            bytes.fromhex(self.MAC), blob, "MAC inside ClientId's data survived"
        )

    def test_scrub_is_idempotent(self) -> None:
        self.scrub()
        once = self.fd.read_bytes()
        self.scrub()
        self.assertEqual(once, self.fd.read_bytes())


class SetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.fd = Path(self.tmp.name) / "RPI_EFI.fd"
        self.fd.write_bytes(
            build_store([variable("SystemTableMode", (0).to_bytes(4, "little"))])
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_set_changes_a_scalar_in_place(self) -> None:
        result = subprocess.run(
            [sys.executable, str(TOOL), "set", str(self.fd), "SystemTableMode=2"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        active = {
            v.name: v.value(self.fd.read_bytes())
            for v in uefivars.parse(self.fd.read_bytes())
            if v.state == uefivars.VAR_ADDED
        }
        self.assertEqual(active["SystemTableMode"], (2).to_bytes(4, "little"))

    def test_set_refuses_a_variable_the_image_does_not_have(self) -> None:
        """A fresh pftf release has an empty store, and quietly doing nothing
        there would produce a card that boots in ACPI mode with no network."""
        result = subprocess.run(
            [sys.executable, str(TOOL), "set", str(self.fd), "NotAThing=1"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not an active variable", result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
