#!/usr/bin/env python3
"""Frame display loop: poll a manifest, hold the photo set in RAM, and put the
current image on every display this machine has.

The pipeline is source -> compose -> sink, and both ends are pluggable:

Source. If the boot's role-ack delivered a config (/tmp/role-config.json,
written by discovery.start; RAM-only, dies with the boot), its `source` is
the manifest URL and its `token` rides as a bearer on every fetch — this is
how the frame pulls from an authenticated photo service (trips) with iPXE
acting only as the credential courier, never hosting a photo. With no config
the frame falls back to the Worker's own /frames/ test cards, so a
misconfigured frame shows placeholder cards rather than a black screen.
Contract: GET <source> -> {"images":[{"name","url"?,"sha256"?}...]}; each
image fetched from `url` resolved against the manifest URL, same header.

The whole set is prefetched into RAM: after one successful poll the frame
rotates locally and survives network loss for the life of the boot — a
manifest poll failure logs and keeps showing the cached set.

Sinks. The HDMI framebuffer (/dev/fb0, raw blit, repainted every pass since
the console scribbles on the shared surface), the Inky e-ink (image-change
only — a refresh costs half a minute), and a preview PNG when no display
exists (same pipeline minus the device write; the ssh-testable surface).

Control. Each pass opens by trading a status report for the config the
operator currently wants (POST /api/machines/<id>/checkin, carrying this
boot's token from the role-ack), which is how a config change reaches a live
frame within one poll instead of at next reboot. It is strictly optional: no
token means no exchange and the loop behaves as it did before one existed,
and any failure is logged once per streak and otherwise ignored.

Stdlib + Pillow + numpy. The inky library (pip-only) is optional at runtime.
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
from urllib.parse import urljoin

POLL_SECONDS = int(os.environ.get("FRAME_POLL", "300"))
ROTATE_SECONDS = int(os.environ.get("FRAME_ROTATE", "1800"))
PREVIEW_PATH = os.environ.get("FRAME_PREVIEW", "/tmp/frame-preview.png")
CONFIG_PATH = os.environ.get("FRAME_CONFIG", "/tmp/role-config.json")
TOKEN_PATH = os.environ.get("FRAME_TOKEN", "/tmp/machine-token")
MACHINE_ID_PATH = os.environ.get("FRAME_MACHINE_ID", "/tmp/machine-id")
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
    # discovery-clock runs before modloop and the frame service, so TLS is
    # available here. Never downgrade the rotating bearer token to cleartext.
    return base.rstrip("/")


def load_config():
    """The role config the ack delivered, or None. Read every pass: the ack
    runs in parallel with this service's start, so the file can appear after
    the first pass — and an operator token rotation lands at next boot the
    same way."""
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else None
    except (OSError, ValueError):
        return None


def read_line(path: str) -> str | None:
    """A one-line file discovery.start wrote, or None if this boot has none."""
    try:
        with open(path) as f:
            return f.read().strip() or None
    except OSError:
        return None


def uptime_seconds() -> int:
    """Monotonic and right on the first tick, which is what an RTC-less board
    can honestly report about how long this boot has been running."""
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except (OSError, ValueError, IndexError):
        return 0


def wire_sink(name: str) -> str:
    """The Worker's name for a sink. Only the e-ink differs — it is named for
    the hardware on the wire and for its place in the pipeline here."""
    return "inky" if name == "panel" else name


def image_status_id(raw: bytes) -> str:
    """A content identifier the control plane can show without receiving the
    private manifest filename."""
    return hashlib.sha256(raw).hexdigest()


# A named User-Agent, because the zone 403s urllib's default one
# (Python-urllib/3.x reads as a bot upstream). Verified on hardware.
USER_AGENT = "frame-display/1"


def fetch(url: str, token: str | None = None, timeout: int = 30) -> bytes:
    headers = {"User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def post_json(url: str, token: str, payload: dict, timeout: int = 10):
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    }
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read())


class ControlPlane:
    """The frame's half of the control loop: trade a status report for the
    config the operator currently wants this machine to run.

    The token is minted by the role-ack and written to tmpfs by discovery.start.
    With no token or no id there is no exchange at all. It rides the same
    authenticated HTTPS control plane as the boot ack. Its RAM copy dies with
    this boot; the server accepts it until the next successful role-ack rotates
    the stored hash, or an operator resets/deletes the machine. It is still a
    bearer token and must not cross the network in cleartext.

    Failure is soft and reported once per consecutive-failure streak. At a
    300 s cadence a night of unreachable control plane would otherwise write
    a few hundred identical lines onto the console, which shares the glass
    with the picture.
    """

    def __init__(self, base: str) -> None:
        self.url_base = base
        self.failures = 0

    def exchange(self, status: dict) -> None:
        token = read_line(TOKEN_PATH)
        machine_id = read_line(MACHINE_ID_PATH)
        if not token or not machine_id:
            return
        try:
            body = post_json(
                f"{self.url_base}/api/machines/{machine_id}/checkin",
                token,
                {"status": status},
            )
            # An older Worker omits config entirely; that means "no refresh
            # support", not "clear the file". The current Worker sends null
            # explicitly when the operator cleared a RAM role's config.
            if isinstance(body, dict) and "config" in body:
                config = body["config"]
                current = load_config()
                # Compare against the file rather than the loop's copy: the
                # file is what the loop adopts from, so this cannot re-adopt
                # what the ack already delivered on the first pass.
                if isinstance(config, dict) and config != current:
                    self.adopt(config)
                elif config is None and current is not None:
                    self.clear()
        except Exception as exc:  # noqa: BLE001 — the display outranks this
            self.failures += 1
            if self.failures == 1:
                log(
                    f"checkin failed ({exc.__class__.__name__}: {exc}); "
                    "rendering continues"
                )
            return
        if self.failures:
            log(f"checkin recovered after {self.failures} failed passes")
            self.failures = 0

    def adopt(self, config: dict) -> None:
        """Hand a new config to the loop the way the ack does — by writing the
        file it re-reads every pass, so adoption and the cache reset ride the
        path that already exists. 0600 because it carries the photo source's
        bearer token, and this box keeps a getty on tty1."""
        fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(config, f)
        # O_CREAT's mode applies only on create; the ack may have made it.
        os.chmod(CONFIG_PATH, 0o600)
        log("checkin delivered a new role config; adopting it this pass")

    def clear(self) -> None:
        """Remove the RAM config only on the Worker's explicit null signal."""
        try:
            os.unlink(CONFIG_PATH)
        except FileNotFoundError:
            return
        log("checkin cleared the role config; using the fallback this pass")


