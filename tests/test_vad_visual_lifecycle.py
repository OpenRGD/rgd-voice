"""VOICE-0.4D: no-hardware tests for VAD→Visual events and PCM provenance."""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from rgd_voice_lab import vad_lifecycle, visual_feedback


class FakeLegacy:
    def __init__(self, pcm=b'\\x02\\x00' * 480, result=None, fail=None):
        self.pcm = pcm
        self.result = {'seconds': .5} if result is None else result
        self.fail = fail
        self.read_exact = self._read_exact

    @staticmethod
    def _read_exact(stream, length):
        return stream.read(length)

    def capture_phrase(self, mic, path, **options):
        assert mic == 'test-mic' and path == Path('sample.wav')
        assert options['vad_mode'] == 2
        self.read_exact(BytesIO(self.pcm), 960)
        if self.fail:
            raise self.fail
        return self.result

    def transcribe(self, model, capture_path, *, vad):
        assert not vad and model == 'fake-stt'
        return 'Buongiorno', .2


class VADVisualTests(TestCase):
    def test_first_pcm_then_stt_then_thinking_remains(self):
        legacy = FakeLegacy()
        original = legacy.read_exact
        seen = []
        with patch.dict(os.environ, {'RGD_VOICE_VISUAL_FEEDBACK': '1'}), \\
                patch.object(visual_feedback, 'emit', side_effect=lambda event: seen.append(event)):
            result = vad_lifecycle.capture_phrase_observed(
                legacy, 'test-mic', Path('sample.wav'), vad_mode=2)
            self.assertEqual(result['seconds'], .5)
            self.assertIs(legacy.read_exact, original)
            self.assertEqual(seen, ['capture_started'])
            self.assertEqual(vad_lifecycle.transcribe_observed(
                legacy, 'fake-stt', Path('sample.wav')), ('Buongiorno', .2))
        self.assertEqual(seen, ['capture_started', 'capture_stopped_processing'])

    def test_no_pcm_never_claims_listening(self):
        legacy = FakeLegacy(pcm=b'')
        legacy.result = None
        with patch.dict(os.environ, {'RGD_VOICE_VISUAL_FEEDBACK':'1'}), \\
                patch.object(visual_feedback, 'emit') as emit:
            self.assertIsNone(vad_lifecycle.capture_phrase_observed(
                legacy, 'test-mic', Path('sample.wav'), vad_mode=2))
        emit.assert_not_called()

    def test_timeout_after_pcm_resolves_to_idle(self):
        legacy = FakeLegacy()
        legacy.result = None
        seen = []
        with patch.dict(os.environ, {'RGD_VOICE_VISUAL_FEEDBACK':'1'}), \\
                patch.object(visual_feedback, 'emit', side_effect=lambda event: seen.append(event)):
            vad_lifecycle.capture_phrase_observed(legacy, 'test-mic', Path('sample.wav'), vad_mode=2)
        self.assertEqual(seen, ['capture_started', 'ready_idle'])

    def test_error_does_not_break_restoration(self):
        legacy = FakeLegacy(fail=RuntimeError('read failed'))
        original = legacy.read_exact
        seen = []
        with patch.dict(os.environ, {'RGD_VOICE_VISUAL_FEEDBACK':'1'}), \\
                patch.object(visual_feedback, 'emit', side_effect=lambda event: seen.append(event)):
            with self.assertRaises(RuntimeError):
                vad_lifecycle.capture_phrase_observed(legacy,'test-mic',Path('sample.wav'),vad_mode=2)
        self.assertIs(legacy.read_exact, original)
        self.assertEqual(seen, ['capture_started', 'service_error'])

    def test_opt_in_only_baseline_path_unchanged(self):
        legacy = FakeLegacy()
        original = legacy.read_exact
        with patch.dict(os.environ, {'RGD_VOICE_VISUAL_FEEDBACK':'0'}), \\
                patch.object(visual_feedback, 'emit') as emit:
            self.assertEqual(vad_lifecycle.capture_phrase_observed(
                legacy,'test-mic',Path('sample.wav'),vad_mode=2)['seconds'], .5)
        self.assertIs(legacy.read_exact, original)
        emit.assert_not_called()

    def test_empty_stt_idle_and_stt_error(self):
        legacy = FakeLegacy()
        legacy.transcribe = lambda *_a, **_kw: ('', .1)
        seen = []
        with patch.object(visual_feedback, 'emit', side_effect=lambda event: seen.append(event)):
            self.assertEqual(vad_lifecycle.transcribe_observed(
                legacy, 'fake-stt', Path('sample.wav'))[0], '')
        self.assertEqual(seen, ['capture_stopped_processing', 'ready_idle'])
        def bad_transcribe(*_args, **_kwargs):
            raise RuntimeError('STT failed')
        legacy.transcribe = bad_transcribe
        seen = []
        with patch.object(visual_feedback, 'emit', side_effect=lambda event: seen.append(event)):
            with self.assertRaises(RuntimeError):
                vad_lifecycle.transcribe_observed(legacy, 'fake-stt', Path('sample.wav'))
        self.assertEqual(seen, ['capture_stopped_processing', 'service_error'])
