---
priority: 3
timeout: 3d
arc: discovery-os
dependencies:
  first-boot-token-plan: "discovery OS registers via POST /api/machines/register and needs the token flow to exist"
---

# Discovery OS: RAM-boot image that inventories and registers

**Components: both** — image build tooling lives in this repository's
`discovery/` directory; the menu entry and overlay serving belong to the
separately operated control-plane Worker.

The spine of zero-touch join: a blank machine netboots into a small RAM-only Linux, inventories hardware, registers with the Worker as `discovered`, receives its first-boot token, and polls for a role assignment. Doubles as the ephemeral/lab boot target (rescue shell).

Approach: Alpine netboot (vmlinuz-lts + initramfs + modloop, from dl-cdn.alpinelinux.org or self-hosted in R2) with a Worker-served apkovl overlay whose init script:
1. Gathers inventory: MACs (`/sys/class/net`), CPU (`/proc/cpuinfo`), RAM (`/proc/meminfo`), disks (`lsblk -J`), DMI vendor/model (`/sys/class/dmi/id`), arch (`uname -m`).
2. `POST /api/machines/register` with the JSON; keep returned token in tmpfs.
3. Polls `GET /api/machines/:id/assignment` (token-authed) every 30s. On assignment → `reboot` (next PXE boot serves the per-machine install script — hand-off defined in role-assign-preseed-plan). Otherwise wait, printing status, with a shell on tty1.

Deliverables:
- `discovery/` build/fetch script producing the overlay and pinning kernel/initrd sources (x86_64 first; arm64 follow-up).
- Worker: `discovery` entry in `src/scripts/distros.ts` (netboot type with Alpine kernel/initrd + apkovl param) and a route serving the overlay tarball.
- Arch-detector menu gains "Discovery / Register this machine"; consider making it the default timeout choice for unknown MACs.
- Verify against `wrangler dev` in QEMU (UTM or `qemu-system-x86_64 -kernel/-initrd` direct-boot) that register + poll round-trips.

Real-hardware validation is Michael's step; the PR must include the exact QEMU command used to verify.

Outcome: merge-ready PR per repo touched, cross-linked.

---
- 2026-07-04T18:41:18Z advanced to=doing claimer=fairchild@blue branch=main
- 2026-07-04T18:42:14Z progress | discovery-os agent dispatched: overlay build in ipxe repo (feat/discovery-os off main), worker routes stacked on feat/ipxe-machine-registry
- 2026-07-04T19:06:32Z advanced to=done
- 2026-07-04T19:06:33Z progress | node and control-plane changes verified with unit(68 tests)+container-sim+QEMU netboot round-trip vs wrangler dev; Alpine 3.22 pinned; wrangler dev needs run_worker_first (latent config gap, flagged not committed)
