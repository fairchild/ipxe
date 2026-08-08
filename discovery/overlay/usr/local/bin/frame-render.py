#!/usr/bin/env python3
"""Frame display loop: poll a manifest, fetch the current image, put it on the
panel — or, with no panel driveable, render what *would* be shown to a PNG.

The dry-run path is not a stub; it is the verification surface. Everything
except the final SPI write — manifest polling, image fetch, resize, letterbox
— runs identically with and without glass, so a frame boot is testable over
ssh before the panel is ever attached, and debuggable after (the preview PNG
is what the panel should be showing).

Fetches ride plain HTTP like the heartbeat: the images are public, the node is
trust-on-LAN by the apkovl's own standard, and a display that needs TLS is
dark exactly when the clock is wrong. The manifest's sha256 entries guard
against truncated fetches, not adversaries.

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
CACHE_DIR = "/tmp/frames"
DEFAULT_RESOLUTION = (800, 480)


def log(msg: str) -> None:
    line = f"[frame] {msg}"
    print(line, flush=True)
    try:
        with open("/dev/console", "w") as console:
            console.write(line + "\n")
    except OSError:
        pass
    subprocess.run(["logger", "-t", "frame", msg], check=False)


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


def get_panel():
    """The real panel, or None for dry-run. EEPROM auto-detect first — the
    fleet has two different panels, which is the case auto-detect exists for."""
    try:
        from inky.auto import auto  # type: ignore

        panel = auto()
        log(f"panel: {type(panel).__name__} {panel.resolution}")
        return panel
    except Exception as exc:  # noqa: BLE001 — any failure here means dry-run
        log(f"no driveable panel ({exc.__class__.__name__}: {exc}); dry-run")
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
    os.makedirs(CACHE_DIR, exist_ok=True)
    panel = get_panel()
    resolution = panel.resolution if panel else DEFAULT_RESOLUTION
    shown: str | None = None

    log(f"loop starting — source {frames}, rotate {ROTATE_SECONDS}s")
    while True:
        try:
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
