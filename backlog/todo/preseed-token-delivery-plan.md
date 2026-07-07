---
priority: 2
timeout: 1d
arc: machine-identity
---

# Move machine-token delivery out of the plaintext preseed

**Repo: `~/code/services`, ipxe/ subdir.** Born from review round 2026-07-06 (PR #1071 thread, commits 8346cc38/8da2d50b).

Review correctly found that debian-installer's early preseed fetch cannot do TLS, so `preseed/url` was downgraded to http. Consequence nobody should live with long-term: the rendered preseed *contains the freshly rotated machine token*, now traveling plaintext across the WAN (Cloudflare edge → home LAN). The single-use nonce limits replay but not sniffing.

Fix shape: keep the preseed itself on http (d-i constraint is real), but strip the token from it. The preseed's `late_command` runs in-target where full TLS is available — have it `curl https://.../api/machines/:id/token?n=<second-nonce>` (single-use, short TTL, minted alongside the preseed nonce) and write the token to disk itself. Token then never crosses the wire unencrypted.

Also verify/document the Cloudflare zone caveat the http downgrade created ("Always Use HTTPS" would 301 the http preseed fetch back to https and break d-i — check the zone setting for ipxe.cloudcompute.com and document or scope an exception path).

Acceptance: preseed renders with no token in it; token fetch is https + single-use + state-conditional; QEMU or real-hardware d-i run confirms the flow; tests for the new endpoint incl. nonce reuse and wrong-state refusals.

---
