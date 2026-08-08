#!/usr/bin/env python3
"""Frame display loop: poll a manifest, fetch the current image, and put it on
every display this machine has.

The pipeline is fetch -> compose -> sink, and sinks are independent: the HDMI
framebuffer (/dev/fb0, the vc4 console — a raw Pillow blit, no X) and the
Pimoroni Inky panel (SPI e-ink via the inky library) are both just sinks, each
composed at its own resolution. A frame with only a monitor shows on the
monitor; only a panel, on the panel; both, on both. With no sink at all the
loop renders to a preview PNG — not a stub but the verification surface:
everything except the final device write runs identically, so a frame boot is
testable over ssh before any display exists and debuggable after.

Fetch URLs are plain HTTP like the heartbeat's, but the zone's https-redirect
exemptions cover only the boot-critical paths — /frames/ 301s to https and
urllib follows it (ca-certificates comes from ensure_deps). That is
acceptable here and nowhere earlier in the boot: this loop runs in the
default runlevel, after the clock service, so TLS's clock dependency is
already satisfied — and a failed fetch retries every poll rather than
wedging a boot. The manifest's sha256 entries guard against truncated
fetches, not adversaries.

Stdlib + Pillow only. The inky library (pip-only) is optional at runtime:
present and a panel answers -> real e-ink refreshes; otherwise that sink is
simply absent.
"""

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

POLL_SECONDS = int(os.environ.get("FRAME_POLL", "300"))
ROTATE_SECONDS = int(os.environ.get("FRAME_ROTATE", "1800"))
PREVIEW_PATH = os.environ.get("FRAME_PREVIEW", "/tmp/frame-preview.png")
PREVIEW_RESOLUTION = (800, 480)


def log(msg: str) -> None:
    line = f"[frame] {msg}"
    print(line, flush=True)
    try:
        with open("/dev/console", "w") as console:
            console.write(line + "\n")
    except OSError:
        pass
    try:
        # check=False covers a nonzero exit but not a missing binary — and
        # log() runs inside the loop's except handler, so an OSError here
        # would convert every loop error into process death.
        subprocess.run(["logger", "-t", "frame", msg], check=False)
    except OSError:
        pass


def api_base() -> str:
    try:
        cmdline = open("/proc/cmdline").read()
    except OSError:
        cmdline = ""
    m = re.search(r"ipxe_api=(\S+)", cmdline)
    base = m.group(1) if m else "https://ipxe.cloudcompute.com"
    return base.rstrip("/").replace("https:", "http:", 1)


