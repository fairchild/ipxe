#!/usr/bin/env python3
"""Frame display loop: poll a manifest, fetch the current image, put it on the
panel — or, with no panel driveable, render what *would* be shown to a PNG.

The dry-run path is not a stub; it is the verification surface. Everything
except the final SPI write — manifest polling, image fetch, resize, letterbox
— runs identically with and without glass, so a frame boot is testable over
ssh before the panel is ever attached, and debuggable after (the preview PNG
is what the panel should be showing).

Fetch URLs are plain HTTP like the heartbeat's, but the zone's https-redirect
exemptions cover only the boot-critical paths — /frames/ 301s to https and
urllib follows it (ca-certificates comes from ensure_deps). That is
acceptable here and nowhere earlier in the boot: this loop runs in the
default runlevel, after the clock service, so TLS's clock dependency is
already satisfied — and a failed fetch retries every poll rather than
wedging a boot. The manifest's sha256 entries guard against truncated
fetches, not adversaries.

Stdlib + Pillow only. The inky library (pip-only) is optional at runtime:
present and a panel answers -> real refreshes; otherwise dry-run.
"""

import hashlib
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
DEFAULT_RESOLUTION = (800, 480)


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
    with urllib.request.urlopen(url, timeout=timeout) as res:
        return res.read()


_panel_probe_logged = False


def get_panel():
    """The real panel, or None for dry-run. EEPROM auto-detect first — the
    fleet has two different panels, which is the case auto-detect exists for.
    Called again each loop pass while None: device nodes can appear after this
    process starts, and a one-shot probe would demote a working panel to
    dry-run for the life of the boot over a race it would win seconds later.
    """
    global _panel_probe_logged
    try:
        from inky.auto import auto  # type: ignore

        panel = auto()
        log(f"panel: {type(panel).__name__} {panel.resolution}")
        _panel_probe_logged = False
        return panel
    except Exception as exc:  # noqa: BLE001 — any failure here means dry-run
        if not _panel_probe_logged:
            log(f"no driveable panel ({exc.__class__.__name__}: {exc}); dry-run")
            _panel_probe_logged = True
        return None


def compose(raw: bytes, resolution: tuple[int, int]):
    """Letterbox onto the panel's canvas. White bars, not crops: a picture
    frame should show the photograph, not a guess about its edges."""
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(raw)).convert("RGB")
    img.thumbnail(resolution)
    canvas = Image.new("RGB", resolution, (255, 255, 255))
    canvas.paste(
        img,
        ((resolution[0] - img.width) // 2, (resolution[1] - img.height) // 2),
    )
    return canvas


def main() -> None:
    base = api_base()
    frames = f"{base}/frames"
    panel = get_panel()
    shown: str | None = None

    log(f"loop starting — source {frames}, rotate {ROTATE_SECONDS}s")
    while True:
        try:
            if panel is None:
                panel = get_panel()
                if panel is not None:
                    # A panel that just appeared should get the current image
                    # now, not at the next rotation boundary.
                    shown = None
            resolution = panel.resolution if panel else DEFAULT_RESOLUTION
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
                if current != shown:
                    raw = fetch(f"{frames}/{current}")
                    want = digests.get(current)
                    got = hashlib.sha256(raw).hexdigest()
                    if want and got != want:
                        log(f"{current}: digest mismatch (truncated fetch?); skipping")
                    else:
                        canvas = compose(raw, resolution)
                        if panel:
                            panel.set_image(canvas)
                            panel.show()  # blocking; 30-45s on 7-colour panels
                            log(f"displayed {current}")
                        else:
                            canvas.save(PREVIEW_PATH)
                            log(f"dry-run rendered {current} -> {PREVIEW_PATH}")
                        shown = current
        except Exception as exc:  # noqa: BLE001 — the loop must survive anything
            log(f"loop error ({exc.__class__.__name__}: {exc}); continuing")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
