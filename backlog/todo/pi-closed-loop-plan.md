---
priority: 0
timeout: 2w
arc: closed-loop
---

# Closed-loop Pi provisioning: control plane, observability, out-of-band recovery

**Spans both repos.** Opened 2026-07-31 after a night of hand-debugging one Pi 4
with a phone camera pointed at an HDMI monitor.

## Intent

Get to a loop an agent can run unattended: change what a machine boots →
make it boot → observe what happened → decide → repeat. Today every one of
those four steps needs Michael physically present, and step three is a
photograph.

The target is not "a nicer dashboard." It is that the system can tell you
*why* a machine stopped, and can act on it without hands.

## The structural gap

Every signal the system has is emitted by the machine over the network. The
failure mode we actually hit — a NIC that never comes up — is the one case
where the machine cannot report anything. The dashboard's last word is
`stage=boot target=discovery-arm64`, then silence, and silence is currently
indistinguishable from "powered off", "kernel panic", "sitting happily at a
rescue shell", and "never plugged in".

Two independent fixes, and we want both:

- **Infer from absence.** The Worker knows a `stage=boot` check-in should be
  followed by a registration within ~2 minutes. When it isn't, that is a
  computable fact, not a mystery. Converts silence into a signal using data
  already stored.
- **A channel that survives a dead NIC.** Serial. Nothing else in the stack
  works when the network doesn't.

## Workstreams

### A. Control plane — the admin panel an agent can drive

The dashboard (`services/ipxe/public/dashboard.html`, live, DASHBOARD_TOKEN)
already does lifecycle board, health tiles, boot feed, machine detail. What
it cannot do is let anything *change* a machine's boot behaviour except
assigning a role from `discovered`/`pending`.

Known or suspected gaps — under audit:
- No per-machine boot-target override; the menu is global per-arch. An
  iteration loop needs "this MAC boots this thing next".
- No state reset, so a machine that advanced can't be re-assigned.
- No delete/re-register for a stale or wrongly-registered MAC.
- No machine-readable "why did this stop" surface.

Everything here must be API-first and token-gated, because the agent and the
human drive the same surface. Follow the existing conditional-write
discipline (see the `assign` handler's comment about a machine advancing
itself between read and write).

### B. Observability

- **Stall detection.** Derived `stalled` state from expected-next-event
  deadlines. No new telemetry, no schema change. Highest value per unit work.
- **Boot-attempt correlation id.** Minted at `stage=menu`, carried through, so
  five reboots in an evening read as five attempts and not one blurred stream.
- **Serial capture.** `console=ttyAMA0` shipped (commit 15bf8036) — that was a
  precondition, since arm64 nodes were pointing the console at a device that
  does not exist. Still needs a capture host.
- **iPXE syslog console.** `CONSOLE_SYSLOG` in `config/console.h` ships all
  iPXE console output to a remote collector — covers the pre-Linux stage that
  serial-from-Linux can't. Cheap and worth doing.
- **Camera** as the honest last resort for firmware-stage failures that
  predate every other channel (UEFI setup, EEPROM).

### C. Out-of-band recovery

Meross MSS620 (two independently switchable outlets, 2.4GHz, no Matter, no
official API). Local control strongly preferred — a recovery tool that needs
the WAN fails exactly when it's needed.

Policy matters more than plumbing: **cycle, then escalate.** A deterministic
failure (ACPI-mode Ethernet) does not get better on the fifth power cut. N
consecutive health-check failures → one cycle → boot timeout → exponential
backoff → hard cap → alert a human and stop. Never flap.

Preconditions: outlet power-on-state forced ON, `POWER_OFF_ON_HALT=1` so a
clean halt is distinguishable from a crash, and the card read-only at runtime
because hard power cuts risk FAT corruption.

### D. Pi boot hardening

- **Devicetree by default.** The standing blocker to zero-touch. pftf keeps
  all UEFI settings inside `RPI_EFI.fd` on the card's own FAT partition, has
  no `config.txt` knob for it, and `virt-fw-vars` can't edit the combined
  CODE+VARS layout. So: golden `.fd` (verify the MAC-cloning risk first) or
  write the EDK2 varstore ourselves. See memory `pi-uefi-nvram-in-fd`.
- **Boot script layering.** Cloud menu → LAN fallback → local menu. Blocked on
  the EMBED-vs-`autoexec.ipxe` question (under verification): if a compiled-in
  embedded script means `autoexec.ipxe` is registered but never executed, the
  layering has to live in the embedded script or chain explicitly.
- **`config.txt`**: `enable_uart=1`, `uart_2ndstage=1` for firmware-stage
  serial. **EEPROM**: `BOOT_ORDER=0xf21` (SD then network), `BOOT_UART=1`,
  `POWER_OFF_ON_HALT=1`, `FREEZE_VERSION=1` once stable.
- **Cloned spare card** — the cheapest recovery path there is.

## Sequencing

A and B are software and can move now. C and D need hardware decisions and
one-time physical setup. D's Devicetree fix is what makes any of the
automation meaningful — until it lands, every power cycle returns the Pi to
the same broken state.

## Open questions

- Where the always-on controller lives (the existing LAN relay host is the
  obvious candidate) — it needs to host serial capture, the LAN artifact
  mirror, and the watchdog.
- Whether to mirror boot artifacts on the LAN as a cloud-outage fallback, and
  whether that reopens the trust surface ADR 0001/0002 deliberately closed.
- Serial capture hardware: USB-TTL on the GPIO UART is the real answer;
  HDMI capture or a camera covers firmware stages nothing else reaches.

## Verify

A machine can be power-cycled, boot to a known target, register, and be
re-targeted — all through the API, with no one in the room; and when it fails
instead, the dashboard says at which stage and the logs say why.
