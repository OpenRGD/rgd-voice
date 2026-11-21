"""Offline browser audio lifecycle regressions. No physical speaker or LEDs."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
from unittest import TestCase
from unittest.mock import patch
import urllib.error
import urllib.request

from rgd_voice_lab import browser_playback, core, server


def packet(session, kind, seq=0, clip="a" * 32):
    return {"clip_id": clip, "playback_session_id": session,
            "sequence": seq, "event": kind}


class BrowserGateTests(TestCase):
    def test_player_events_are_not_hardware_observations(self):
        gate = browser_playback.BrowserPlaybackGate()
        received = []
        first = gate.consume(packet("1" * 32, "playing"), lambda event: received.append(event) or True)
        self.assertTrue(first["accepted"])
        self.assertFalse(first["physical_audio_observed"])
        self.assertEqual(first["evidence_kind"], "browser_media_element_event_unverified_physical_output")
        gate.consume(packet("1" * 32, "stopped", 1), lambda event: received.append(event) or True)
        self.assertEqual(received, ["playback_started", "playback_stopped"])

    def test_old_player_end_cannot_override_new_player(self):
        gate = browser_playback.BrowserPlaybackGate()
        received = []
        send = lambda event: received.append(event) or True
        gate.consume(packet("1" * 32, "playing", 0), send)
        gate.consume(packet("2" * 32, "playing", 0), send)
        ignored = gate.consume(packet("1" * 32, "stopped", 1), send)
        self.assertFalse(ignored["accepted"])
        self.assertEqual(ignored["ignored"], "retired_browser_session")
        gate.consume(packet("2" * 32, "stopped", 1), send)
        self.assertEqual(received, ["playback_started", "playback_started", "playback_stopped"])

    def test_duplicate_and_invalid_payloads_fail_closed(self):
        gate = browser_playback.BrowserPlaybackGate()
        send = lambda _: True
        gate.consume(packet("1" * 32, "playing", 0), send)
        self.assertFalse(gate.consume(packet("1" * 32, "playing", 0), send)["accepted"])
        invalid = [
            {"clip_id": "../passwd", "playback_session_id": "1" * 32, "sequence": 0, "event": "playing"},
            {**packet("1" * 32, "playing"), "sequence": True},
            {**packet("1" * 32, "playing"), "event": "invoke_shell"},
            {**packet("1" * 32, "playing"), "url": "http://example.invalid"},
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                browser_playback.validate_browser_playback(payload)

    def test_absent_optional_visual_service_does_not_break_browser_request(self):
        gate = browser_playback.BrowserPlaybackGate()
        result = gate.consume(packet("9" * 32, "playing"), lambda _: False)
        self.assertTrue(result["accepted"])
        self.assertFalse(result["visual_scheduled"])


class BrowserHttpTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.original_home = core.APP_HOME
        core.APP_HOME = Path(cls.temp.name)
        server.ensure_dirs()
        (core.APP_HOME / "clips" / ("a" * 32 + ".wav")).write_bytes(b"FAKE-WAV-NOT-PLAYED")
        server.BROWSER_PLAYBACK_GATE = browser_playback.BrowserPlaybackGate()
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.httpd.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)
        core.APP_HOME = cls.original_home
        cls.temp.cleanup()

    def post(self, value, *, header=True, origin=None):
        headers = {"Content-Type": "application/json"}
        if header:
            headers["X-Voice-Lab-Setup"] = "local"
        if origin is not None:
            headers["Origin"] = origin
        request = urllib.request.Request(self.base + "/api/browser-playback",
                                         data=json.dumps(value).encode(), headers=headers, method="POST")
        with urllib.request.urlopen(request, timeout=4) as response:
            return json.load(response)

    def test_browser_playback_requires_same_origin_and_setup_header(self):
        with patch.object(server.visual_feedback, "emit", return_value=True) as emit:
            with self.assertRaises(urllib.error.HTTPError) as e:
                self.post(packet("3" * 32, "playing"), header=False)
            self.assertEqual(e.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as e:
                self.post(packet("3" * 32, "playing"), origin="https://untrusted.invalid")
            self.assertEqual(e.exception.code, 403)
            self.assertTrue(self.post(packet("3" * 32, "playing"), origin=self.base)["accepted"])
            emit.assert_called_once_with("playback_started")

    def test_browser_stop_needs_active_session(self):
        with patch.object(server.visual_feedback, "emit", return_value=True) as emit:
            result = self.post(packet("4" * 32, "stopped", 1))
            self.assertFalse(result["accepted"])
            emit.assert_not_called()

    def test_browser_cannot_emit_playback_for_missing_clip(self):
        with patch.object(server.visual_feedback, "emit", return_value=True) as emit:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.post(packet("5" * 32, "playing", clip="f" * 32))
            self.assertEqual(error.exception.code, 404)
            emit.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
