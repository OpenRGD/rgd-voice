"""VOICE-0.5A regression tests: no Piper install, BLE, devices or network."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch
import wave

from rgd_voice_lab import benchmark_piper, core, piper_engine


class FakePiperVoice:
    loads = 0
    sessions = 0
    active = 0
    max_active = 0
    guard = threading.Lock()

    @classmethod
    def load(cls, path):
        if not path.endswith('.onnx'):
            raise AssertionError("wrong model")
        with cls.guard:
            cls.loads += 1
        return cls()

    def synthesize_wav(self, text, wav):
        with type(self).guard:
            type(self).active += 1
            type(self).max_active = max(type(self).max_active, type(self).active)
        try:
            time.sleep(.004)
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b'\x00\x00' * 1600)
        finally:
            with type(self).guard:
                type(self).active -= 1
                type(self).sessions += 1


class PiperResidentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.model = self.folder / 'it_IT-serena-high.onnx'
        self.model.write_bytes(b'SYNTHETIC MODEL')
        self.config = Path(str(self.model) + '.json')
        self.config.write_text('{}', encoding='utf-8')
        self.engine = piper_engine.PiperEngine()
        FakePiperVoice.loads = 0
        FakePiperVoice.sessions = 0
        FakePiperVoice.active = 0
        FakePiperVoice.max_active = 0
        self.fake_module = types.ModuleType('piper')
        self.fake_module.PiperVoice = FakePiperVoice
        self.patches = [
            patch.dict(sys.modules, {'piper': self.fake_module}),
            patch.dict(os.environ, {'RGD_VOICE_PIPER_RESIDENT': '1'}),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.engine.clear()
        self.temp.cleanup()

    def generate(self, idx=1, model=None):
        return self.engine.synthesize_wav(
            model or self.model, 'Ciao dal laboratorio', self.folder / f'{idx}.wav')

    def test_first_load_then_repeated_synthesis_with_one_engine(self):
        first = self.generate(1)
        second = self.generate(2)
        third = self.generate(3)
        self.assertFalse(first['cache_hit'])
        self.assertEqual(first['resident_enabled'], True)
        self.assertTrue(second['cache_hit'])
        self.assertEqual(second['load_seconds'], 0)
        self.assertTrue(third['cache_hit'])
        self.assertEqual(FakePiperVoice.loads, 1)
        self.assertEqual(FakePiperVoice.sessions, 3)
        self.assertEqual(self.engine.status()['load_count'], 1)
        self.assertEqual(self.engine.status()['synthesis_count'], 3)
        self.assertEqual(self.engine.status()['model_filename'], 'it_IT-serena-high.onnx')
        self.assertNotIn('Ciao dal laboratorio', str(self.engine.status()))

    def test_optional_prewarm_does_not_generate_audio(self):
        warmed = self.engine.prepare(self.model)
        self.assertFalse(warmed['cache_hit'])
        self.assertEqual(FakePiperVoice.loads, 1)
        self.assertEqual(FakePiperVoice.sessions, 0)
        self.assertTrue(self.generate()['cache_hit'])
        self.assertEqual(FakePiperVoice.loads, 1)

    def test_changed_config_forces_safe_reload(self):
        self.generate(1)
        before = self.config.stat()
        os.utime(self.config, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        second = self.generate(2)
        self.assertFalse(second['cache_hit'])
        self.assertEqual(FakePiperVoice.loads, 2)

    def test_single_voice_limit_when_switching_models(self):
        other = self.folder / 'it_IT-serena-medium.onnx'
        other.write_bytes(b'MOCK ALTERNATE')
        Path(str(other) + '.json').write_text('{}', encoding='utf-8')
        self.generate(1)
        self.assertFalse(self.generate(2, other)['cache_hit'])
        self.assertFalse(self.generate(3)['cache_hit'])
        self.assertEqual(FakePiperVoice.loads, 3)
        self.assertEqual(self.engine.status()['model_filename'], self.model.name)

    def test_cache_can_be_disabled_without_device_side_effects(self):
        with patch.dict(os.environ, {'RGD_VOICE_PIPER_RESIDENT': '0'}):
            self.assertFalse(self.engine.prepare(self.model)['resident_enabled'])
            self.assertFalse(self.generate(1)['cache_hit'])
            self.assertFalse(self.generate(2)['cache_hit'])
            self.assertEqual(FakePiperVoice.loads, 2)
            self.assertFalse(self.engine.status()['loaded'])

    def test_invalid_model_fails_closed_without_piper_load(self):
        with self.assertRaises(FileNotFoundError):
            self.engine.prepare(self.folder / 'missing.onnx')
        self.config.unlink()
        with self.assertRaises(FileNotFoundError):
            self.engine.prepare(self.model)
        self.assertEqual(FakePiperVoice.loads, 0)

    def test_parallel_requests_serialize_piper_inference(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            reports = list(pool.map(self.generate, range(1, 9)))
        self.assertEqual(FakePiperVoice.loads, 1)
        self.assertEqual(FakePiperVoice.sessions, 8)
        self.assertEqual(FakePiperVoice.max_active, 1)
        self.assertEqual(sum(1 for x in reports if x['cache_hit']), 7)

    def test_voice_core_existing_wav_interface_reuses_engine(self):
        with patch.object(core.piper_engine, 'ENGINE', self.engine), \
             patch.object(core, 'require_ready'), \
             patch.object(core, 'piper_path', return_value=self.model), \
             patch('rgd_voice_lab.pricing.record', return_value={'estimated_usd': 0}):
            first = core.synthesize('piper:it_IT-serena-high', 'Ciao', 'it_IT-serena-high',
                                    self.folder / 'core1.wav')
            second = core.synthesize('piper:it_IT-serena-high', 'Ciao', 'it_IT-serena-high',
                                     self.folder / 'core2.wav')
        self.assertEqual(first['model'], 'piper:it_IT-serena-high')
        self.assertFalse(first['piper_runtime']['cache_hit'])
        self.assertTrue(second['piper_runtime']['cache_hit'])
        self.assertGreater(second['duration'], 0)
        self.assertEqual(FakePiperVoice.loads, 1)

    def test_benchmark_uses_ephemeral_wavs_without_playback(self):
        with patch.object(benchmark_piper.piper_engine, 'ENGINE', self.engine), \
             patch.object(core, 'require_ready'), \
             patch.object(core, 'piper_path', return_value=self.model), \
             patch.object(core, 'playback') as playback:
            report = benchmark_piper.benchmark(
                'piper:it_IT-serena-high', 'Frase sintetica', repeat=4)
        self.assertEqual(report['repetitions'], 4)
        self.assertEqual(report['first_cache_hit'], False)
        self.assertTrue(report['all_subsequent_cache_hits'])
        self.assertEqual(len(report['runs']), 4)
        self.assertEqual(FakePiperVoice.loads, 1)
        self.assertEqual(FakePiperVoice.sessions, 4)
        playback.assert_not_called()

    def test_benchmark_rejects_cloud_and_ambiguous_text(self):
        with self.assertRaises(ValueError):
            benchmark_piper.benchmark('el:eleven_v4', 'Test', repeat=3)
        with self.assertRaises(ValueError):
            benchmark_piper.benchmark('piper:it_IT-serena-high', 'Test', repeat=1)
        with self.assertRaises(ValueError):
            benchmark_piper.benchmark('piper:it_IT-serena-high', '', repeat=3)


if __name__ == '__main__':
    unittest.main()
