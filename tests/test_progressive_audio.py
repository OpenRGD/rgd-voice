"""VOICE-0.5A.1: offline PCM streaming tests; no real model/device/cloud/LED."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import Mock, patch

from rgd_voice_lab import core, piper_engine, progressive_audio, benchmark_progressive_piper

SINK = "bluez_sink.operator_test_a2dp"
REAL_POPEN = subprocess.Popen


class FakeChunk:
    def __init__(self, pcm=None, rate=16000, width=2, channels=1):
        self.sample_rate = rate
        self.sample_width = width
        self.sample_channels = channels
        self.audio_int16_bytes = pcm if pcm is not None else b'\x0b\x00' * 3200


class FakeVoice:
    loads = 0
    events = []
    chunks = 2
    invalid_format_after = False
    fail_after_first = False

    @classmethod
    def load(cls, path):
        cls.loads += 1
        return cls()

    def synthesize(self, text):
        for idx in range(type(self).chunks):
            if idx:
                time.sleep(0.02)
                if type(self).fail_after_first:
                    raise ValueError("Synthesis failure after first audio")
            chunk = FakeChunk(rate=22050 if idx and type(self).invalid_format_after else 16000)
            type(self).events.append(("produced", idx))
            yield chunk

    def synthesize_wav(self, text, wav):
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\x00\x00' * 1000)


class OldPiperVoice(FakeVoice):
    synthesize = None


class EmptyPiperVoice(FakeVoice):
    def synthesize(self, text):
        return iter(())


class ProgressiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.model = self.folder / "it_IT-serena-high.onnx"
        self.model.write_bytes(b"MOCK ONNX")
        Path(str(self.model) + ".json").write_text("{}", encoding="utf-8")
        self.engine = piper_engine.PiperEngine()
        self.piper = types.ModuleType("piper")
        self.piper.PiperVoice = FakeVoice
        FakeVoice.loads = 0
        FakeVoice.chunks = 2
        FakeVoice.events = []
        FakeVoice.invalid_format_after = False
        FakeVoice.fail_after_first = False
        self.events = []
        self.calls = []
        self.fallback = Mock(return_value=(1.5, 2.5, 4.0))
        self.profile = {
            "model_id": "piper:it_IT-serena-high",
            "voice_id": "it_IT-serena-high",
            "controls": {"speed": 1.0, "pitch_semitones": 0.0},
        }

        def fake_spawn(cmd, **kw):
            self.calls.append(cmd)
            # Kernel pipe + consumer process; physically no sound card/LED.
            return REAL_POPEN(
                [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"], **kw)

        self.patchers = [
            patch.dict(sys.modules, {"piper": self.piper}),
            patch.dict(os.environ, {"RGD_VOICE_PIPER_RESIDENT": "1"}),
            patch.object(progressive_audio.piper_engine, "ENGINE", self.engine),
            patch.object(progressive_audio.core, "require_ready"),
            patch.object(progressive_audio.core, "piper_path", return_value=self.model),
            patch.object(progressive_audio.core, "endpoints", return_value=[SINK]),
            patch.object(progressive_audio.shutil, "which", return_value="/usr/bin/pacat"),
            patch.object(progressive_audio.subprocess, "Popen", side_effect=fake_spawn),
            patch.object(progressive_audio.visual_feedback, "emit", side_effect=self.events.append),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()
        self.engine.clear()
        self.temp.cleanup()

    def run_stream(self, text="Prima frase. Seconda frase.", **kwargs):
        return progressive_audio.speak(
            self.profile, text, SINK, fallback=self.fallback, **kwargs)

    def test_two_chunks_are_played_through_one_bounded_raw_pulse_stream(self):
        out = self.run_stream()
        self.assertEqual(out["mode"], "progressive_pcm")
        self.assertEqual(out["chunk_count"], 2)
        self.assertEqual(out["sample_rate"], 16000)
        self.assertEqual(out["channels"], 1)
        self.assertEqual(out["pcm_bytes"], 12800)
        self.assertTrue(0 <= out["first_pcm_seconds"] <= out["synthesis_done_seconds"])
        self.assertTrue(0 <= out["process_start_seconds"] <= out["first_write_seconds"])
        self.assertLessEqual(out["first_write_seconds"], out["playback_drain_seconds"])
        self.assertFalse(out["audio_observed_on_bose"])
        self.assertEqual(self.events, ["playback_started", "playback_stopped"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1:4], ["--playback", "--raw", "--format=s16le"])
        self.assertIn("--rate=16000", self.calls[0])
        self.assertIn("--channels=1", self.calls[0])
        self.assertIn(f"--device={SINK}", self.calls[0])
        self.fallback.assert_not_called()

    def test_repeated_streams_reuse_same_piper_session(self):
        self.run_stream("Uno. Due.")
        self.run_stream("Tre. Quattro.")
        self.assertEqual(FakeVoice.loads, 1)
        self.assertEqual(self.engine.status()["synthesis_count"], 2)
        self.assertEqual(self.events, ["playback_started", "playback_stopped"] * 2)

    def test_single_sentence_one_chunk_does_not_promise_lower_first_audio(self):
        FakeVoice.chunks = 1
        out = self.run_stream("Una frase.")
        self.assertEqual(out["chunk_count"], 1)
        self.assertGreater(out["first_write_seconds"], 0)

    def test_old_piper_api_falls_back_before_output(self):
        self.piper.PiperVoice = OldPiperVoice
        out = self.run_stream()
        self.assertEqual(out["mode"], "full_wav_fallback")
        self.assertEqual(out["fallback_reason"], "piper_stream_unavailable")
        self.assertEqual(self.calls, [])
        self.fallback.assert_called_once()
        self.assertEqual(self.events, [])

    def test_no_pcm_falls_back_before_output(self):
        self.piper.PiperVoice = EmptyPiperVoice
        out = self.run_stream("...")
        self.assertEqual(out["mode"], "full_wav_fallback")
        self.assertEqual(self.calls, [])
        self.fallback.assert_called_once()

    def test_missing_pacat_falls_back_without_loading_piper(self):
        with patch.object(progressive_audio.shutil, "which", return_value=None):
            out = self.run_stream()
        self.assertEqual(out["fallback_reason"], "pacat_unavailable")
        self.assertEqual(FakeVoice.loads, 0)
        self.assertEqual(self.calls, [])

    def test_saved_speed_pitch_preserved_via_old_wav(self):
        self.profile["controls"]["speed"] = 0.9
        out = self.run_stream()
        self.assertEqual(out["fallback_reason"], "speed_pitch_require_full_wav")
        self.assertEqual(self.calls, [])
        self.fallback.assert_called_once()
        self.assertEqual(FakeVoice.loads, 0)

    def test_resident_disabled_uses_fallback(self):
        with patch.dict(os.environ, {"RGD_VOICE_PIPER_RESIDENT": "0"}):
            out = self.run_stream()
        self.assertEqual(out["fallback_reason"], "piper_resident_disabled")
        self.assertEqual(self.calls, [])

    def test_cloud_profile_rejected_before_playback(self):
        self.profile["model_id"] = "el:eleven_v4"
        with self.assertRaises(ValueError):
            self.run_stream()
        self.assertEqual(self.calls, [])
        self.fallback.assert_not_called()

    def test_wrong_sink_denied_before_process_or_model(self):
        with patch.object(progressive_audio.core, "endpoints", return_value=[]):
            with self.assertRaises(RuntimeError):
                self.run_stream()
        self.assertEqual(self.calls, [])
        self.assertEqual(FakeVoice.loads, 0)

    def test_invalid_format_after_audio_never_replays_full_wav(self):
        FakeVoice.invalid_format_after = True
        with self.assertRaises(ValueError):
            self.run_stream()
        self.assertIn("playback_started", self.events)
        self.assertIn("service_error", self.events)
        self.assertNotIn("playback_stopped", self.events)
        self.fallback.assert_not_called()

    def test_synthesis_failure_after_audio_never_replays_full_wav(self):
        FakeVoice.fail_after_first = True
        with self.assertRaisesRegex(ValueError, "Synthesis failure"):
            self.run_stream()
        self.assertIn("service_error", self.events)
        self.assertEqual(self.events.count("playback_started"), 1)
        self.fallback.assert_not_called()

    def test_writer_failure_before_any_bytes_falls_back_cleanly(self):
        with patch.object(progressive_audio.RawPulsePlayer, "_start", side_effect=FileNotFoundError("fake pacat")):
            out = self.run_stream()
        self.assertEqual(out["fallback_reason"], "raw_player_unavailable")
        self.assertEqual(self.events, [])
        self.fallback.assert_called_once()

    def test_writer_failure_after_first_pcm_never_replays(self):
        original = progressive_audio.RawPulsePlayer.write
        seen = []
        def fail_later(player, pcm, rate, channels):
            if not seen:
                seen.append(True)
                return original(player, pcm, rate, channels)
            raise progressive_audio.ProgressivePlaybackError("failed later")
        with patch.object(progressive_audio.RawPulsePlayer, "write", fail_later):
            with self.assertRaises(progressive_audio.ProgressivePlaybackError):
                self.run_stream()
        self.assertIn("service_error", self.events)
        self.fallback.assert_not_called()

    def test_engine_strict_pcm_validation(self):
        FakeVoice.invalid_format_after = True
        chunks = []
        with self.assertRaises(ValueError):
            self.engine.synthesize_pcm(self.model, "Due frasi.", lambda *item: chunks.append(item))
        self.assertEqual(len(chunks), 1)
        self.assertEqual(self.engine.status()["synthesis_count"], 0)

    def test_cancel_event_prevents_playback_and_no_fallback(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(InterruptedError):
            self.run_stream(cancel=cancelled)
        self.assertEqual(self.calls, [])
        self.fallback.assert_not_called()

    def test_success_does_not_set_callers_cancel_token(self):
        token = threading.Event()
        out = self.run_stream(cancel=token)
        self.assertEqual(out["mode"], "progressive_pcm")
        self.assertFalse(token.is_set())
        self.assertEqual(self.events, ["playback_started", "playback_stopped"])
        self.fallback.assert_not_called()

    def test_cancel_after_first_write_stops_playback_without_replay(self):
        token = threading.Event()
        original = progressive_audio.RawPulsePlayer.write

        def cancel_after_first_write(player, pcm, rate, channels):
            result = original(player, pcm, rate, channels)
            token.set()
            return result

        with patch.object(
            progressive_audio.RawPulsePlayer, "write", cancel_after_first_write
        ):
            with self.assertRaises(InterruptedError):
                self.run_stream(cancel=token)
        self.assertIn("playback_started", self.events)
        self.assertIn("playback_stopped", self.events)
        self.assertNotIn("service_error", self.events)
        self.assertEqual(self.events.count("playback_started"), 1)
        self.fallback.assert_not_called()

    def test_no_device_benchmark_uses_same_text_and_real_pcm_metadata(self):
        with patch.object(benchmark_progressive_piper.piper_engine, "ENGINE", self.engine):
            result = benchmark_progressive_piper.benchmark(
                "piper:it_IT-serena-high", "multi", repeat=2)
        self.assertEqual(result["scenario"], "multi")
        self.assertEqual(result["repetitions"], 2)
        self.assertEqual(len(result["runs"]), 2)
        self.assertTrue(all(r["stream_chunk_count"] == 2 for r in result["runs"]))
        self.assertTrue(all(r["pcm_bytes"] == 12800 for r in result["runs"]))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.events, [])

    def test_no_device_benchmark_rejects_cloud_and_unbounded_repeat(self):
        with self.assertRaises(ValueError):
            benchmark_progressive_piper.benchmark("el:eleven_v4", "multi", 3)
        with self.assertRaises(ValueError):
            benchmark_progressive_piper.benchmark("piper:it_IT-serena-high", "multi", 11)


if __name__ == "__main__":
    unittest.main()
