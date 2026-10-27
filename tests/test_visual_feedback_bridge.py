"""Offline Visual Feedback tests: no audio hardware, cloud or real BLE.

Voice makes at most best-effort logical reports; a green visual state is not
independent evidence that audible PCM reached a speaker.
"""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch
import unittest
import wave

from rgd_voice_lab import core, visual_feedback


class VisualBridgeTests(unittest.TestCase):
    def test_disabled_by_default(self):
        with patch.dict(os.environ, {"RGD_VOICE_VISUAL_FEEDBACK": "0"}):
            with patch.object(visual_feedback._RELAY, "submit") as submit:
                self.assertFalse(visual_feedback.emit("playback_started"))
                submit.assert_not_called()

    def test_contract_and_sequence_are_strict(self):
        pkt = visual_feedback.packet("a" * 32, 7, "playback_started")
        self.assertEqual(pkt, {
            "version": 1,
            "command": "voice_event",
            "schema_version": "openrgd.visual.voice-event.v0.1-candidate",
            "source": "rgd-voice",
            "session_id": "a" * 32,
            "sequence": 7,
            "event": "playback.started",
        })
        with self.assertRaises(ValueError):
            visual_feedback.packet("a" * 32, 8, "audio.generated")

    def test_relay_is_monotonic_and_fail_open_for_visual_outage(self):
        relay = visual_feedback.VisualRelay()
        packets = []
        with patch.object(relay, "_send", side_effect=packets.append):
            relay.submit("capture_started")
            relay.submit("capture_stopped_processing")
            relay.submit("ready_idle")
            relay._pending.join()
        self.assertEqual([p["sequence"] for p in packets], [0, 1, 2])
        self.assertEqual([p["event"] for p in packets],
                         ["listening.started", "inference.started", "voice.ready"])
        self.assertEqual(len({p["session_id"] for p in packets}), 1)

    def test_playback_reports_only_when_process_is_started(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.communicate.return_value = (None, "")
        proc.returncode = 0
        with patch.object(core, "endpoints", return_value=["sample-speaker"]), \
                patch.object(core.subprocess, "Popen", return_value=proc), \
                patch.object(core.visual_feedback, "emit") as emit:
            result = core.playback(Path("/tmp/synthetic-preview.wav"), "sample-speaker")
        self.assertEqual(result["status"], "played")
        self.assertEqual([call.args[0] for call in emit.call_args_list],
                         ["playback_started", "playback_stopped"])

    def test_playback_error_never_reports_success(self):
        proc = MagicMock()
        proc.poll.return_value = None
        proc.communicate.return_value = (None, "audio backend failed")
        proc.returncode = 2
        with patch.object(core, "endpoints", return_value=["sample-speaker"]), \
                patch.object(core.subprocess, "Popen", return_value=proc), \
                patch.object(core.visual_feedback, "emit") as emit:
            with self.assertRaisesRegex(RuntimeError, "paplay"):
                core.playback(Path("/tmp/synthetic-preview.wav"), "sample-speaker")
        self.assertEqual([call.args[0] for call in emit.call_args_list],
                         ["playback_started", "service_error"])

    def test_playback_without_audio_process_does_not_claim_start(self):
        proc = MagicMock()
        proc.poll.return_value = 1
        proc.communicate.return_value = (None, "could not connect")
        proc.returncode = 1
        with patch.object(core, "endpoints", return_value=["sample-speaker"]), \
                patch.object(core.subprocess, "Popen", return_value=proc), \
                patch.object(core.visual_feedback, "emit") as emit:
            with self.assertRaises(RuntimeError):
                core.playback(Path("/tmp/synthetic-preview.wav"), "sample-speaker")
        self.assertEqual([call.args[0] for call in emit.call_args_list],
                         ["service_error"])

    def test_capture_emits_started_only_after_parecord_spawn(self):
        with TemporaryDirectory() as temp:
            wav = Path(temp) / "capture.wav"
            with wave.open(str(wav), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(16000)
                stream.writeframes(b"\x00\x00" * 32000)
            proc = MagicMock()
            proc.poll.return_value = None
            proc.communicate.return_value = (None, b"")
            with patch.object(core, "endpoints", return_value=["sample-mic"]), \
                    patch.object(core.subprocess, "Popen", return_value=proc), \
                    patch.object(core.time, "sleep"), \
                    patch.object(core.visual_feedback, "emit") as emit:
                result = core.capture("sample-mic", 2, wav)
        self.assertEqual(result["duration"], 2)
        self.assertEqual([call.args[0] for call in emit.call_args_list],
                         ["capture_started", "ready_idle"])

    def test_transcription_emits_processing_and_idle(self):
        model_id = "oa:gpt-transcribe"
        with TemporaryDirectory() as temp:
            path = Path(temp) / "sample.wav"
            path.write_bytes(b"RIFFTEST")
            with patch.object(core, "require_ready"), \
                    patch.object(core, "multipart", return_value=(b"fake", "multipart/form-data")), \
                    patch.object(core, "get_json", return_value={"text": "Ciao"}), \
                    patch.object(core.secrets, "get_secret", return_value="fake-no-cloud-call"), \
                    patch.object(core, "audio_duration", return_value=2.0), \
                    patch("rgd_voice_lab.pricing.record", return_value={"estimated_usd": None}), \
                    patch.object(core.visual_feedback, "emit") as emit:
                result = core.transcribe(model_id, path)
        self.assertEqual(result["text"], "Ciao")
        self.assertEqual([call.args[0] for call in emit.call_args_list],
                         ["capture_stopped_processing", "ready_idle"])


if __name__ == "__main__":
    unittest.main()
