# Trusted root CAs

These certificates' SHA256 fingerprints get baked into the custom iPXE binaries
via `TRUST=` (see `../compile-ipxe.sh`). iPXE trusts a TLS chain the moment it
reaches a **presented** certificate whose fingerprint is in this list —
`x509_check_root()` is a plain fingerprint compare, so the anchor does not have
to be self-signed, but it *does* have to be a cert the server actually sends.
There is no callout to `ca.ipxe.org`.

## The cross-sign gotcha (why the obvious cert is the wrong one)

The live chain for `ipxe.cloudcompute.com` (Google Trust Services, fronted by
Cloudflare) is:

```
CN=cloudcompute.com  (ECDSA P-256)        leaf, rotates ~90d
  └─ GTS WE1         (ECDSA P-256)        intermediate, rotates
       └─ GTS Root R4 (ECDSA P-384)       ← issued by GlobalSign Root CA,
                                            i.e. the CROSS-SIGNED R4
```

The top cert the server presents is **GTS Root R4 cross-signed by GlobalSign**
(`issuer = GlobalSign Root CA`), not the self-signed GTS Root R4 root you get
from `pki.goog`. They share the same public key but are different DER
certificates, so they have **different fingerprints**:

```
cross-signed R4 (presented)   76:B2:7B:80:A5:80:27:DC:3C:F1:DA:68:DA:C1:70:10:ED:93:99:7D:0B:60:3E:2F:AD:BE:85:01:24:93:B5:A7
self-signed  R4 (pki.goog)    34:9D:FA:40:58:C5:E2:63:12:3B:39:8A:E7:95:57:3C:4E:13:13:C8:3F:E6:8F:93:55:6C:D5:E8:03:1B:3C:7D
```

Trusting only the self-signed R4 makes iPXE fail every handshake with
"Untrusted root certificate" (EACCES 0x…eb3c), because that fingerprint never
appears in the presented chain. This was caught by a QEMU boot test against the
live server — it is not visible from `openssl verify`, which uses the local
trust store rather than fingerprint pinning.

`gtsr4-globalsign.pem` was extracted from the live handshake and verified to
carry the genuine R4 public key (`openssl pkey` hash identical to the
self-signed root's), and it chains under the well-known GlobalSign Root CA.

## Embedded roots

| File | Cert | Role |
|------|------|------|
| `gtsr4-globalsign.pem` | GTS Root R4, cross-signed by GlobalSign | **Load-bearing** — the cert actually presented |
| `gtsr4.pem` | GTS Root R4, self-signed | Hedge if the CDN switches to presenting the self-signed root |
| `gtsr1.pem` | GTS Root R1 | Hedge for a GTS RSA (WR-series) reissue |
| `isrgrootx1.pem` | ISRG Root X1 | Hedge if the CDN switches to Let's Encrypt |

The hedges are inert unless the server later presents a chain ending in them.

## Fingerprints (verify before trusting)

```
GTS Root R4 (cross)  76:B2:7B:80:A5:80:27:DC:3C:F1:DA:68:DA:C1:70:10:ED:93:99:7D:0B:60:3E:2F:AD:BE:85:01:24:93:B5:A7
GTS Root R4 (self)   34:9D:FA:40:58:C5:E2:63:12:3B:39:8A:E7:95:57:3C:4E:13:13:C8:3F:E6:8F:93:55:6C:D5:E8:03:1B:3C:7D
GTS Root R1          D9:47:43:2A:BD:E7:B7:FA:90:FC:2E:6B:59:10:1B:12:80:E0:E1:C7:E4:E4:0F:A3:C6:88:7F:FF:57:A7:F4:CF
ISRG Root X1         96:BC:EC:06:26:49:76:F3:74:60:77:9A:CF:28:C5:A7:CF:E8:A3:C0:AA:E1:1A:8F:FC:EE:05:C0:BD:DF:08:C6
```

Regenerate/verify:

```sh
for f in gtsr4-globalsign gtsr4 gtsr1 isrgrootx1; do
  openssl x509 -in "$f.pem" -noout -subject -issuer -fingerprint -sha256
done
```

## Known fragility

iPXE stores only fingerprints, so it cannot complete a partial chain — the CDN
must keep presenting a cert whose fingerprint is trusted. The cross-signed R4
expires 2028-01 and will eventually be reissued (new DER → new fingerprint). If
a handshake to `ipxe.cloudcompute.com` starts failing cert validation, re-dump
the presented chain and update the trusted cert:

```sh
openssl s_client -connect ipxe.cloudcompute.com:443 \
  -servername ipxe.cloudcompute.com -showcerts </dev/null 2>/dev/null \
  | awk '/BEGIN CERT/{c++} c==3{print} /END CERT/&&c==3{exit}'
```
