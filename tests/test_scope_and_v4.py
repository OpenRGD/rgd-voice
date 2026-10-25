"""Offline regression tests for ElevenLabs least-privilege keys and v4.
No real provider traffic, API keys, audio devices or credits used.
"""
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from rgd_voice_lab import core, secrets, server


class DummyResponse:
    def __init__(self, status=200):
        self.status = status
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc_val, tb):
        return False


class ScopedVerificationTests(unittest.TestCase):
    def test_user_403_but_models_and_voices_200_is_verified_limited(self):
        def mock_open(req, timeout=8):
            self.assertEqual(req.get_header('Xi-api-key'), 'key_test_fake_secret_12345678')
            if req.full_url.endswith('/v1/user'):
                raise urllib.error.HTTPError(req.full_url,403,'Forbidden',{},io.BytesIO(b'private'))
            return DummyResponse()
        with patch.object(secrets,'get_secret',return_value='key_test_fake_secret_12345678'):
            with patch.object(core.urllib.request,'urlopen', side_effect=mock_open):
                result=core.verify_api_key('elevenlabs')
        self.assertTrue(result['verified'])
        self.assertEqual(result['state'],'verified_limited')
        self.assertEqual(result['checks']['user_read']['status'],'restricted')
        self.assertEqual(result['checks']['models_read']['status'],'available')
        self.assertEqual(result['checks']['voices_read']['status'],'available')
        self.assertNotIn('key_test_fake_secret_',str(result))
        self.assertTrue(result['no_generation_performed'])

    def test_all_403_does_not_claim_invalid_api_key(self):
        with patch.object(secrets,'get_secret',return_value='key_test_fake_secret_12345678'):
            with patch.object(core.urllib.request,'urlopen',side_effect=urllib.error.HTTPError('https://api.elevenlabs.io/v1/models',403,'Forbidden',{},None)):
                result=core.verify_api_key('elevenlabs')
        self.assertFalse(result['verified'])
        self.assertEqual(result['state'],'limited_unconfirmed')
        self.assertIn('NON',result['message'])

    def test_all_401_marks_authentication_refused(self):
        with patch.object(secrets,'get_secret',return_value='key_test_fake_secret_12345678'):
            with patch.object(core.urllib.request,'urlopen',side_effect=urllib.error.HTTPError('https://api.elevenlabs.io/v1/models',401,'Unauthorized',{},None)):
                result=core.verify_api_key('elevenlabs')
        self.assertFalse(result['verified'])
        self.assertEqual(result['state'],'unauthorized')

    def test_unreachable_keeps_verification_inconclusive(self):
        with patch.object(secrets,'get_secret',return_value='key_test_fake_secret_12345678'):
            with patch.object(core.urllib.request,'urlopen',side_effect=urllib.error.URLError('network down')):
                result=core.verify_api_key('elevenlabs')
        self.assertEqual(result['state'],'inconclusive')
        self.assertFalse(result['verified'])


class ElevenV4AdapterTests(unittest.TestCase):
    def test_v4_has_distinct_candidate_model(self):
        model=core.MODELS['el:eleven_v4']
        self.assertEqual(model['type'],'tts')
        self.assertEqual(model['model'],'eleven_v4')

    def test_v4_uses_text_to_dialogue_with_single_speaker(self):
        from unittest.mock import MagicMock
        with tempfile.TemporaryDirectory() as temp:
            dest=Path(temp)/'v4.wav'
            called={}
            def binary(url,headers,data):
                called['url']=url
                called['headers']=headers
                called['body']=json.loads(data)
                return b'\x00\x00'*8000
            with patch.object(core,'require_ready'):
                with patch.object(secrets,'get_secret',return_value='key_fake_do_not_expose'):
                    with patch.object(core,'get_binary',side_effect=binary):
                        out=core.synthesize('el:eleven_v4','Hello [curious] world','JBFqnCBsd6RMkjVDRZzb',dest)
            self.assertTrue(dest.is_file())
            self.assertEqual(out['model'],'el:eleven_v4')
            self.assertIn('/v1/text-to-dialogue?', called['url'])
            self.assertEqual(called['body']['model_id'],'eleven_v4')
            self.assertEqual(called['body']['inputs'],[{'text':'Hello [curious] world','voice_id':'JBFqnCBsd6RMkjVDRZzb'}])
            self.assertNotIn('key_fake_do_not_expose',str(out))

    def test_flash_still_uses_existing_text_to_speech(self):
        with tempfile.TemporaryDirectory() as temp:
            dest=Path(temp)/'flash.wav'
            called={}
            def binary(url,headers,data):
                called['url']=url
                called['body']=json.loads(data)
                return b'\x00\x00'*8000
            with patch.object(core,'require_ready'):
                with patch.object(secrets,'get_secret',return_value='key_fake_do_not_expose'):
                    with patch.object(core,'get_binary',side_effect=binary):
                        core.synthesize('el:eleven_flash_v2_5','Ciao', 'JBFqnCBsd6RMkjVDRZzb',dest)
            self.assertIn('/v1/text-to-speech/', called['url'])
            self.assertEqual(called['body']['model_id'],'eleven_flash_v2_5')

if __name__ == '__main__':
    unittest.main()
