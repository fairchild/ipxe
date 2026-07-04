---
priority: 2
timeout: 2d
arc: machine-identity
dependencies:
  auth-boots-endpoint-plan: "touches the same checkin.ts routes; land the security fix first to avoid conflicts"
---

# First-boot token identity + D1 machine registry

**Repo: `~/code/services/ipxe`.**

Registration is about to drive provisioning decisions, so MAC-as-identity over unauthenticated GET must become TOFU (trust on first use): the first check-in from a new MAC mints a per-machine token; every later state-changing interaction requires it.

Design (agreed 2026-07-04):
1. **D1 registry** replacing KV `device:<mac>` as source of truth for machines (KV `bootrev:` event log stays). Schema: `machines(id TEXT PRIMARY KEY, mac TEXT UNIQUE, token_hash TEXT, arch TEXT, site TEXT, state TEXT CHECK(state IN ('discovered','pending','assigned','installing','active')), role TEXT, hw_inventory TEXT /*json*/, first_seen TEXT, last_seen TEXT)`. Wrangler D1 binding + migration file; document `wrangler d1 create` / `migrations apply` as Michael's deploy steps.
2. **Token mint**: on register from an unknown MAC, generate a random token (crypto.getRandomValues, ≥128 bits, base32/hex), store only its SHA-256 hash, return plaintext once. Known MAC + valid token → update last_seen/state. Known MAC + missing/invalid token → record the event, do NOT touch the machine row (spoof attempts visible but harmless).
3. **Endpoints**: keep `GET /api/checkin` compatible (stage=ipxe, no token — pre-OS can't hold secrets; updates event log only). Add `POST /api/machines/register` (discovery OS: mac + hw inventory JSON → { id, token } if new), `POST /api/machines/:id/checkin` (token-authed state updates), and DASHBOARD_TOKEN-authed `GET /api/machines` for the dashboard.
4. Tests: mint-on-first-contact, hash-not-plaintext stored, spoofed MAC can't overwrite, state transitions, register idempotency (re-register of known MAC without token returns 409 or similar, not a new token).

Keep the diff reviewable: registry + tokens only — no preseed serving, no role UI (that's role-assign-preseed-plan).

Outcome: merge-ready PR against the services repo; PR body lists the wrangler D1 setup commands.

---
- 2026-07-04T18:31:48Z advanced to=doing claimer=fairchild@blue branch=main
- 2026-07-04T18:32:30Z progress | agent dispatched, stacked worktree on fix/ipxe-boots-auth, branch feat/ipxe-machine-registry
