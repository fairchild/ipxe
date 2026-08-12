#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Regression tests for the frame's optional authenticated control loop."""

from __future__ import annotations

import hashlib
import importlib.util
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch


MODULE_PATH = Path(__file__).parent / "overlay/usr/local/bin/frame-render.py"
# Import the shipped script without leaving host-specific bytecode inside the
# filesystem tree that build-overlay.sh packages.
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("frame_render", MODULE_PATH)
assert SPEC and SPEC.loader
frame_render = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(frame_render)


class ApiBaseTests(unittest.TestCase):
    def test_preserves_https_for_machine_bearer_requests(self) -> None:
        cmdline = "ipxe_api=https://boot.example.test role=frame"
        with patch("builtins.open", mock_open(read_data=cmdline)):
            self.assertEqual(frame_render.api_base(), "https://boot.example.test")

    def test_status_identifies_image_bytes_without_exposing_its_name(self) -> None:
        raw = b"private image bytes"
        self.assertEqual(
            frame_render.image_status_id(raw), hashlib.sha256(raw).hexdigest()
        )


class ControlPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.token_path = root / "machine-token"
        self.id_path = root / "machine-id"
        self.config_path = root / "role-config.json"
        self.constants = patch.multiple(
            frame_render,
            TOKEN_PATH=str(self.token_path),
            MACHINE_ID_PATH=str(self.id_path),
            CONFIG_PATH=str(self.config_path),
        )
        self.constants.start()

    def tearDown(self) -> None:
        self.constants.stop()
        self.tempdir.cleanup()

    def test_no_boot_credential_means_no_control_plane_dependency(self) -> None:
        control = frame_render.ControlPlane("https://boot.example.test")
        with patch.object(frame_render, "post_json") as post:
            control.exchange({"ok": True})
        post.assert_not_called()

    def test_posts_status_and_adopts_current_config_privately(self) -> None:
        self.token_path.write_text("boot-token")
        self.id_path.write_text("machine-1")
        control = frame_render.ControlPlane("https://boot.example.test")
        status_value = {"image": "one.jpg", "ok": True}
        next_config = {
            "source": "https://trips.example/manifest",
            "token": "photo-token",
        }

        with patch.object(
            frame_render, "post_json", return_value={"config": next_config}
        ) as post:
            control.exchange(status_value)

        post.assert_called_once_with(
            "https://boot.example.test/api/machines/machine-1/checkin",
            "boot-token",
            {"status": status_value},
        )
        self.assertEqual(frame_render.load_config(), next_config)
        self.assertEqual(stat.S_IMODE(self.config_path.stat().st_mode), 0o600)

    def test_explicit_null_clears_config_but_an_omitted_field_does_not(self) -> None:
        self.token_path.write_text("boot-token")
        self.id_path.write_text("machine-1")
        self.config_path.write_text('{"source":"https://old.example/manifest"}')
        control = frame_render.ControlPlane("https://boot.example.test")

        with patch.object(frame_render, "post_json", return_value={}):
            control.exchange({"ok": True})
        self.assertTrue(self.config_path.exists())

        with patch.object(frame_render, "post_json", return_value={"config": None}):
            control.exchange({"ok": True})
        self.assertFalse(self.config_path.exists())

    def test_failure_is_soft_and_logged_once_per_streak(self) -> None:
        self.token_path.write_text("boot-token")
        self.id_path.write_text("machine-1")
        control = frame_render.ControlPlane("https://boot.example.test")

        with (
            patch.object(frame_render, "post_json", side_effect=OSError("offline")),
            patch.object(frame_render, "log") as log,
        ):
            control.exchange({"ok": False})
            control.exchange({"ok": False})

        self.assertEqual(control.failures, 2)
        log.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
