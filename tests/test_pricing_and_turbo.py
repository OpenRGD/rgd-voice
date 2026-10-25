import asyncio
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from rgd_voice_lab import pricing, turbo, core


class PricingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_v4_turbo_price_unconfirmed(self):
        for model in ('el:eleven_v4', 'el:eleven_v4_turbo'):
            self.assertIsNone(pricing.tariff(self.root, model)['usd_per_1000_chars'])
            self.assertIsNone(pricing.forecast(self.root, model)['usd_period'])

    def test_reference_forecast_uses_character_assumption(self):
        forecast = pricing.forecast(self.root, 'el:eleven_flash_v2_5', 30, 900, 30)
        self.assertAlmostEqual(forecast['usd_per_voiced_minute'], 0.045)
        self.assertAlmostEqual(forecast['usd_period'], 40.50)

    def test_operator_price_override_private_reversible(self):
        result = pricing.set_override(self.root, 'el:eleven_v4_turbo', 0.075, 'usd_per_1000_chars')
        self.assertEqual(result['rate_status'], 'operator_override')
        self.assertAlmostEqual(pricing.forecast(self.root, 'el:eleven_v4_turbo', 30, 900)['usd_period'], 60.75)
        self.assertEqual((pricing.root(self.root)/'overrides.json').stat().st_mode & 0o777, 0o600)
        pricing.delete_override(self.root, 'el:eleven_v4_turbo')
        self.assertIsNone(pricing.tariff(self.root, 'el:eleven_v4_turbo')['usd_per_1000_chars'])

    def test_never_store_utterances_and_unknown_rate_not_free(self):
        a = pricing.record(self.root, 'ElevenLabs', 'el:eleven_v4_turbo', 'tts', chars=200, audio_seconds=13)
        self.assertIsNone(a['estimated_usd'])
        b = pricing.record(self.root, 'ElevenLabs', 'el:eleven_flash_v2_5', 'tts', chars=1000, audio_seconds=60)
        self.assertAlmostEqual(b['estimated_usd'], .05)
        c = pricing.record(self.root, 'Piper', 'piper:it_IT-serena-high', 'tts', chars=100, audio_seconds=8)
        self.assertEqual(c['estimated_usd'], 0)
        report = pricing.summary(self.root)
        self.assertEqual(report['total_requests'], 3)
        self.assertTrue(report['has_unpriced_requests'])
        raw = (pricing.root(self.root)/'usage.sqlite3').read_bytes()
        self.assertNotIn(b'secret', raw)

    def test_provider_sync_rates_not_mislabeled_as_usd_tariffs(self):
        def fake_get(url, headers):
            self.assertIn('xi-api-key', headers)
            if url.endswith('models'):
                return [{'model_id':'eleven_v4_turbo', 'name':'Eleven v4 Turbo',
                         'can_do_text_to_speech':True,'model_rates':{'character_cost_multiplier':0.75}}]
            raise PermissionError('403 User Read restricted')
        with mock.patch('rgd_voice_lab.pricing.json_write', wraps=pricing.json_write) as w:
            snap=pricing.sync_elevenlabs(self.root,key='SECRET-CREDENTIAL', request_json=fake_get)
        self.assertTrue(w.called)
        self.assertEqual(snap['subscription_state'],'restricted_or_unavailable')
        self.assertFalse(snap['public_rate_reference']['auto_usd_prices'])
        self.assertEqual(pricing.model_meta(self.root,'el:eleven_v4_turbo')['model_rates']['character_cost_multiplier'],0.75)
        self.assertNotIn('SECRET-CREDENTIAL',(pricing.root(self.root)/'models-snapshot.json').read_text())
        self.assertIsNone(pricing.tariff(self.root,'el:eleven_v4_turbo')['usd_per_1000_chars'])

    def test_price_input_rejected(self):
        for amount in (-1, float('nan'), 0, 1001, 'abc'):
            with self.assertRaises(ValueError):
                pricing.set_override(self.root,'el:eleven_v4_turbo',amount,'usd_per_1000_chars')
        for minutes in (-1, float('nan'), 1441):
            with self.assertRaises(ValueError):
                pricing.forecast(self.root,'el:eleven_flash_v2_5', minutes)


class FakeSocket:
    def __init__(self, frames):
        self.frames=list(frames)
        self.sent=[]

    async def __aenter__(self): return self
    async def __aexit__(self, *args): return None
    async def send(self, text): self.sent.append(json.loads(text))
    async def recv(self): return json.dumps(self.frames.pop(0))


class TurboTests(unittest.TestCase):
    def test_turbo_registration_and_audio_chunks(self):
        audio=b'Z' * 180
        sock=FakeSocket([{'audio':base64.b64encode(audio).decode()}, {'is_final':True}])
        async def test():
            return await turbo.collect_mp3('Ciao, Paky-01','abc123ABC789','PRIVATE-KEY',
                                           {'stability':0.5},connection_factory=lambda *a,**kw:sock)
        out,ttfb=asyncio.run(test())
        self.assertEqual(out,audio)
        self.assertGreaterEqual(ttfb,0)
        self.assertEqual(sock.sent[0]['voices'],['abc123ABC789'])
        self.assertEqual(sock.sent[1]['inputs'][0]['text'],'Ciao, Paky-01')
        self.assertTrue(sock.sent[2]['close_socket'])

    def test_turbo_provider_error_is_sanitized(self):
        sock=FakeSocket([{'error':{'message':'not permitted'},'is_final':True}])
        async def test():
            return await turbo.collect_mp3('Ciao','abcdefgh1234','SECRET',connection_factory=lambda *a,**kw:sock)
        with self.assertRaises(RuntimeError) as error:
            asyncio.run(test())
        self.assertNotIn('SECRET',str(error.exception))

    def test_v4_turbo_in_catalog(self):
        self.assertIn('el:eleven_v4_turbo',core.MODELS)
        self.assertEqual(core.MODELS['el:eleven_v4_turbo']['model'],'eleven_v4_turbo')


if __name__ == '__main__':
    unittest.main()