def fetch(url: str, timeout: int = 30) -> bytes:
    # A named User-Agent, because the zone 403s urllib's default one
    # (Python-urllib/3.x reads as a bot upstream). Verified on hardware:
    # identical request, default UA 403, this UA 200.
    req = urllib.request.Request(url, headers={"User-Agent": "frame-display/1"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


class FramebufferSink:
    """The HDMI console framebuffer. vc4 comes from modloop at sysinit, so on
    a Pi with a monitor this exists before the loop ever starts. 16bpp packs
    as RGB565-LE (Pillow raw "BGR;16"), 32bpp as XRGB (raw "BGRX")."""

    name = "framebuffer"
    # The console shares this surface: any service writing /dev/console
    # scribbles a text line over the picture. Repainting every poll pass is a
    # cheap blit and cleans scribbles within a poll interval — something the
    # e-ink must never do, since its refresh costs half a minute of flashing.
    repaint_always = True

    def __init__(self) -> None:
        base = "/sys/class/graphics/fb0"
        self.dev = "/dev/fb0"
        os.stat(self.dev)
        self.bpp = int(open(f"{base}/bits_per_pixel").read())
        if self.bpp not in (16, 32):
            raise ValueError(f"unsupported fb depth {self.bpp}")
        if self.bpp == 16:
            # RGB565 packing runs through numpy — modern Pillow removed its
            # legacy "BGR;16" packer (found out on hardware: "No packer
            # found"). numpy is in ensure_deps, and the inky panel needs it
            # anyway.
            import numpy  # noqa: F401
        # Visible mode ("U:1920x1080p-0"), not virtual_size — the virtual
        # buffer can be taller than the screen for console panning.
        try:
            m = re.search(r"(\d+)x(\d+)", open(f"{base}/modes").read())
            self.resolution = (int(m.group(1)), int(m.group(2)))
        except Exception:  # noqa: BLE001 — fall back to the virtual size
            w, h = open(f"{base}/virtual_size").read().split(",")
            self.resolution = (int(w), int(h))
        try:
            self.stride = int(open(f"{base}/stride").read())
        except OSError:
            self.stride = self.resolution[0] * self.bpp // 8

    def show(self, canvas) -> None:
        if self.bpp == 32:
            raw, row = canvas.tobytes("raw", "BGRX"), self.resolution[0] * 4
        else:
            import numpy as np

            rgb = np.frombuffer(canvas.tobytes(), dtype=np.uint8)
            rgb = rgb.reshape(-1, 3).astype(np.uint16)
            packed = (
                ((rgb[:, 0] >> 3) << 11)
                | ((rgb[:, 1] >> 2) << 5)
                | (rgb[:, 2] >> 3)
            )
            raw, row = packed.astype("<u2").tobytes(), self.resolution[0] * 2
        if self.stride > row:
            pad = b"\x00" * (self.stride - row)
            raw = b"".join(
                raw[i * row : (i + 1) * row] + pad
                for i in range(self.resolution[1])
            )
        with open(self.dev, "wb") as fb:
            fb.write(raw)


class PanelSink:
    """The Inky e-ink panel, via EEPROM auto-detect — the fleet has two
    different panels, which is the case auto-detect exists for."""

    name = "panel"

    def __init__(self) -> None:
        from inky.auto import auto  # type: ignore

        self.panel = auto()
        self.resolution = self.panel.resolution

    def show(self, canvas) -> None:
        self.panel.set_image(canvas)
        self.panel.show()  # blocking; 30-45s on 7-colour panels


class PreviewSink:
    """Last resort and debugging surface: what a display would be showing."""

    name = "preview"
    resolution = PREVIEW_RESOLUTION

    def show(self, canvas) -> None:
        canvas.save(PREVIEW_PATH)


SINK_TYPES = (FramebufferSink, PanelSink)


def compose(raw: bytes, resolution: tuple[int, int]):
    """Letterbox onto the sink's canvas. Black bars, not crops: a picture
    frame should show the photograph, not a guess about its edges."""
    from PIL import Image

    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img.thumbnail(resolution)
    canvas = Image.new("RGB", resolution, (0, 0, 0))
    canvas.paste(
        img,
        ((resolution[0] - img.width) // 2, (resolution[1] - img.height) // 2),
    )
    return canvas


def main() -> None:
    base = api_base()
    frames = f"{base}/frames"
    sinks: dict[str, object] = {}
    probe_logged: set[str] = set()
    shown: dict[str, str] = {}
    cached: tuple[str, bytes] | None = None

    log(f"loop starting — source {frames}, rotate {ROTATE_SECONDS}s")
    while True:
        try:
            # Probe for sinks that aren't up yet, every pass: device nodes and
            # libraries can appear after this process starts, and a one-shot
            # probe would demote a working display to nothing over a race it
            # would win seconds later. A sink that appears mid-run gets the
            # current image immediately (its `shown` entry is empty).
            for sink_type in SINK_TYPES:
                if sink_type.name in sinks:
                    continue
                try:
                    sink = sink_type()
                    sinks[sink_type.name] = sink
                    probe_logged.discard(sink_type.name)
                    log(f"sink up: {sink.name} {sink.resolution}")
                except Exception as exc:  # noqa: BLE001 — absent sink
                    if sink_type.name not in probe_logged:
                        probe_logged.add(sink_type.name)
                        log(
                            f"no {sink_type.name} "
                            f"({exc.__class__.__name__}: {exc})"
                        )
            active = list(sinks.values()) or [PreviewSink()]

            manifest = json.loads(fetch(f"{frames}/manifest.json", timeout=15))
            images = [i["name"] for i in manifest.get("images", []) if i.get("name")]
            digests = {
                i["name"]: i.get("sha256")
                for i in manifest.get("images", [])
                if i.get("name")
            }
            if not images:
                log("manifest has no images; waiting")
            else:
                # Rotation is a function of the clock, not local state, so every
                # frame in a fleet shows the same picture at the same time and a
                # reboot lands on the schedule instead of restarting it.
                current = images[int(time.time() / ROTATE_SECONDS) % len(images)]
                stale = [
                    s
                    for s in active
                    if shown.get(s.name) != current
                    or getattr(s, "repaint_always", False)
                ]
                if stale and (cached is None or cached[0] != current):
                    raw = fetch(f"{frames}/{current}")
                    want = digests.get(current)
                    got = hashlib.sha256(raw).hexdigest()
                    if want and got != want:
                        log(f"{current}: digest mismatch (truncated fetch?); skipping")
                        stale = []
                    else:
                        cached = (current, raw)
                for sink in stale:
                    if shown.get(sink.name) != current:
                        # Log before painting, so the console scribble this
                        # line causes lands under the fresh image, not on it.
                        log(f"{sink.name}: displaying {current}")
                    sink.show(compose(cached[1], sink.resolution))
                    shown[sink.name] = current
        except Exception as exc:  # noqa: BLE001 — the loop must survive anything
            log(f"loop error ({exc.__class__.__name__}: {exc}); continuing")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
