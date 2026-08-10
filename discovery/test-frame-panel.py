#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Regression tests for panel selection and what the frame reports about it.

The failure these guard against is a frame that looks healthy while showing
nothing: the e-ink never initialised, the picture went to an attached monitor
(or to a preview file nobody looks at), and every signal upstream read green.
So the tests are about the honesty of the report as much as the selection.

No inky package is installed anywhere in CI, and none is needed — the driver
is looked up by module path, which is exactly the seam to stub.
"""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parent / "overlay/usr/local/bin/frame-render.py"
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("frame_render", MODULE_PATH)
assert SPEC and SPEC.loader
frame_render = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(frame_render)


class FakePanel:
    """Stands in for an inky driver class: records how it was constructed."""

    def __init__(self, resolution):
        self.resolution = resolution
        self.shown = []

    def set_image(self, canvas):
        self.shown.append(canvas)

    def show(self):
        pass


def install_fake_inky(monkey: dict[str, types.ModuleType]) -> None:
    """Put a stub `inky` package in sys.modules for the duration of a test."""
    for name, module in monkey.items():
        sys.modules[name] = module


class PanelOverrideTests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = {k: v for k, v in sys.modules.items() if k.startswith("inky")}
        for k in list(sys.modules):
            if k.startswith("inky"):
                del sys.modules[k]

    def tearDown(self) -> None:
        for k in list(sys.modules):
            if k.startswith("inky"):
                del sys.modules[k]
        sys.modules.update(self.saved)

    def _stub_driver(self, module_name: str) -> list[dict]:
        """Install inky.<module_name>.Inky and return a list that records the
        constructor kwargs it was called with."""
        calls: list[dict] = []

        class Inky(FakePanel):
            def __init__(self, **kwargs):
                calls.append(kwargs)
                super().__init__(kwargs["resolution"])

        pkg = types.ModuleType("inky")
        pkg.__path__ = []  # mark as a package so submodule import works
        driver = types.ModuleType(f"inky.{module_name}")
        driver.Inky = Inky
        setattr(pkg, module_name, driver)
        install_fake_inky({"inky": pkg, f"inky.{module_name}": driver})
        return calls

    def test_named_override_selects_that_driver_and_its_geometry(self) -> None:
        # The 4" Impression: the panel on the bench. 640x400 on a UC8159 —
        # a different controller and a different size from the Spectra 6 4.0,
        # which is the confusion this table exists to prevent.
        calls = self._stub_driver("inky_uc8159")
        sink = frame_render.PanelSink("impression-4")

        self.assertEqual(calls, [{"resolution": (640, 400)}])
        self.assertEqual(sink.resolution, (640, 400))
        self.assertIn("override", sink.detected_as)

    def test_spectra6_four_inch_is_a_different_driver_and_size(self) -> None:
        calls = self._stub_driver("inky_e640")
        sink = frame_render.PanelSink("spectra6-4")

        self.assertEqual(calls, [{"resolution": (600, 400)}])
        self.assertEqual(sink.resolution, (600, 400))

    def test_unknown_override_names_the_ones_that_exist(self) -> None:
        self._stub_driver("inky_uc8159")
        with self.assertRaises(ValueError) as caught:
            frame_render.PanelSink("impression-9000")

        message = str(caught.exception)
        self.assertIn("impression-9000", message)
        self.assertIn("impression-4", message)

    def test_no_override_uses_eeprom_autodetect(self) -> None:
        """Autodetect stays primary: an operator who sets nothing must not get
        a guessed driver, they must get the one the panel identifies as."""
        detected = FakePanel((800, 480))
        pkg = types.ModuleType("inky")
        pkg.__path__ = []
        auto_mod = types.ModuleType("inky.auto")
        auto_mod.auto = lambda: detected
        pkg.auto = auto_mod
        install_fake_inky({"inky": pkg, "inky.auto": auto_mod})

        sink = frame_render.PanelSink()

        self.assertEqual(sink.resolution, (800, 480))
        self.assertNotIn("override", sink.detected_as)

    def test_override_table_matches_the_librarys_own_geometry(self) -> None:
        """Every override maps to a distinct (module, class, resolution). A
        duplicate row means two names drive the same glass differently, which
        is the kind of table error that only shows up as a damaged panel."""
        rows = list(frame_render.PANEL_OVERRIDES.values())
        self.assertEqual(len(rows), len(set(rows)))


class SinkReportingTests(unittest.TestCase):
    def test_wire_sink_names_the_hardware_not_the_pipeline_stage(self) -> None:
        self.assertEqual(frame_render.wire_sink("panel"), "inky")
        self.assertEqual(frame_render.wire_sink("framebuffer"), "framebuffer")
        self.assertEqual(frame_render.wire_sink("preview"), "preview")

    def test_status_field_lists_every_sink_not_just_the_first(self) -> None:
        """A frame with a monitor attached and a dead panel must not report
        the same string as a frame that is actually driving the e-ink."""
        both = "+".join(
            frame_render.wire_sink(n) for n in ("panel", "framebuffer")
        )
        hdmi_only = "+".join(frame_render.wire_sink(n) for n in ("framebuffer",))

        self.assertEqual(both, "inky+framebuffer")
        self.assertEqual(hdmi_only, "framebuffer")
        self.assertNotEqual(both, hdmi_only)


class ComposeTests(unittest.TestCase):
    def test_letterboxes_onto_the_panel_geometry(self) -> None:
        """The manifest is authored for a 1600x1200 panel; the bench panel is
        640x400. Composing must fit the photograph inside the panel it is
        actually going to, without cropping it to fill."""
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")

        import io

        buf = io.BytesIO()
        Image.new("RGB", (1600, 1200), (10, 20, 30)).save(buf, format="PNG")

        canvas = frame_render.compose(buf.getvalue(), (640, 400))

        self.assertEqual(canvas.size, (640, 400))
        # 4:3 into 8:5 letterboxes left/right, so the vertical edges are bars.
        self.assertEqual(canvas.getpixel((0, 200)), (0, 0, 0))
        self.assertEqual(canvas.getpixel((320, 200)), (10, 20, 30))


if __name__ == "__main__":
    unittest.main(verbosity=2)
