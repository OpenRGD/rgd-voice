"""Offline Voice Studio regression suite. No ElevenLabs billing, no robot/audio I/O."""
import json
import math
import os
import stat
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from rgd_voice_lab import core, voice_controls, voice_profile, say


class ControlsTests(unittest.TestCase):
    def test_valid_defaults_and_ranges(self):
        self.assertEqual(voice_controls.normalize()['speed'], 1.0)
        self.assertEqual(voice_controls.normalize({'emotion': 'curious'})['emotion'], 'curious')
        self.assertTrue(voice_controls.needs_local_fx(voice_controls.normalize({'pitch_semitones': 1})))

    def test_rejects_unrecognized_values_nan_or_boolean(self):
        for setting in [{'random':'oops'}, {'stability':float('nan')}, {'speed':5}, {'speed':True},
                        {'pitch_semitones':100}, {'emotion':'[system] bad'}, {'speaker_boost':'true'}]:
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                voice_controls.normalize(setting)

    def test_emotion_and_style_are_model_specific(self):
        with self.assertRaises(ValueError):
            voice_controls.validate_for_model(core.MODELS['piper:it_IT-serena-medium'],
                                               voice_controls.normalize({'emotion':'curious'}))
        with self.assertRaises(ValueError):
            voice_controls.validate_for_model(core.MODELS['el:eleven_v4'],
                                               voice_controls.normalize({'style':0.8}))

    def test_expressive_tags_are_only_for_v4(self):
        c=voice_controls.normalize({'emotion':'curious'})
        self.assertEqual(voice_controls.expressive_text('Ciao!',c,core.MODELS['el:eleven_v4']), '[curious] Ciao!')
        self.assertEqual(voice_controls.expressive_text('Ciao!',c,core.MODELS['piper:it_IT-paola-medium']), 'Ciao!')

    def test_v4_studio_sends_only_supported_native_options(self):
        captured={}
        def stub(url, headers, data):
            captured['url']=url
            captured['payload']=json.loads(data)
            return b'\x01\x00'*16000
        with tempfile.TemporaryDirectory() as d:
            out=Path(d)/'speech.wav'
            with patch.object(core,'require_ready'):
                with patch.object(core.secrets,'get_secret',return_value='dummy_key'):
                    with patch.object(core,'get_binary',side_effect=stub):
                        result=core.synthesize('el:eleven_v4','Ciao!', 'JBFqnCBsd6RMkjVDRZzb',out,
                                               controls={'emotion':'curious','stability':0.45,'similarity_boost':0.8})
        self.assertIn('/v1/text-to-speech/',captured['url'])
        self.assertEqual(captured['payload']['text'],'[curious] Ciao!')
        self.assertEqual(captured['payload']['voice_settings'],{'stability':0.45,'similarity_boost':0.8})
        self.assertNotIn('speed',captured['payload'])
        self.assertNotIn('pitch',captured['payload'])
        self.assertFalse(result['postprocess_applied'])

    def test_flash_native_style_and_no_native_pitch(self):
        c=voice_controls.normalize({'style':0.2,'speed':1.1,'pitch_semitones':1})
        native=voice_controls.eleven_native_settings(c,core.MODELS['el:eleven_flash_v2_5'])
        self.assertEqual(native['style'],0.2)
        self.assertNotIn('speed',native)
        self.assertNotIn('pitch_semitones',native)

    @unittest.skipUnless(core.shutil.which('ffmpeg'), 'ffmpeg unavailable')
    def test_local_fx_tempo_and_pitch(self):
        import array
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'tone.wav'
            rate=16000
            samples=array.array('h',(int(4000*math.sin(2*math.pi*440*i/rate)) for i in range(rate)))
            with wave.open(str(path),'wb') as w:
                w.setframerate(rate);w.setnchannels(1);w.setsampwidth(2);w.writeframes(samples.tobytes())
            controls=voice_controls.normalize({'speed':0.9,'pitch_semitones':2})
            result=voice_controls.apply_local_fx(path, controls)
            self.assertTrue(result)
            with wave.open(str(path),'rb') as w:
                self.assertEqual(w.getframerate(),16000)
                self.assertEqual(w.getnchannels(),1)
                self.assertTrue(15800 < w.getnframes() < 20500)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.old=core.APP_HOME
        core.APP_HOME=Path(self.temp.name)

    def tearDown(self):
        core.APP_HOME=self.old
        self.temp.cleanup()

    def test_private_file_and_round_trip(self):
        # The test checks profile serialization, not whether optional FFmpeg
        # is installed on this CI host. Mock only the dependency probe.
        with patch.object(core,'availability',return_value='ready'), \
                patch('rgd_voice_lab.voice_controls.shutil.which', return_value='/test/ffmpeg'):
            p=voice_profile.save({'robot_instance_id':'paky-01', 'model_id':'piper:it_IT-serena-high',
                                  'voice_id':'it_IT-serena-high','controls':{'speed':1.1}})
        self.assertEqual(p['scope'],'audio_output_only')
        self.assertEqual(voice_profile.read()['voice_id'],'it_IT-serena-high')
        self.assertEqual(stat.S_IMODE(voice_profile.path().stat().st_mode),0o600)
        self.assertEqual(stat.S_IMODE(voice_profile.path().parent.stat().st_mode),0o700)
        self.assertNotIn('ELEVENLABS_API_KEY',voice_profile.path().read_text())

    def test_reject_wrong_instance_and_voice(self):
        with self.assertRaises(ValueError):
            voice_profile.save({'robot_instance_id':'paky-02','model_id':'piper:it_IT-serena-high',
                                'voice_id':'it_IT-serena-high'})
        with self.assertRaises(ValueError):
            voice_profile.save({'model_id':'piper:it_IT-serena-high', 'voice_id':'voice_fake'})

    def test_say_dry_run_does_not_call_synthesis(self):
        with patch.object(core,'availability',return_value='ready'):
            voice_profile.save({'model_id':'piper:it_IT-serena-medium','voice_id':'it_IT-serena-medium'})
        with patch.object(core,'synthesize') as synth:
            result=say.main(['--text','Ciao Paky','--dry-run'])
        self.assertEqual(result,0)
        synth.assert_not_called()

    def test_cloud_needs_explicit_consent(self):
        with patch.object(core,'availability',return_value='ready'):
            voice_profile.save({'model_id':'el:eleven_v4','voice_id':'JBFqnCBsd6RMkjVDRZzb'})
        with patch.object(core,'synthesize') as synth:
            with self.assertRaises(SystemExit) as error:
                say.main(['--text','Ciao Pasquale','--play'])
        self.assertEqual(error.exception.code,2)
        synth.assert_not_called()