class FramebufferSink:
    """The HDMI console framebuffer. vc4 comes from modloop at sysinit, so on
    a Pi with a monitor this exists before the loop ever starts. 16bpp packs
    as RGB565-LE via numpy (modern Pillow removed its BGR;16 packer), 32bpp
    as XRGB (raw "BGRX")."""

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
            import numpy  # noqa: F401 — fail the probe early if absent
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
    fallback = f"{base}/frames/manifest.json"
    control = ControlPlane(base)
    sinks: dict[str, object] = {}
    probe_logged: set[str] = set()
    shown: dict[str, str] = {}
    config = None
    images: list[str] = []
    cache: dict[str, bytes] = {}
    showable: list[str] = []
    current: str | None = None
    current_sha256: str | None = None
    current_sink: str | None = None
    manifest_ok = False

    log(f"loop starting — fallback source {fallback}, rotate {ROTATE_SECONDS}s")
    while True:
        try:
            # First, so a config the operator changed since the last pass is
            # the one this pass renders. The status describes the pass just
            # finished — five short fields, far under the 2048-byte cap.
            control.exchange(
                {
                    # The filename belongs to the private source. A digest is
                    # enough to correlate the dashboard with a manifest entry.
                    "image_sha256": current_sha256,
                    "sink": current_sink,
                    "images": len(showable),
                    "ok": manifest_ok,
                    "up": uptime_seconds(),
                }
            )

            new_config = load_config()
            if new_config != config:
                config = new_config
                images, cache, shown = [], {}, {}
                log(
                    "source: "
                    + (config.get("source", "?") if config else f"{fallback} (fallback)")
                )
            manifest_url = (config or {}).get("source") or fallback
            token = (config or {}).get("token")

            # Probe for sinks that aren't up yet, every pass: device nodes and
            # libraries can appear after this process starts. A sink that
            # appears mid-run gets the current image immediately.
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
                        log(f"no {sink_type.name} ({exc.__class__.__name__}: {exc})")
            active = list(sinks.values()) or [PreviewSink()]
            current_sink = wire_sink(active[0].name)

            # Poll the manifest and prefetch the whole set into RAM. Failure
            # here is logged and non-fatal: the frame keeps rotating through
            # whatever it already holds — the network is needed to *change*
            # the set, not to display it.
            try:
                manifest = json.loads(fetch(manifest_url, token, timeout=15))
                entries = [
                    i for i in manifest.get("images", []) if i.get("name")
                ]
                images = [i["name"] for i in entries]
                for entry in entries:
                    name = entry["name"]
                    if name in cache:
                        continue
                    raw = fetch(urljoin(manifest_url, entry.get("url") or name), token)
                    want = entry.get("sha256")
                    if want and hashlib.sha256(raw).hexdigest() != want:
                        log(f"{name}: digest mismatch (truncated fetch?); skipping")
                        continue
                    cache[name] = raw
                for gone in set(cache) - set(images):
                    del cache[gone]
                manifest_ok = True
            except Exception as exc:  # noqa: BLE001 — keep showing the cached set
                manifest_ok = False
                log(
                    f"manifest poll failed ({exc.__class__.__name__}: {exc}); "
                    f"showing cached set of {len(cache)}"
                )

            showable = [n for n in images if n in cache]
            if showable:
                # Rotation is a function of the clock, not local state, so
                # every frame in a fleet shows the same picture and a reboot
                # lands on the schedule instead of restarting it.
                current = showable[int(time.time() / ROTATE_SECONDS) % len(showable)]
                current_sha256 = image_status_id(cache[current])
                for sink in active:
                    if shown.get(sink.name) == current and not getattr(
                        sink, "repaint_always", False
                    ):
                        continue
                    if shown.get(sink.name) != current:
                        # Log before painting, so the console scribble this
                        # line causes lands under the fresh image, not on it.
                        log(f"{sink.name}: displaying {current}")
                    sink.show(compose(cache[current], sink.resolution))
                    shown[sink.name] = current
        except Exception as exc:  # noqa: BLE001 — the loop must survive anything
            log(f"loop error ({exc.__class__.__name__}: {exc}); continuing")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    sys.exit(main())
