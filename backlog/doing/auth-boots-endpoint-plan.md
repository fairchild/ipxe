---
priority: 1
timeout: 4h
arc: security-debts
---

# Auth on /api/boots and cheap fleet counting

**Repo: `~/code/services/ipxe`** (Cloudflare Worker, Hono, bun; tests via `bun run test`).

`src/routes/checkin.ts` exposes `GET /api/boots` with no auth and no rate limit. It lists fleet MACs, internal IPs, and boot history publicly, and calls `countPrefix()` — a full KV list scan over up to 90 days of `bootrev:` keys — on every request. Information leak + cost amplifier.

Fix:
1. Require a bearer token on `/api/boots`: `Authorization: Bearer <DASHBOARD_TOKEN>`, `DASHBOARD_TOKEN` as a Worker secret (document `wrangler secret put DASHBOARD_TOKEN`; in dev, `.dev.vars`). Constant-time comparison. 401 without it.
2. The `/pi` dashboard (public/pi.html) calls this endpoint — thread the token (prompt-and-localStorage is fine for a personal dashboard); default posture: protect the API, let the static page load but show nothing without a token.
3. Replace per-request `countPrefix` scans with maintained counters: KV keys `count:total` and `count:<date>` incremented in `storeBootEvent` (KV increments race; approximate counts are fine for a dashboard — note in a comment). No full-prefix scan per request.
4. Extend the existing rate limiter to cover `/api/boots`.
5. Tests: 401 without token, 200 with, counts present. Follow existing Vitest patterns in `test/`.

Do not break `GET /api/checkin` — iPXE calls it unauthenticated by design at boot time (identity arrives via first-boot-token-plan).

Outcome: merge-ready PR against the services repo. Deploy (`wrangler deploy` + secret) is Michael's step; say so in the PR body.

---
- 2026-07-04T18:24:11Z advanced to=doing claimer=fairchild@blue branch=main
- 2026-07-04T18:24:51Z progress | pin-ipxe-binaries: agent dispatched in worktree, branch fix/pin-ipxe-binaries
