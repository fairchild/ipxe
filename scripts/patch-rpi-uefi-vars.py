#!/usr/bin/env python3
"""Read and patch UEFI variables inside a pftf/RPi4 RPI_EFI.fd, offline.

The Pi has no NVRAM chip, so the pftf firmware persists every UEFI setup value
back into RPI_EFI.fd on the SD card's FAT partition. There is no config.txt knob
for any of it, and virt-fw-vars cannot parse the Pi's combined CODE+VARS layout.
That leaves this: locate the EDK2 authenticated variable store and edit values in
place.

Only same-size scalar edits are supported, deliberately. Nothing moves, nothing
is added, no offsets shift, and the firmware volume header (whose checksum covers
only itself) is never touched. That keeps a bricked-boot mistake off the table.

    patch-rpi-uefi-vars.py list  RPI_EFI.fd
    patch-rpi-uefi-vars.py set   RPI_EFI.fd SystemTableMode=2 RamLimitTo3GB=0

SystemTableMode: 0=ACPI, 1=ACPI+DT, 2=Devicetree. A Pi 4 needs 2 (or 1) or the
generic arm64 kernel cannot find the genet PHY over MDIO and Ethernet never comes
up -- ACPI is the firmware default, which is why a freshly flashed card always
boots with dead networking.
"""

from __future__ import annotations

import struct
import sys
import uuid
from pathlib import Path

AUTH_STORE_GUID = uuid.UUID("aaf32c78-947b-439a-a180-2e144ec37792")
PLAIN_STORE_GUID = uuid.UUID("ddcf3616-3275-4164-98b6-fe85707ffe7d")
VAR_START_ID = 0x55AA
VAR_ADDED = 0x3F
AUTH_HDR_LEN = 60
PLAIN_HDR_LEN = 32

KNOWN_ENUMS = {
    "SystemTableMode": {0: "ACPI", 1: "ACPI+Devicetree", 2: "Devicetree"},
    "RamLimitTo3GB": {0: "disabled (full RAM)", 1: "enabled (3GB cap)"},
    "ConsolePref": {0: "graphical", 1: "serial"},
}


class Variable:
    __slots__ = ("name", "guid", "data_offset", "data_len", "state")

    def __init__(self, name: str, guid: uuid.UUID, data_offset: int, data_len: int, state: int):
        self.name = name
        self.guid = guid
        self.data_offset = data_offset
        self.data_len = data_len
        self.state = state

    def value(self, blob: bytes) -> bytes:
        return blob[self.data_offset : self.data_offset + self.data_len]


def find_store(blob: bytes) -> tuple[int, int, int]:
    """Return (header_offset, store_size, header_len) for the variable store."""
    for off in range(0, len(blob) - 16, 4):
        guid = uuid.UUID(bytes_le=blob[off : off + 16])
        if guid == AUTH_STORE_GUID:
            (size,) = struct.unpack_from("<I", blob, off + 16)
            return off, size, AUTH_HDR_LEN
        if guid == PLAIN_STORE_GUID:
            (size,) = struct.unpack_from("<I", blob, off + 16)
            return off, size, PLAIN_HDR_LEN
    raise SystemExit("no EDK2 variable store found — is this a pftf RPI_EFI.fd?")


def parse(blob: bytes) -> list[Variable]:
    base, size, hdr_len = find_store(blob)
    end = min(base + size, len(blob))
    out: list[Variable] = []
    p = base + 28
    while p + hdr_len <= end:
        start_id, state = struct.unpack_from("<HB", blob, p)
        if start_id != VAR_START_ID:
            break
        if hdr_len == AUTH_HDR_LEN:
            name_size, data_size = struct.unpack_from("<II", blob, p + 36)
            guid = uuid.UUID(bytes_le=blob[p + 44 : p + 60])
        else:
            name_size, data_size = struct.unpack_from("<II", blob, p + 8)
            guid = uuid.UUID(bytes_le=blob[p + 16 : p + 32])
        if name_size > 1024 or data_size > 1 << 20:
            break
        name = blob[p + hdr_len : p + hdr_len + name_size].decode("utf-16-le", "replace").rstrip("\x00")
        out.append(Variable(name, guid, p + hdr_len + name_size, data_size, state))
        p = (p + hdr_len + name_size + data_size + 3) & ~3
    return out


def describe(name: str, raw: bytes) -> str:
    if len(raw) > 8:
        return raw[:16].hex() + ("..." if len(raw) > 16 else "")
    n = int.from_bytes(raw, "little")
    meaning = KNOWN_ENUMS.get(name, {}).get(n)
    return f"{n}" + (f"  ({meaning})" if meaning else "")


def cmd_list(path: Path) -> None:
    blob = path.read_bytes()
    variables = [v for v in parse(blob) if v.state == VAR_ADDED]
    print(f"{path}: {len(variables)} active variables")
    for v in sorted(variables, key=lambda v: v.name):
        print(f"  {v.name:26s} len={v.data_len:<5} {describe(v.name, v.value(blob))}")


def cmd_set(path: Path, assignments: list[str]) -> None:
    blob = bytearray(path.read_bytes())
    by_name = {v.name: v for v in parse(bytes(blob)) if v.state == VAR_ADDED}
    changed = False

    for assignment in assignments:
        if "=" not in assignment:
            raise SystemExit(f"expected NAME=VALUE, got {assignment!r}")
        name, _, raw_value = assignment.partition("=")
        var = by_name.get(name)
        if var is None:
            raise SystemExit(
                f"{name!r} is not an active variable in this image. "
                "Only existing variables can be edited in place — boot the Pi once "
                "so the firmware writes its defaults, then patch."
            )
        new = int(raw_value, 0).to_bytes(var.data_len, "little")
        old = var.value(bytes(blob))
        if new == old:
            print(f"  {name} already {describe(name, old)} — unchanged")
            continue
        blob[var.data_offset : var.data_offset + var.data_len] = new
        print(f"  {name}: {describe(name, old)} -> {describe(name, new)}")
        changed = True

    if not changed:
        print("nothing to write")
        return

    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
        print(f"backup written to {backup}")
    path.write_bytes(bytes(blob))

    verify = {v.name: v.value(path.read_bytes()) for v in parse(path.read_bytes()) if v.state == VAR_ADDED}
    for assignment in assignments:
        name, _, raw_value = assignment.partition("=")
        want = int(raw_value, 0)
        got = int.from_bytes(verify[name], "little")
        if want != got:
            raise SystemExit(f"VERIFY FAILED: {name} reads {got}, expected {want}")
    print(f"verified — {path} updated")


def main(argv: list[str]) -> None:
    if len(argv) < 3 or argv[1] not in {"list", "set"}:
        print(__doc__)
        raise SystemExit(2)
    path = Path(argv[2])
    if not path.is_file():
        raise SystemExit(f"no such file: {path}")
    if argv[1] == "list":
        cmd_list(path)
    else:
        cmd_set(path, argv[3:])


if __name__ == "__main__":
    main(sys.argv)
