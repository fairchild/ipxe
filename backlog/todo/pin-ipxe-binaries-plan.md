---
priority: 1
timeout: 4h
arc: security-debts
---

# Pin iPXE binaries into the container image

**Repo: this one (`~/code/ipxe`).**

`bootstrap/download-ipxe.sh` currently curls `undionly.kpxe`, `ipxe.efi`, and `ipxe-arm64.efi` from boot.ipxe.org at *container startup* with no checksum verification. Every PXE client runs whatever came down, at pre-OS privilege. It's also an availability coupling: `set -e` + `curl -f` means the container won't start when boot.ipxe.org is down.

Fix: download the binaries at **build time** in the Dockerfile, verify against pinned sha256s, and ship them in the image. Startup then just runs envsubst + dnsmasq.

Steps:
1. Fetch current binaries from boot.ipxe.org, record their sha256s into `bootstrap/ipxe-binaries.sha256` (format: `<hash>  <filename>`).
2. Rework `bootstrap/Dockerfile`: RUN that fetches the three binaries into `/tftpboot`, then `sha256sum -c` against the pinned file — build fails on mismatch.
3. Remove the download step from the ENTRYPOINT; keep `download-ipxe.sh` as the build-time fetch script (still honoring `TFTP_DIR`), called from the Dockerfile.
4. While here, repo hygiene: add `*.img.gz` to `.gitignore` and delete the two stray 20-byte `disk7-backup-*.img.gz` files at repo root.
5. Update README.md and CLAUDE.md "Key Design Decisions" (they currently say binaries download at startup). Also reconcile README quick-start vs compose on `--cap-add=NET_ADMIN` — test which is actually needed and document the working invocation.

Verify: `docker build -t ipxe-bootstrap ./bootstrap` succeeds; container starts dnsmasq without any network fetch; corrupting a hash in the sha256 file makes the build fail.

Outcome: merge-ready PR against `fairchild/ipxe` main.

---
