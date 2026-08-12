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
    patch-rpi-uefi-vars.py scrub RPI_EFI.fd   # make a seeded image cloneable

SystemTableMode: 0=ACPI, 1=ACPI+DT, 2=Devicetree. A Pi 4 needs 2 (or 1) or the
generic arm64 kernel cannot find the genet PHY over MDIO and Ethernet never comes
up -- ACPI is the firmware default, which is why a freshly flashed card always
boots with dead networking.
"""

from __future__ import annotations

import re
import struct
import sys
import uuid
from pathlib import Path

AUTH_STORE_GUID = uuid.UUID("aaf32c78-947b-439a-a180-2e144ec37792")
PLAIN_STORE_GUID = uuid.UUID("ddcf3616-3275-4164-98b6-fe85707ffe7d")
VAR_START_ID = 0x55AA
VAR_ADDED = 0x3F
# EDK2 records deletion by clearing bits (flash can only clear them):
# VAR_ADDED & VAR_DELETED == 0x3F & 0xFD == 0x3D. Firmware skips any header
# whose state is not exactly VAR_ADDED, so this is the whole mechanism.
VAR_DELETED_STATE = 0x3D
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


def cmd_delete(path: Path, names: list[str]) -> None:
    """Mark variables deleted and wipe what they held.

    Why both. EDK2 records a variable's fate in one state byte, and deleting is
    clearing bits in it — 0x3F -> 0x3D — which is a same-size, in-place edit and
    so stays inside this tool's rule that nothing ever moves. But a deleted
    variable's bytes are merely ignored, not erased, and the variables worth
    deleting here are the ones that identify a particular Pi: its DHCP ClientId,
    the per-NIC records EDK2 names after the MAC, and _NDL. Leaving their
    contents in the image would put a real MAC address inside an artifact this
    project publishes, which is the thing we are deleting them to avoid. So the
    name and data are zeroed too — same size, no offsets shift, and the result
    is checkable by searching the file rather than by trusting this function.

    The firmware writes fresh values for all of these on the next boot, per
    machine, which is what makes one image cloneable across Pis.
    """
    blob = bytearray(path.read_bytes())
    base, size, hdr_len = find_store(bytes(blob))
    wanted = set(names)
    hit: list[str] = []

    # Re-walk rather than reuse parse()'s offsets: we need each variable's
    # header position, which is what carries the state byte.
    end = min(base + size, len(blob))
    p = base + 28
    while p + hdr_len <= end:
        start_id, state = struct.unpack_from("<HB", blob, p)
        if start_id != VAR_START_ID:
            break
        if hdr_len == AUTH_HDR_LEN:
            name_size, data_size = struct.unpack_from("<II", blob, p + 36)
        else:
            name_size, data_size = struct.unpack_from("<II", blob, p + 8)
        if name_size > 1024 or data_size > 1 << 20:
            break
        name = (
            bytes(blob[p + hdr_len : p + hdr_len + name_size])
            .decode("utf-16-le", "replace")
            .rstrip("\x00")
        )
        if state == VAR_ADDED and name in wanted:
            blob[p + 2] = VAR_DELETED_STATE
            span = p + hdr_len
            blob[span : span + name_size + data_size] = b"\x00" * (name_size + data_size)
            hit.append(name)
        p = (p + hdr_len + name_size + data_size + 3) & ~3

    missing = wanted - set(hit)
    if missing:
        # Not fatal: scrubbing a name that a given image never had is the
        # normal case across firmware versions and boot histories.
        print(f"  not present (nothing to do): {', '.join(sorted(missing))}")
    if not hit:
        print("nothing to write")
        return

    backup = path.with_suffix(path.suffix + ".bak")
    if not backup.exists():
        backup.write_bytes(path.read_bytes())
        print(f"backup written to {backup}")
    path.write_bytes(bytes(blob))

    written = path.read_bytes()
    still_active = {v.name for v in parse(written) if v.state == VAR_ADDED}
    for name in hit:
        if name in still_active:
            raise SystemExit(f"VERIFY FAILED: {name} is still active after delete")
        print(f"  deleted and zeroed: {name}")
    print(f"verified — {path} updated")


MAC_NAME = re.compile(r"^[0-9A-F]{12}$")


def machine_specific(blob: bytes) -> list[str]:
    """Names of the variables that make an RPI_EFI.fd belong to one Pi.

    Three kinds, all regenerated by the firmware on the next boot:
      * ClientId and _NDL — the DHCP client identifier and network device list,
        both of which embed the NIC's MAC.
      * variables EDK2 names after the MAC itself (12 uppercase hex).
      * the auto-created "UEFI PXEv4/v6 (MAC:...)" boot options, whose
        descriptions carry the MAC as text.

    Everything else in the store is a setting, not an identity, and settings
    are the reason to keep a seeded image at all.
    """
    names: list[str] = []
    for v in parse(blob):
        if v.state != VAR_ADDED:
            continue  # dead entries are handled by purge_dead, not by name
        if v.name in ("ClientId", "_NDL") or MAC_NAME.match(v.name):
            names.append(v.name)
        elif v.name.startswith("Boot") and v.name[4:].isalnum() and len(v.name) == 8:
            text = v.value(blob).decode("utf-16-le", "replace")
            if "MAC:" in text:
                names.append(v.name)
    return names


def purge_dead(path: Path) -> int:
    """Zero the name and data of every variable the store has already retired.

    EDK2 deletes by clearing a state bit and leaves the bytes in place until a
    reclaim pass happens to compact the store — so a firmware image carries the
    history of what the machine used to be, including MAC-named records from
    earlier boots. Those are exactly the bytes a published image must not have,
    and nothing reads them, so zeroing is free. Sizes are untouched, so the
    walk still finds every subsequent header.
    """
    blob = bytearray(path.read_bytes())
    base, size, hdr_len = find_store(bytes(blob))
    end = min(base + size, len(blob))
    p = base + 28
    purged = 0
    while p + hdr_len <= end:
        start_id, state = struct.unpack_from("<HB", blob, p)
        if start_id != VAR_START_ID:
            break
        if hdr_len == AUTH_HDR_LEN:
            name_size, data_size = struct.unpack_from("<II", blob, p + 36)
        else:
            name_size, data_size = struct.unpack_from("<II", blob, p + 8)
        if name_size > 1024 or data_size > 1 << 20:
            break
        span = p + hdr_len
        if state != VAR_ADDED and any(blob[span : span + name_size + data_size]):
            blob[span : span + name_size + data_size] = b"\x00" * (name_size + data_size)
            purged += 1
        p = (p + hdr_len + name_size + data_size + 3) & ~3
    if purged:
        path.write_bytes(bytes(blob))
    return purged


def cmd_scrub(path: Path) -> None:
    """Turn one Pi's firmware image into a cloneable one.

    A seeded RPI_EFI.fd is worth keeping because the settings that make this
    platform work — SystemTableMode=2 above all — cannot be written into a
    factory image offline: a fresh pftf release has an empty variable store,
    and this tool only edits what already exists. So the way to a card that
    boots correctly with no hands is to seed it from a Pi that already does,
    then remove what was that Pi's alone.
    """
    before = path.read_bytes()
    names = machine_specific(before)
    if names:
        print(f"{path}: scrubbing {len(names)} machine-specific variable(s)")
        cmd_delete(path, names)
    purged = purge_dead(path)
    if purged:
        print(f"  purged {purged} already-deleted record(s) left behind by the firmware")

    # Verify against the bytes rather than against the intent. Every MAC the
    # original image mentioned must be absent from the scrubbed one, in raw
    # form and as UTF-16 text — the two ways this store spells them.
    after = path.read_bytes()
    macs = {m.group(0) for m in re.finditer(rb"(?:[0-9A-F]\x00){12}", before)}
    leaked = []
    for mac_u16 in macs:
        text = mac_u16.decode("utf-16-le")
        raw = bytes.fromhex(text)
        if mac_u16 in after or raw in after:
            leaked.append(text)
    if leaked:
        raise SystemExit(
            "VERIFY FAILED: these still appear in the scrubbed image: "
            + ", ".join(sorted(leaked))
        )
    print(f"verified — no MAC-shaped identifier remains in {path}")


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
        if raw_value.startswith("hex:"):
            # Not everything worth editing is a scalar. BootOrder is an array of
            # little-endian UINT16 boot-option numbers, and reordering it is how
            # you stop the firmware running two full PXE timeouts before it
            # looks at the SD card.
            new = bytes.fromhex(raw_value[4:])
            if len(new) != var.data_len:
                raise SystemExit(
                    f"{name}: hex value is {len(new)} bytes, variable is {var.data_len}. "
                    "Only same-size edits are supported."
                )
        else:
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

    written = path.read_bytes()
    verify = {v.name: v.value(written) for v in parse(written) if v.state == VAR_ADDED}
    for assignment in assignments:
        name, _, raw_value = assignment.partition("=")
        got = verify[name]
        want = (
            bytes.fromhex(raw_value[4:])
            if raw_value.startswith("hex:")
            else int(raw_value, 0).to_bytes(len(got), "little")
        )
        if want != got:
            raise SystemExit(
                f"VERIFY FAILED: {name} reads {got.hex()}, expected {want.hex()}"
            )
    print(f"verified — {path} updated")


def main(argv: list[str]) -> None:
    if len(argv) < 3 or argv[1] not in {"list", "set", "delete", "scrub"}:
        print(__doc__)
        raise SystemExit(2)
    path = Path(argv[2])
    if not path.is_file():
        raise SystemExit(f"no such file: {path}")
    if argv[1] == "list":
        cmd_list(path)
    elif argv[1] == "delete":
        cmd_delete(path, argv[3:])
    elif argv[1] == "scrub":
        cmd_scrub(path)
    else:
        cmd_set(path, argv[3:])


if __name__ == "__main__":
    main(sys.argv)
