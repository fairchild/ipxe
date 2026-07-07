#!/usr/bin/env python3
# Proxy-DHCP stage-1 arch check.
#
# For each client-arch, send a DHCPDISCOVER mimicking a *dumb* (non-iPXE) PXE
# firmware and confirm the proxy answers with a ProxyDHCP boot offer (yiaddr==0).
# This is the stage-1 handoff that qemu itself cannot exercise: qemu's own network
# boot ROMs are already iPXE and send user-class "iPXE" on their first request,
# short-circuiting straight to stage 2. Driving raw DHCP lets us prove a dumb
# firmware of every architecture — including UEFI arch 9, which no guest covers —
# gets a boot offer from the proxy.
#
# Each arch uses a distinct MAC so the proxy log shows the arch tag it assigned
# (run-lab asserts those tags); combined with the TFTP sha256 check, this proves
# the arch -> stock-binary mapping end to end. Exit 0 only if every arch is
# offered a boot.
import socket, struct, random, time, sys

IFACE = b"br0"
SERVER = "10.77.0.1"

# arch, label, MAC-suffix, the stock binary the proxy config maps this arch to
ARCHES = [
    (0,  "BIOS x86",     0xa0, "undionly.kpxe"),
    (7,  "UEFI x86-64",  0xa7, "ipxe.efi"),
    (9,  "UEFI x86-64'", 0xa9, "ipxe.efi"),
    (11, "UEFI ARM64",   0xab, "ipxe-arm64.efi"),
]

def _opt(t, v): return bytes([t, len(v)]) + v

def _discover(arch, mac):
    xid = random.randint(0, 1 << 32)
    p = struct.pack("!BBBBIHH4s4s4s4s16s64s128s", 1, 1, 6, 0, xid, 0, 0x8000,
                    b"\0"*4, b"\0"*4, b"\0"*4, b"\0"*4, mac + b"\0"*10,
                    b"\0"*64, b"\0"*128)
    p += struct.pack("!4B", 99, 130, 83, 99) + bytes([53, 1, 1])
    vc = ("PXEClient:Arch:%05d:UNDI:002001" % arch).encode()
    return (p + bytes([55, 4, 1, 3, 60, 67])
            + bytes([93, 2]) + struct.pack("!H", arch)
            + _opt(60, vc) + _opt(94, b"\x01\x02\x01")
            + _opt(97, b"\x00" + bytes(range(16))) + bytes([255]))

def proxy_offers(arch, macsuffix):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try: s.setsockopt(socket.SOL_SOCKET, 25, IFACE)      # SO_BINDTODEVICE
    except OSError: pass
    s.bind(("0.0.0.0", 68)); s.settimeout(3)
    mac = b"\x52\x54\x00\x00\x77" + bytes([macsuffix])
    s.sendto(_discover(arch, mac), ("255.255.255.255", 67))
    t = time.time(); got = False
    while time.time() - t < 3:
        try: data, _ = s.recvfrom(2048)
        except socket.timeout: break
        if data[16:20] == b"\0\0\0\0":                   # yiaddr==0 -> proxy offer
            got = True
    s.close(); return got

def main():
    failures = 0
    for arch, label, macsuffix, binary in ARCHES:
        ok = proxy_offers(arch, macsuffix)
        status = "PASS" if ok else "FAIL"
        print(f"    arch {arch:2d} ({label:12s}) -> proxy boot offer [{binary:16s}] {status}")
        failures += 0 if ok else 1
        time.sleep(0.3)
    sys.exit(1 if failures else 0)

if __name__ == "__main__":
    main()
