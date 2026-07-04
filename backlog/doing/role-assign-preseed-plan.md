---
priority: 4
timeout: 3d
arc: unattended-install
dependencies:
  first-boot-token-plan: "assignment and preseed are keyed by machine id + token"
  discovery-os-plan: "machines reach pending state via discovery OS before roles are assignable"
---

# Role assignment → per-machine preseed → installed server checks in

**Repo: `~/code/services/ipxe`.**

Closes the loop from `pending` machine to working server:
1. **Assignment API**: `POST /api/machines/:id/assign { role }` (DASHBOARD_TOKEN-authed) sets state=assigned. `GET /api/machines/:id/assignment` (machine-token-authed) returns it — the discovery OS polls this.
2. **Per-machine boot branch**: pass `${net0/mac}` as a query param from the arch-detector script; when an assigned machine PXE-boots, `boot.ipxe` serves the distro netinstall script with preseed args instead of the interactive menu.
3. **Preseed serving**: `GET /config/:machineId/preseed.cfg` — Debian preseed template rendered per machine: hostname from role, guided-LVM whole-first-disk, mirror, and a `late_command` writing the machine token + a firstboot systemd unit into /target. Template lives in `src/scripts/` beside the iPXE templates. Endpoint auth note: d-i fetches preseed with plain HTTP GET and can't send headers — key the URL on machine id + a short-lived nonce embedded in the served boot script, and have it single-use, since the preseed contains the token delivery path.
4. **Firstboot check-in**: installed unit curls `POST /api/machines/:id/checkin { stage: "os" }` with the token → state=active, then disables itself.
5. **Dashboard**: minimal — list machines with state + an assign control, token-gated per auth-boots-endpoint-plan.
6. **Roles**: static map in `src/scripts/roles.ts` (name → hostname pattern, packages, optional post-install script URL). Config-management beyond firstboot is out of scope.
7. Tests: assignment state machine, preseed renders hostname/token correctly, unauthorized assign rejected, preseed nonce single-use.

Verify end-to-end in QEMU: discovery → assign via curl → reboot → unattended Debian install → firstboot check-in flips state to active. Document the run in the PR.

Outcome: merge-ready PR against the services repo.

---
- 2026-07-04T19:06:33Z advanced to=doing claimer=fairchild@blue branch=main
