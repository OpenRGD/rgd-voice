"""Offline A/B probe tests: same native PCM in WAV and raw, no device access."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

from rgd_voice_lab import audio_ab_probe


class FixtureTests(unittest.TestCase):
    def test_exact_same_pcm_in_wav_and_raw_files(self):
        raw_parts = [
            b"\x00\x00" * 2205,
            b"\x00\x40" * 4410,
            b"\xfc\xff" * 2205,
        ]

        def synth(_model, _text, cb):
            for pcm in raw_parts:
                cb(pcm, 22050, 1)
            return {"pcm_bytes": sum(len(x) for x in raw_parts),
                    "chunk_count": len(raw_parts)}

        with tempfile.TemporaryDirectory() as d:
            with patch.object(audio_ab_probe.piper_engine.ENGINE,
                              "synthesize_pcm", side_effect=synth) as engine:
                report = audio_ab_probe.capture_fixture(
                    Path("/does-not-exist.onnx"), "Frase uno. Due. Tre.", Path(d))
            self.assertEqual(report["total_bytes"], sum(len(x) for x in raw_parts))
            self.assertEqual(len(report["chunks"]), 3)
            self.assertEqual(report["sample_rate"], 22050)
            raw = report["raw_path"].read_bytes()
            with wave.open(str(report["wav_path"]), "rb") as f:
                self.assertEqual(f.getsampwidth(), 2)
                self.assertEqual(f.getnchannels(), 1)
                self.assertEqual(f.getframerate(), 22050)
                wav_pcm = f.readframes(f.getnframes())
            self.assertEqual(raw, b"".join(raw_parts))
            self.assertEqual(wav_pcm, raw)
            self.assertGreater(report["chunks"][1]["peak_percent"], 0)
            engine.assert_called_once()

    def test_inconsistent_pcm_format_is_rejected_without_playback(self):
        def synth(_model, _text, cb):
            cb(b"\x01\x00" * 320, 16000, 1)
            cb(b"\x01\x00" * 320, 22050, 1)

        with tempfile.TemporaryDirectory() as d:
            with patch.object(audio_ab_probe.piper_engine.ENGINE,
                              "synthesize_pcm", side_effect=synth), \
                 patch.object(audio_ab_probe, "_run_player") as player:
                with self.assertRaisesRegex(ValueError, "PCM format changed"):
                    audio_ab_probe.capture_fixture(Path("/fake"), "Test", Path(d))
            player.assert_not_called()

    def test_paced_fixture_uses_exact_same_three_pcm_chunks(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pcm_parts = [
                b"\\x04\\x00" * 300,
                b"\\x05\\x00" * 400,
                b"\\x06\\x00" * 500,
            ]
            # Save the same bytes that would have been generated once by Piper.
            # Construct bytes here, without loading any model or sound server.
            pcm_parts = [bytes.fromhex(x.replace("\\x", "")) * n for x, n in
                         [("04\\x00", 300), ("05\\x00", 400), ("06\\x00", 500)]]
            raw = root / "fixture.pcm"
            raw.write_bytes(b"".join(pcm_parts))
            fixture = {
                "raw_path": raw, "sample_rate": 22050,
                "chunks": [{"bytes": len(part), "seconds": len(part) / 44100}
                           for part in pcm_parts],
            }
            played = []
            instances = []
            class FakePlayer:
                def __init__(self, sink, *, origin):
                    self.sink = sink
                    self.origin = origin
                    self.first_write_seconds = None
                    self.bytes_written = 0
                    self.closed = False
                    instances.append(self)
                def write(self, pcm, rate, channels):
                    self.first_write_seconds = 0.001 if self.first_write_seconds is None else self.first_write_seconds
                    played.append((bytes(pcm), rate, channels))
                    self.bytes_written += len(pcm)
                def finish(self):
                    return 0.5
                def close(self):
                    self.closed = True
            with patch.object(audio_ab_probe.progressive_audio, "RawPulsePlayer", FakePlayer), \\
                 patch.object(audio_ab_probe.time, "sleep") as pause:
                result = audio_ab_probe.play_paced_fixture(fixture, "test_sink", 1.8)
            self.assertEqual([x[0] for x in played], pcm_parts)
            self.assertTrue(all(x[1:] == (22050, 1) for x in played))
            self.assertEqual(result["bytes_written"], len(b"".join(pcm_parts)))
            self.assertEqual([x["chunk"] for x in result["chunks"]], [1, 2, 3])
            self.assertEqual(result["gap_seconds"], 1.8)
            self.assertEqual(pause.call_count, 2)
            pause.assert_any_call(1.8)
            self.assertEqual(len(instances), 1)
            self.assertTrue(instances[0].closed)

    def test_paced_probe_rejects_invalid_gap_before_device_access(self):
        with self.assertRaises(ValueError):
            audio_ab_probe.play_paced_fixture({}, "none", -0.5)
        with self.assertRaises(ValueError):
            audio_ab_probe.play_paced_fixture({}, "none", 5.1)
        with patch.object(audio_ab_probe.core, "endpoints") as endpoints:
            with self.assertRaises(SystemExit) as exc:
                audio_ab_probe.main(["--paced-only", "--paced-gap-ms", "1800"])
        self.assertEqual(exc.exception.code, 2)
        endpoints.assert_not_called()

    def test_no_play_flag_does_not_discover_or_start_speakers(self):
        profile = {"model_id": "piper:it_IT-serena-high", "controls": {}}
        fixture = {
            "total_bytes": 3200, "duration_seconds": 0.1,
            "sample_rate": 16000, "chunks": [
                {"index": 1, "seconds": 0.1,
                 "peak_percent": 50., "sha256_prefix": "123abc"}],
            "raw_sha256": "a" * 64,
        }
        with patch.object(audio_ab_probe.voice_profile, "read", return_value=profile), \
             patch.object(audio_ab_probe.core, "piper_path",
                          return_value=Path("/synthetic-model.onnx")), \
             patch.object(audio_ab_probe.piper_engine.ENGINE, "prepare",
                          return_value={"load_seconds": 0.1}), \
             patch.object(audio_ab_probe, "capture_fixture", return_value=fixture), \
             patch.object(audio_ab_probe.core, "endpoints") as endpoints, \
             patch.object(audio_ab_probe, "_run_player") as player:
            self.assertEqual(audio_ab_probe.main([]), 0)
        endpoints.assert_not_called()
        player.assert_not_called()


if __name__ == "__main__":
    unittest.main()
