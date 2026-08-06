---
priority: 2
timeout: 1w
arc: closed-loop
---

# openrc reports our healthy services as `crashed`

Found 2026-08-06 while proving the stall detector against hardware.

`discovery-clock` and `discovery-sshd` both end `start()` by launching a daemon
with `start-stop-daemon --background` and returning. openrc has no supervised
process left to point at, so `rc-status` shows both as `crashed` on a node that
is working perfectly — heartbeat flowing, dropbear serving, clock correct.

Two costs, and the second is the expensive one.

A real crash in either service is indistinguishable from this permanent false
one, so `rc-status` cannot be used to answer "is this node healthy". That is
merely misleading.

The expensive one: **`need` on a service openrc considers crashed is
unsatisfiable, and openrc skips the dependent silently.** A test wedge declared
`need discovery-sshd`; openrc dropped the wedge without a word and the boot came
up entirely healthy. A service that never runs and a service that runs and
succeeds are the same observation from outside. That is the project's recurring
failure shape — the instrument reporting success because it never executed.

## The fix

Give openrc a pidfile to supervise, which is what it wants:

```sh
start() {
    start-stop-daemon --start --background --make-pidfile \
        --pidfile /run/discovery-heartbeat.pid \
        --exec /usr/local/bin/discovery-heartbeat
}
```

`discovery-heartbeat` already maintains `/run/discovery-heartbeat.pid` for its
own single-instance guard, so the two must agree on who writes it — let
`start-stop-daemon` own the file and drop the script's `echo $$`, or keep the
script's and use `--pidfile` without `--make-pidfile`. Do not end up with both
writing it; a stale or contested pidfile turns the single-instance guard into
the flood it exists to prevent.

Verify by mutation, not by reading `rc-status` once: stop the daemon by hand and
confirm openrc *then* reports crashed. A status that reads correct on a healthy
node proves nothing — that is how this shipped in the first place.

## Related

`discovery-sshd` also declares `after net local`, so ssh arrives after `local`.
Anything blocking before `local` costs the shell as well. Worth a comment where
someone writing a new service will read it, since the natural instinct is to
assume ssh is available early.
