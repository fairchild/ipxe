---
priority: 1
timeout: 1d
arc: validation
---

# Ops UI: fleet dashboard + /about architecture page

**Repo: `~/code/services`, ipxe/ subdir.** Decided in 2026-07-06 interview.

Michael wants to *understand what's going on* from the browser:
1. **Dashboard** (all four views chosen): machine lifecycle board grouped by state (discovered/pending/assigned/installing/active) with assign action; live boot feed (recent events: mac, arch, stage, target, time); health tiles (counts by state, boots today, stale machines by last_seen, spoof attempts from `spoofrev:`); per-machine detail (hardware inventory JSON from discovery, boot history, state timeline, role/preseed info).
2. **/about page**: the system explains itself — boot chain (firmware → proxy DHCP/TFTP → iPXE → HTTPS menu → discovery → assigned → installed → active), the two-repo shape (bootstrap container + Worker), machine lifecycle diagram, trust model (what's authenticated at each hop, TOFU tokens, nonce'd preseed, token rotation), links to dashboard.

Auth: keep the existing DASHBOARD_TOKEN localStorage flow (decided: token now, Better Auth via services/home only when a second user needs access). /about is public (no secrets in it); dashboard data behind the token as today.

Constraints: Worker serves static from public/ with API routes — follow existing patterns (pi.html). No build step/framework — hand-rolled HTML/CSS/JS in keeping with the repo. May need small read-only API additions (e.g. GET /api/machines/:id detail with events, spoof/stale counts endpoint) — DASHBOARD_TOKEN-authed, tested. Do not weaken any existing auth.

Branch from top of the review-fixed stack (feat/ipxe-role-preseed @ c13b7bcc); PR base feat/ipxe-role-preseed, retarget to main after Michael merges the stack.

Verify: Vitest for new endpoints; manual walkthrough against wrangler dev with seeded D1 machines in several states; screenshots in the PR.

Outcome: merge-ready PR.

---
- 2026-07-07T02:48:48Z advanced to=doing claimer=fairchild@blue branch=main
- 2026-07-07T03:14:24Z progress | control-plane change delivered /dashboard + /about + stats/detail APIs, 106 tests; screenshots reviewed, quality high; task stays in doing/ until review round dispositioned per Michael's protocol
- 2026-07-07T04:34:13Z advanced to=done | control-plane review dispositioned (3 threads fixed incl. per-MAC event index), 124 tests at stack top
