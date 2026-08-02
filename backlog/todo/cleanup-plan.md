---
priority: 1
timeout: 1w
arc: closed-loop
---

# Post-outage cleanup: what to fix, and what to accept

Written 2026-08-02 after the Pi 4 netboot outage was resolved, with an outside
advisory pass. The point of this document is as much what we are **not** doing.
Scale is a handful of machines Michael owns; most debt here is cheap to carry.

## The fulcrum

`own-boot-image-plan.md` decides the fate of a third of this list. Most of the
fragility lives in the six-hop fetch chain: it is why boot-stage beacons could
not report an apkovl failure, why three URLs had to be downgraded to plaintext,
and why boot-failure telemetry has nowhere good to live. **Instrumentation built
against Alpine's diskless init is instrumentation for an engine we have already
decided to remove.** Check work against that before starting it.

## Now — roughly a day

1. **Push and merge both branches.** Production is deployed from a feature
   branch and is ahead of `main`; this repo has previously carried unnoticed
   deploy drift for weeks. Ranked first because it is the one item that corrupts
   *future* debugging rather than present behaviour — the next incident
   otherwise opens with "what is actually deployed?".

2. **Registration policy + reset, one Worker change.** An ephemeral RAM node can
   currently register exactly once: the TOFU token lives only in RAM, so any
   reboot before assignment yields 409 and the node idles forever (observed
   live, four spoof events). This is not a persistence problem — there is
   nowhere on a RAM node to put a token. It is a policy problem: **while a
   machine is unassigned, re-registration by the same MAC should rotate the
   token rather than refuse.** TOFU's trust window should extend to assignment,
   not to first contact. Add `reset` and `delete` endpoints in the same pass;
   recovery currently requires raw `wrangler d1 execute` against production.

3. **Stall detection.** "Reached `stage=boot`, never registered, for N
   consecutive boots" is computable from data already stored, and would have
   caught this outage on day one. It survives the boot-image rewrite unchanged
   because it watches the registry, not the boot chain.

4. **Four one-liners**, folded into whichever PR carries the above — not tracked
   separately: raise the 60/min per-IP rate limit or key it on machine id (every
   lab machine shares one NAT address); dashboard card reads `last_seen` while
   the STALE badge derives from `last_checkin`; JSON 404 for unmatched `/api/*`
   instead of 200 + the HTML landing page.

## Next

5. **The boot image**, honouring its own sequencing note: understand the current
   stall before the architecture that moots it, or find the same bug twice.

6. **Arch-aware roles** — the guardrail, in its 20-line form. The framing that
   matters: this is not primarily a safety feature. Roles are not arch-aware at
   all, so an x86 Debian preseed assigned to a Pi is nonsense *before* it is
   dangerous. A `compatible_arch` (or `destructive: disk`) field on each role,
   checked in `assign` against the machine's inventoried arch and refusable only
   with `force=true`, is a correctness check that happens to be the entire
   safety fix. Warranted because the human-level control ("don't assign
   disk-install roles to the Pi") already existed and nearly fired anyway.

7. **Run the watchdog escalation against the real plug once.** An afternoon of
   testing, not development. Identify and single cycles are proven; the bounded
   escalation ladder has never executed against hardware, which is the classic
   thing that fails at 2am.

8. **Per-machine boot target** — load-bearing for the agent iteration loop, so
   do it as the first piece of that work. Standalone, at this scale, premature.

## Not doing — accepted permanently

- **Transition history / an events table.** Three timestamps plus stall
  detection is enough state for five machines. The dashboard's fabricated
  timeline is fixed by *not displaying* inferred steps, not by event-sourcing
  the registry.
- **Per-failure phone-home from iPXE `:failed` labels.** Stall detection covers
  "died and never registered"; `CONSOLE_SYSLOG` at the next iPXE rebuild covers
  "died and here is why". A third mechanism for the same question is waste.
- **Anything on the zone HTTPS hack beyond documentation.** Write the paragraph,
  verify the redirect rule actually covers `/dashboard`'s bearer token, stop.
  Revisit after the boot image, when the plaintext set shrinks to just the d-i
  preseed — that one is real and permanent, since d-i's early fetch cannot TLS.
- **Hardware profiles, capability models, role-policy engines.** See item 6.

`silence is ambiguous` is one problem with three price points (failure
phone-home, transition history, stall detection). Stall detection is the right
price; buying all three buys the same answer three times.

## One experiment, before the knowledge decays

We do not know whether the sysinit stall was modloop or an unbounded `ntpd` —
both changed at once and the fix was attributed to the wrong one. Thirty
minutes to settle it, and the boot-image design may care which.

## Process, kept deliberately small

Two durable sentences, no checklists:

- In the repo's CLAUDE.md: a comment in the boot chain once confidently
  described the wrong fetch mechanism and survived two audits — **trust source
  over comments in the boot path**, and prefer deleting a wrong mechanism
  comment to correcting it.
- In `own-boot-image-plan.md`'s Verify section, where it will be read at the
  moment it matters: **a diagnostic must not depend on the subsystem it
  observes.** Concretely, the boot-stage beacon belongs as early in the
  initramfs as a NIC exists, never in anything fetched later.

"Change one variable at a time" is not worth institutionalising; nobody reads it
at 2am.
