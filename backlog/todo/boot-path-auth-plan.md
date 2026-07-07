---
priority: 3
timeout: 2d
arc: machine-identity
---

# Close the unauthenticated nonce-minting gap on /boot.ipxe

**Repos: both.** Born from review round 2026-07-06 (PR #1071 P1, partially mitigated with rate limiting).

`GET /boot.ipxe?mac=<assigned-mac>` mints a preseed nonce for any caller who knows an assigned machine's MAC — iPXE at cold boot has no credentials, so the endpoint is anonymous by design. Rate limiting now bounds abuse, but the exposure stands: anyone who can guess/observe a MAC during the assignment window can obtain the install script + nonce (and, until [[preseed-token-delivery]] lands, the token in the preseed).

Options sketched at review time, evaluate and pick:
1. **Bootstrap-container shared secret**: container appends a per-site key to the chain URL (embedded script already parameterizes the URL via build ARG); Worker requires it for nonce-minting paths. Ties nonce minting to "request came through a trusted site's boot chain."
2. **Discovery OS drives the install**: assigned machines aren't rebooted into anonymous PXE — the discovery OS (which holds the machine token) fetches the install script/nonce over authenticated https itself, writes it, and kexecs/chains. Anonymous /boot.ipxe then only ever serves the menu + discovery.
3. Accept-with-monitoring: document the window (assignment→install), alert on nonce mints that don't culminate in a preseed fetch from the same IP.

Dependencies to respect: [[preseed-token-delivery]] shrinks what leaks through this gap — do that first.

Acceptance: chosen design implemented + tested; /about trust table updated to reflect the new hop guarantees; threat note in the PR body explaining residual exposure.

---
