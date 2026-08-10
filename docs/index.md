---
title: iPXE Bootstrap
---

A proxy DHCP + TFTP container that network-boots bare metal into an iPXE menu,
alongside whatever DHCP server is already on the network. The iPXE binaries are
compiled from pinned upstream source with the chain URL and a set of trusted
root-CA fingerprints baked in, so a machine goes from power-on to a running node
without touching iPXE's own CA infrastructure or downloading a boot script from
anyone else.

Source, container image, and setup instructions:
**[github.com/fairchild/ipxe](https://github.com/fairchild/ipxe)**.

## Logbook

Development notes from getting this to work on real hardware — the failures, the
mechanism behind each one, and which of them emulation is structurally incapable
of catching.

**[Read the logbook →](logbook/)**

## Field guides

- **[Frame role](frame-role.html)** — the full camera-to-glass path for a
  RAM-only picture frame: capture, curation, credential boundaries, iPXE boot,
  authenticated media delivery, and rendering.