if __name__=='__main__':
    unittest.main()

class ProfileHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import threading
        from rgd_voice_lab import server
        from rgd_voice_lab import secrets
        cls.tmp = tempfile.TemporaryDirectory()
        cls.old_home = core.APP_HOME
        cls.old_env = secrets.ENV_PATH
        core.APP_HOME = Path(cls.tmp.name)
        secrets.ENV_PATH = Path(cls.tmp.name) / 'config' / '.env'
        server.ensure_dirs()
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.httpd.server_port}'

    @classmethod
    def tearDownClass(cls):
        from rgd_voice_lab import secrets
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)
        core.APP_HOME=cls.old_home
        secrets.ENV_PATH=cls.old_env
        cls.tmp.cleanup()

    def post(self, obj, setup=False):
        import urllib.request
        headers={'Content-Type':'application/json'}
        if setup: headers['X-Voice-Lab-Setup']='local'
        req=urllib.request.Request(self.base+'/api/voice-default',data=json.dumps(obj).encode(),headers=headers,method='POST')
        return urllib.request.urlopen(req,timeout=5)

    def test_profile_requires_setup_header_and_confirmation(self):
        import urllib.error
        obj={'model_id':'piper:it_IT-serena-medium','voice_id':'it_IT-serena-medium','confirm':True}
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post(obj)
        self.assertEqual(e.exception.code,403)
        obj['confirm']=False
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.post(obj,setup=True)
        self.assertEqual(e.exception.code,400)

    def test_profile_round_trip_via_http(self):
        import urllib.request
        obj={'robot_instance_id':'paky-01','model_id':'piper:it_IT-serena-medium','voice_id':'it_IT-serena-medium',
             'controls':{'speed':1.0,'pitch_semitones':0},'confirm':True}
        with patch.object(core,'availability',return_value='ready'):
            with self.post(obj,setup=True) as response:
                record=json.load(response)['profile']
        self.assertEqual(record['robot_instance_id'],'paky-01')
        with urllib.request.urlopen(self.base+'/api/voice-default',timeout=5) as response:
            stored=json.load(response)['profile']
        self.assertEqual(stored['controls']['speed'],1.0)
        self.assertNotIn('key',str(stored))
