"""Offline contract tests: no hardware, model download, or external HTTP required."""
import json
import os
import tempfile
import stat
from unittest.mock import patch
import threading
import unittest
import urllib.error
import urllib.request
import wave
from pathlib import Path

from rgd_voice_lab import core
from rgd_voice_lab import server
from rgd_voice_lab import secrets


class CoreTests(unittest.TestCase):
    def test_catalog_unique_ids_and_types(self):
        self.assertEqual(len(core.CATALOG), len(core.MODELS))
        self.assertTrue(all(m['type'] in ('stt', 'tts') for m in core.CATALOG))
        self.assertTrue(any(m['provider'] == 'ElevenLabs' and m['type'] == 'stt' for m in core.CATALOG))
        self.assertTrue(any(m['provider'] == 'ElevenLabs' and m['type'] == 'tts' for m in core.CATALOG))
        self.assertIn('piper:it_IT-serena-medium',core.MODELS)
        self.assertIn('piper:en_US-ryan-high',core.MODELS)
        self.assertIn('oa:gpt-transcribe',core.MODELS)

    def test_wer_same_text(self):
        self.assertEqual(core.wer('Ciao, Paky!', 'ciao paky')['wer_pct'], 0.0)

    def test_wer_substitution(self):
        self.assertEqual(core.wer('ciao sono paky', 'ciao sono paci'), {'errors': 1, 'words': 3, 'wer_pct': 33.3})

    def test_cloud_rejected_as_install(self):
        with self.assertRaises(ValueError):
            core.install_model('el:scribe_v2')

    def test_multipart_encoded(self):
        data, ctype = core.multipart({'model_id': 'scribe_v2'}, 'file', 'speech.wav', b'RIFFHELLO', 'audio/wav')
        self.assertIn(b'RIFFHELLO', data)
        self.assertIn(b'content-type: audio/wav', data.lower())
        self.assertTrue(ctype.startswith('multipart/form-data;'))

    def test_no_invalid_path_reference(self):
        with self.assertRaises(ValueError):
            server.clip_path('../../etc/shadow')
        with self.assertRaises(ValueError):
            server.clip_path('00' * 33)

    def test_origin_filter(self):
        self.assertTrue(server.allowed_origin('http://127.0.0.1:8765', 8765))
        self.assertFalse(server.allowed_origin('https://evil.example', 8765))


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.previous = core.APP_HOME
        cls.old_secrets_path = secrets.ENV_PATH
        secrets.ENV_PATH = Path(cls.temp.name) / 'config' / '.env'
        core.APP_HOME = Path(cls.temp.name)
        server.ensure_dirs()
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f'http://127.0.0.1:{cls.httpd.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)
        core.APP_HOME = cls.previous
        secrets.ENV_PATH = cls.old_secrets_path
        cls.temp.cleanup()

    def post(self, route, data=None, raw=False, origin=None, extra_headers=None):
        payload = data if raw else json.dumps(data).encode()
        headers = {'Content-Type': 'audio/wav' if raw else 'application/json'}
        if origin:
            headers['Origin'] = origin
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(self.origin + route, data=payload, method='POST', headers=headers)
        return urllib.request.urlopen(req, timeout=12)

    def test_index(self):
        with urllib.request.urlopen(self.origin + '/', timeout=12) as r:
            self.assertIn(b'La voce', r.read())

    def test_models_api(self):
        with urllib.request.urlopen(self.origin + '/api/models', timeout=12) as r:
            data = json.load(r)
            self.assertTrue(any(m['id'] == 'fw:base:5' for m in data['models']))

    def test_install_needs_confirm(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post('/api/install', {'model_id':'fw:tiny:5'})
        self.assertEqual(ctx.exception.code, 400)

    def test_cross_origin_denied(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post('/api/delete', {'clip_id':'a'*32}, origin='https://evil.example')
        self.assertEqual(ctx.exception.code, 403)

    def test_voice_library_curated_offline(self):
        with urllib.request.urlopen(self.origin + '/api/voice-library?provider=piper&language=it') as r:
            data = json.load(r)
        names = {v['id'] for v in data['voices']}
        self.assertIn('it_IT-serena-high', names)
        self.assertIn('it_IT-paola-medium', names)
        self.assertNotIn('en_US-amy-medium', names)

    def test_cost_dashboard_and_forecast(self):
        with urllib.request.urlopen(self.origin + '/api/costs') as r:
            data=json.load(r)
        self.assertTrue(any(x['model_id']=='el:eleven_v4_turbo' for x in data['rates']))
        self.assertFalse(data['sync']['usd_prices_autosync'])
        with self.post('/api/pricing/forecast', {'model_id':'el:eleven_v4_turbo','minutes_per_day':30}) as r:
            self.assertIsNone(json.load(r)['usd_period'])

    def test_cost_rate_override_requires_setup_header(self):
        payload={'action':'save','model_id':'el:eleven_v4_turbo','unit':'usd_per_1000_chars',
                 'unit_price_usd':0.1,'confirm':True}
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post('/api/pricing/rate', payload)
        self.assertEqual(ctx.exception.code,403)
        with self.post('/api/pricing/rate',payload,extra_headers={'X-Voice-Lab-Setup':'local'}) as r:
            self.assertEqual(json.load(r)['rate']['rate_status'],'operator_override')
        with self.post('/api/pricing/forecast', {'model_id':'el:eleven_v4_turbo','minutes_per_day':30}) as r:
            self.assertIsNotNone(json.load(r)['usd_period'])
        with self.post('/api/pricing/rate',{'action':'delete','model_id':'el:eleven_v4_turbo','confirm':True},
                       extra_headers={'X-Voice-Lab-Setup':'local'}) as r:
            self.assertIsNone(json.load(r)['rate']['usd_per_1000_chars'])

    def test_sync_api_requires_confirmation_and_never_leaks_key(self):
        from rgd_voice_lab import pricing
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post('/api/pricing/sync', {'confirm':True})
        self.assertEqual(ctx.exception.code,403)
        with patch.object(secrets,'get_secret',return_value='SENSITIVE-TEST-KEY'), \
             patch.object(pricing,'sync_elevenlabs',return_value={
                    'synced_at_utc':'2026-10-25T10:00:00Z','model_count':2,
                    'subscription_state':'restricted_or_unavailable'}) as sync:
            with self.post('/api/pricing/sync', {'confirm':True},
                       extra_headers={'X-Voice-Lab-Setup':'local'}) as r:
                response=r.read()
                self.assertNotIn(b'SENSITIVE-TEST-KEY',response)
                self.assertIn(b'model_count',response)
            self.assertEqual(sync.call_args.kwargs['key'],'SENSITIVE-TEST-KEY')

    def test_setup_credentials_require_explicit_local_header(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post('/api/credentials', {'provider':'elevenlabs', 'action':'save',
                                           'key':'fake_key_1234567890123','confirm':True})
        self.assertEqual(ctx.exception.code, 403)

    def test_setup_save_status_and_remove_never_exposes_key(self):
        fake = 'sk_local_lab_only_1234567890123456'
        headers = {'X-Voice-Lab-Setup':'local'}
        with self.post('/api/credentials', {'provider':'elevenlabs', 'action':'save',
                                            'key':fake, 'confirm':True}, extra_headers=headers) as r:
            body = r.read()
        self.assertNotIn(fake.encode(),body)
        self.assertEqual(stat.S_IMODE(secrets.ENV_PATH.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(secrets.ENV_PATH.parent.stat().st_mode), 0o700)
        self.assertIn(fake, secrets.ENV_PATH.read_text())
        with urllib.request.urlopen(self.origin + '/api/credentials') as r:
            data = json.load(r)
        self.assertEqual(data['providers']['elevenlabs']['source'], 'local_env')
        self.assertNotIn(fake,str(data))
        with urllib.request.urlopen(self.origin + '/api/models') as r:
            models=json.load(r)['models']
        self.assertNotIn(fake, json.dumps(models))
        self.assertEqual(next(x['status'] for x in models if x['id']=='el:scribe_v2'),'ready')
        with self.post('/api/credentials', {'provider':'elevenlabs', 'action':'delete',
                                            'confirm':True}, extra_headers=headers) as r:
            self.assertTrue(json.load(r)['removed_from_local_env'])
        self.assertNotIn(fake,secrets.ENV_PATH.read_text())

    def test_setup_rejects_env_injection_and_sensitive_static_paths(self):
        headers={'X-Voice-Lab-Setup':'local'}
        for key in ['abc\nOTHER=oops', 'key$(touch_/tmp/hack)', 'short']:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.post('/api/credentials', {'provider':'openai','action':'save','key':key,
                                                'confirm':True},extra_headers=headers)
            self.assertEqual(ctx.exception.code,400)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.origin+'/.env')
        self.assertEqual(ctx.exception.code,404)

    def test_setup_verify_explicit_only(self):
        headers={'X-Voice-Lab-Setup':'local'}
        with patch.object(core,'verify_api_key',return_value={'verified':True,'provider':'openai'}) as check:
            with self.post('/api/credentials', {'provider':'openai','action':'verify',
                                                'confirm':True}, extra_headers=headers) as r:
                self.assertTrue(json.load(r)['verified'])
            check.assert_called_once_with('openai')

    def test_shared_voice_requires_confirmation_and_custom_header(self):
        payload={'voice_id':'abcdefgh12345678','public_owner_id':'abcdefgh12345678901234567890',
                 'name':'Test Voice','confirm':False}
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post('/api/voices/add-shared', payload, extra_headers={'X-Voice-Lab-Setup':'local'})
        self.assertEqual(e.exception.code,400)
        payload['confirm']=True
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post('/api/voices/add-shared',payload)
        self.assertEqual(e.exception.code,403)
        with patch.object(core,'add_shared_voice',return_value={'added':True,'voice_id':'abc','name':'Test Voice'}) as mocked:
            with self.post('/api/voices/add-shared',payload,extra_headers={'X-Voice-Lab-Setup':'local'}) as resp:
                self.assertTrue(json.load(resp)['added'])
        mocked.assert_called_once()

    def test_secret_rejects_world_readable_file(self):
        with tempfile.TemporaryDirectory() as temp:
            old=secrets.ENV_PATH
            try:
                secrets.ENV_PATH=Path(temp)/'.env'
                secrets.ENV_PATH.write_text('OPENAI_API_KEY=fake_stored_key_1234567890\n')
                secrets.ENV_PATH.chmod(0o644)
                with self.assertRaises(RuntimeError):
                    secrets.get_secret('openai')
            finally:
                secrets.ENV_PATH=old

    def test_import_playable_wav_and_delete(self):
        import io
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\x00\x00'*16000)
        with self.post('/api/import', buf.getvalue(), raw=True) as r:
            data = json.load(r)
        self.assertEqual(data['duration'], 1.0)
        with urllib.request.urlopen(self.origin + '/api/audio/' + data['clip_id']) as r:
            self.assertEqual(r.headers['Content-Type'], 'audio/wav')
            self.assertEqual(r.read(), buf.getvalue())
        with self.post('/api/delete', {'clip_id':data['clip_id']}) as r:
            self.assertTrue(json.load(r)['deleted'])


if __name__ == '__main__':
    unittest.main()
