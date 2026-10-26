"""Offline VAD --check regressions: no audio hardware/cloud/LED needed."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("paky01_vad_studio_preflight", ROOT / "paky01_vad_studio_v01.py")
BRIDGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BRIDGE)


class VADPreflightTests(unittest.TestCase):
    def invoke(self, arguments, profile, *, profile_error=None):
        legacy = Mock()
        legacy.resolve_devices.return_value = ("bc-master-capture", "bose-a2dp-playback")
        output = io.StringIO()
        problem = io.StringIO()
        def read():
            if profile_error:
                raise profile_error
            return profile
        with patch.object(BRIDGE, "load_legacy_vad", return_value=legacy) as loader, \
             patch.object(BRIDGE.voice_profile, "read", side_effect=read), \
             patch.object(BRIDGE.voice_profile, "path", return_value=Path("/tmp/mock-voice-profile.json")), \
             patch.object(BRIDGE.visual_feedback, "emit") as visual, \
             patch.object(BRIDGE.core, "synthesize") as synth, \
             patch.object(sys, "argv", ["paky01_vad_studio_v01.py", *arguments]), \
             redirect_stdout(output), redirect_stderr(problem):
            try:
                result = BRIDGE.main()
                exit_code = result
            except SystemExit as exc:
                exit_code = exc.code
        visual.assert_not_called()
        synth.assert_not_called()
        return exit_code, output.getvalue(), problem.getvalue(), loader, legacy

    def test_check_without_voice_default_is_read_only(self):
        code, stdout, stderr, loader, legacy = self.invoke(["--check"], None)
        self.assertEqual(code, 0)
        self.assertIn("[AUDIO] INPUT=bc-master-capture", stdout)
        self.assertIn("[AUDIO] OUTPUT=bose-a2dp-playback", stdout)
        self.assertIn("Non impostata (facoltativa per --check)", stdout)
        self.assertIn("/tmp/mock-voice-profile.json", stdout)
        self.assertFalse(stderr)
        loader.assert_called_once()
        legacy.resolve_devices.assert_called_once_with(activate_a2dp=False)
        legacy.load_whisper.assert_not_called()
        legacy.capture_phrase.assert_not_called()

    def test_check_existing_cloud_profile_needs_no_consent_and_no_cloud_call(self):
        saved = {"model_id":"el:eleven_v4", "voice_id":"JBFqnCBsd6RMkjVDRZzb"}
        code, stdout, _, _, legacy = self.invoke(["--check"], saved)
        self.assertEqual(code, 0)
        self.assertIn("el:eleven_v4", stdout)
        legacy.resolve_devices.assert_called_once_with(activate_a2dp=False)

    def test_check_invalid_profile_still_reports_devices_without_using_it(self):
        code, stdout, _, _, legacy = self.invoke(["--check"], None, profile_error=ValueError("bad profile"))
        self.assertEqual(code, 0)
        self.assertIn("Profilo non valido/non leggibile", stdout)
        legacy.resolve_devices.assert_called_once_with(activate_a2dp=False)

    def test_real_vad_without_profile_fails_before_audio_touch(self):
        code, stdout, stderr, loader, legacy = self.invoke(["--turns", "1"], None)
        self.assertEqual(code, 2)
        self.assertIn("Nessuna voce predefinita", stderr)
        self.assertEqual(stdout, "")
        loader.assert_not_called()
        legacy.resolve_devices.assert_not_called()

    def test_cloud_profile_requires_explicit_consent_before_audio_touch(self):
        saved = {"model_id":"el:eleven_v4", "voice_id":"JBFqnCBsd6RMkjVDRZzb"}
        code, stdout, stderr, loader, legacy = self.invoke(["--turns", "1"], saved)
        self.assertEqual(code, 2)
        self.assertIn("--allow-cloud", stderr)
        self.assertEqual(stdout, "")
        loader.assert_not_called()
        legacy.resolve_devices.assert_not_called()


if __name__ == "__main__":
    unittest.main()
