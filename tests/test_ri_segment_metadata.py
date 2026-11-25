"""VOICE↔RI-01: pure contract + opt-in local metadata fan-out tests.

No microphone, BLE, sockets, network, model or media archive. All WAVs are
synthetic and automatically removed. Does NOT test RI EvidenceStore ingestion.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import uuid
import wave

from rgd_voice_lab import ri_segment_metadata as bridge, vad_lifecycle, visual_feedback


def make_consent(duration=30):
    now = time.monotonic_ns()
    return bridge.ConsentWindow(
        uuid.uuid4().hex, "explicit_local_contract_test",
        now, now + duration * 1_000_000_000,
    )


def make_wav(path, rate=16000, seconds=1):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * rate * seconds)


class RIMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wav = Path(self.tmp.name) / "segment.wav"
        make_wav(self.wav)
        self.consent = make_consent()
        self.received = []

    def tearDown(self):
        self.tmp.cleanup()

    def new_fanout(self, cb=None, consent=None):
        return bridge.LocalSegmentMetadataFanout(
            consent=consent or self.consent,
            source_id="voice.local.unverified.bc-master",
            consumer=cb or self.received.append,
        )

    def test_strict_metadata_and_no_observation_claim(self):
        fan = self.new_fanout()
        self.assertTrue(fan.submit_closed_wav(self.wav))
        self.assertTrue(fan.drain_for_test())
        self.assertEqual(len(self.received), 1)
        record = self.received[0]
        self.assertEqual(record["schema_version"], bridge.SCHEMA_VERSION)
        self.assertEqual(record["capture_owner_status"], "voice_local_unmanaged")
        self.assertFalse(record["source_id_verified"])
        self.assertEqual(record["source_id"], "voice.local.unverified.bc-master")
        self.assertEqual(record["format"]["media_type"], "audio/wav")
        self.assertEqual(record["format"]["pcm_sample_rate_hz"], 16000)
        self.assertEqual(record["format"]["sample_frame_count"], 16000)
        self.assertEqual(record["time"]["source_measurement_time_status"], "unknown")
        self.assertIsNone(record["time"]["chronograf_anchor_ref"])
        self.assertIsNone(record["ri_observation_ref"])
        self.assertEqual(record["storage_status"], "not_persisted")
        self.assertFalse(record["payload_attached"])
        self.assertFalse(record["history_committed"])
        self.assertFalse(record["actuation_enabled"])
        encoded = json.dumps(record)
        for forbidden in (
            str(self.wav), "pcm_bytes", "payload_sha256_hex", "observation_id",
            "raw_audio", "transcript", "recording_url", "api_key",
        ):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(fan.status()["delivered_metadata"], 1)

    def test_same_session_monotonic_sequence_and_explicit_expiration(self):
        fan = self.new_fanout()
        self.assertTrue(fan.submit_closed_wav(self.wav))
        self.assertTrue(fan.submit_closed_wav(self.wav))
        self.assertTrue(fan.drain_for_test())
        self.assertEqual([v["segment_sequence"] for v in self.received], [0, 1])
        self.assertEqual(len({v["producer_session_id"] for v in self.received}), 1)
        self.assertEqual(self.received[0]["consent"]["scope"], "metadata_only")
        self.assertLessEqual(
            self.received[0]["time"]["segment_closed_monotonic_ns"],
            self.received[1]["time"]["segment_closed_monotonic_ns"],
        )

    def test_bad_or_missing_consent_fails_closed(self):
        with self.assertRaises(TypeError):
            self.new_fanout(consent="yes")
        with self.assertRaises(ValueError):
            bridge.ConsentWindow(uuid.uuid4().hex, "test", 1, 2,
                                 scope="raw_media_and_cloud")
        with self.assertRaises(ValueError):
            bridge.ConsentWindow(uuid.uuid4().hex, "test", 1, 150_000_000_001)
        with self.assertRaises(ValueError):
            bridge.ConsentWindow.for_explicit_local_session(
                purpose="pilot", ttl_seconds=121)

    def test_expired_consent_cannot_schedule(self):
        now = time.monotonic_ns()
        consent = bridge.ConsentWindow(uuid.uuid4().hex, "test",
                                       now - 2_000_000_000,
                                       now - 1_000_000_000)
        fan = self.new_fanout(consent=consent)
        self.assertFalse(fan.submit_closed_wav(self.wav))
        self.assertEqual(fan.status()["dropped_expired_or_unconsented"], 1)
        self.assertEqual(self.received, [])

    def test_expired_during_queue_wait_drops_undelivered_report(self):
        now = time.monotonic_ns()
        consent = bridge.ConsentWindow(uuid.uuid4().hex, "test",
                                       now - 500_000_000,
                                       now + 500_000_000)
        started = threading.Event()
        release = threading.Event()
        def slow(_report):
            started.set()
            release.wait(timeout=2)
        fan = self.new_fanout(cb=slow, consent=consent)
        self.assertTrue(fan.submit_closed_wav(self.wav))
        self.assertTrue(started.wait(1))
        self.assertTrue(fan.submit_closed_wav(self.wav))
        time.sleep(.55)
        release.set()
        self.assertTrue(fan.drain_for_test())
        self.assertEqual(fan.status()["delivered_metadata"], 1)
        self.assertEqual(fan.status()["dropped_expired_in_queue"], 1)

    def test_queue_backpressure_does_not_block_capture(self):
        entered = threading.Event()
        release = threading.Event()
        def blocking(_metadata):
            entered.set()
            release.wait(timeout=2)
        fan = self.new_fanout(cb=blocking)
        try:
            self.assertTrue(fan.submit_closed_wav(self.wav))
            self.assertTrue(entered.wait(1))
            before = time.monotonic()
            accepted = [fan.submit_closed_wav(self.wav) for _ in
                        range(bridge.MAX_PENDING + 2)]
            elapsed = time.monotonic() - before
            self.assertLess(elapsed, 0.25)
            self.assertEqual(accepted.count(True), bridge.MAX_PENDING)
            self.assertEqual(accepted.count(False), 2)
            self.assertEqual(fan.status()["dropped_queue_full"], 2)
        finally:
            release.set()
        self.assertTrue(fan.drain_for_test())

    def test_ri_consumer_exception_and_missing_wav_are_nonfatal(self):
        def disconnected(_report):
            raise ConnectionError("RI unavailable")
        fan = self.new_fanout(cb=disconnected)
        self.assertTrue(fan.submit_closed_wav(self.wav))
        self.assertTrue(fan.submit_closed_wav(Path(self.tmp.name) / "gone.wav"))
        self.assertTrue(fan.drain_for_test())
        self.assertEqual(fan.status()["dropped_consumer_or_source_error"], 2)
        self.assertEqual(fan.status().get("delivered_metadata", 0), 0)

    def test_report_strict_schema_optional_dependency(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema optional in Voice offline suite")
        schema = json.loads(
            (Path(__file__).resolve().parents[1] / "contracts" /
             "voice-audio-segment-closed-report.v0.1-candidate.schema.json")
            .read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        fan = self.new_fanout()
        fan.submit_closed_wav(self.wav)
        self.assertTrue(fan.drain_for_test())
        report = self.received[0]
        validator = Draft202012Validator(schema)
        self.assertEqual(list(validator.iter_errors(report)), [])
        for invalid in (
            dict(report, ri_observation_ref={"observation_id": "obs_fake"}),
            dict(report, source_id_verified=True),
            dict(report, payload_attached=True),
            dict(report, actuation_enabled=True),
            dict(report, history_committed=True),
            dict(report, unexpected_media_path=str(self.wav)),
        ):
            self.assertTrue(list(validator.iter_errors(invalid)))

    def test_pure_projection_rejects_fabricated_observation_or_clock(self):
        pcm = bridge._pcm_metadata(self.wav)
        report = bridge.make_closed_segment_report(
            consent=self.consent, source_id="voice.source.unverified",
            segment_sequence=0, pcm_format=pcm,
            closed_monotonic_ns=self.consent.issued_monotonic_ns + 10,
            host_boot_id=None,
        )
        self.assertIsNone(report["ri_observation_ref"])
        self.assertIsNone(report["time"]["boot_id"])
        self.assertNotIn("integrity_status", report)
        with self.assertRaises(ValueError):
            bridge.make_closed_segment_report(
                consent=self.consent, source_id="voice.source.unverified",
                segment_sequence=0, pcm_format=pcm,
                closed_monotonic_ns=self.consent.expires_monotonic_ns,
                host_boot_id=None)


class LocalVADBoundaryTests(unittest.TestCase):
    def test_default_capture_keeps_no_ri_side_effects(self):
        legacy = Mock()
        legacy.capture_phrase.return_value = {"duration": 1.0}
        with patch.dict(os.environ, {"RGD_VOICE_VISUAL_FEEDBACK": "0"}), \
             patch.object(visual_feedback, "emit") as visual, \
             patch.object(bridge, "_pcm_metadata") as pcm:
            result = vad_lifecycle.capture_phrase_observed(
                legacy, "test-source", Path("closed.wav"), vad_mode=2)
        self.assertEqual(result["duration"], 1.0)
        pcm.assert_not_called()
        visual.assert_not_called()

    def test_opt_in_capture_fanout_only_after_completed_segment(self):
        legacy = Mock()
        closed = Path("closed.wav")
        legacy.capture_phrase.return_value = {"duration": 1.0}
        receiver = Mock()
        with patch.dict(os.environ, {"RGD_VOICE_VISUAL_FEEDBACK": "0"}):
            result = vad_lifecycle.capture_phrase_observed(
                legacy, "test-source", closed, vad_mode=2,
                ri_metadata_fanout=receiver)
        self.assertEqual(result["duration"], 1.0)
        receiver.submit_closed_wav.assert_called_once_with(closed)
        # The new kwarg never reached the legacy PulseAudio/VAD code.
        self.assertEqual(legacy.capture_phrase.call_args.kwargs, {"vad_mode": 2})

    def test_vad_errors_and_timeouts_never_submit_evidence(self):
        legacy = Mock()
        relay = Mock()
        with patch.dict(os.environ, {"RGD_VOICE_VISUAL_FEEDBACK": "0"}):
            legacy.capture_phrase.return_value = None
            vad_lifecycle.capture_phrase_observed(
                legacy, "mic", Path("x.wav"), ri_metadata_fanout=relay)
            legacy.capture_phrase.side_effect = RuntimeError("mic disconnected")
            with self.assertRaises(RuntimeError):
                vad_lifecycle.capture_phrase_observed(
                    legacy, "mic", Path("x.wav"), ri_metadata_fanout=relay)
        relay.submit_closed_wav.assert_not_called()

    def test_slow_or_failed_ri_relay_cannot_break_vad(self):
        legacy = Mock()
        legacy.capture_phrase.return_value = {"duration": 1.0}
        relay = Mock()
        relay.submit_closed_wav.side_effect = RuntimeError("consumer broken")
        with patch.dict(os.environ, {"RGD_VOICE_VISUAL_FEEDBACK": "0"}):
            result = vad_lifecycle.capture_phrase_observed(
                legacy, "mic", Path("x.wav"), ri_metadata_fanout=relay)
        self.assertEqual(result["duration"], 1.0)
        relay.submit_closed_wav.assert_called_once()


if __name__ == "__main__":
    unittest.main()
