"""VOICE-0.4D read-only monitor and Unix wire integration (no BLE/hardware)."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
from unittest import TestCase
from unittest.mock import patch

from rgd_voice_lab import vad_lifecycle, visual_feedback
from scripts.voice_visual_monitor import read_status, snapshot


class LegacyPCM:
    @staticmethod
    def read_exact(stream, length):
        return stream.read(length)

    def capture_phrase(self, mic, dest, **kwargs):
        self.read_exact(BytesIO(b'\x00\x00' * 480), 960)
        return {'seconds': .5}

    def transcribe(self, model, dest, *, vad):
        return ('Ciao', .2)


class VisualWireTests(TestCase):
    def test_monitor_requires_v03_and_separates_applied(self):
        status = {'ok': True, 'desired': 'speaking', 'applied': 'speaking',
                  'connected': True, 'voice_event': {
                      'session_id': '1' * 32, 'last_sequence': 2,
                      'last_event': 'playback.started'}}
        self.assertEqual(snapshot(status)[2:], ('playback.started', 'speaking', 'speaking', True))
        with self.assertRaisesRegex(RuntimeError, 'no v0.3'):
            snapshot({'ok': True, 'desired': 'idle'})

    def test_status_monitor_sends_read_only_command(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'test.sock'
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(str(path))
            srv.listen(1)
            received = []

            def receive():
                with srv:
                    conn, _ = srv.accept()
                    with conn:
                        received.append(conn.recv(1024))
                        conn.sendall((json.dumps({'ok': True, 'desired': 'idle',
                            'voice_event': {'last_event': 'voice.ready'}}) + '\n').encode())

            thread = threading.Thread(target=receive, daemon=True)
            thread.start()
            result = read_status(path)
            thread.join(2)
        self.assertEqual(received, [b'{"version":1,"command":"status"}\n'])
        self.assertEqual(result['voice_event']['last_event'], 'voice.ready')

    def test_real_visual_relay_wire_from_actual_pcm_and_stt(self):
        legacy = LegacyPCM()
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'visual.sock'
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(str(path))
            srv.listen(4)
            received = []

            def receive():
                with srv:
                    for _ in range(3):
                        conn, _ = srv.accept()
                        with conn:
                            raw = b''
                            while not raw.endswith(b'\n'):
                                raw += conn.recv(1024)
                            request = json.loads(raw)
                            received.append(request)
                            conn.sendall((json.dumps({
                                'ok': True, 'accepted': True,
                                'source_event': request['event'],
                                'event_sequence': request['sequence']}) + '\n').encode())

            thread = threading.Thread(target=receive, daemon=True)
            thread.start()
            relay = visual_feedback.VisualRelay()
            with patch.dict(os.environ, {'RGD_VOICE_VISUAL_FEEDBACK': '1'}), \
                    patch.object(visual_feedback, '_RELAY', relay), \
                    patch.object(visual_feedback, 'socket_path', return_value=path):
                vad_lifecycle.capture_phrase_observed(
                    legacy, 'test-mic', Path('sample.wav'), vad_mode=2)
                vad_lifecycle.transcribe_observed(legacy, 'test-stt', Path('sample.wav'))
                visual_feedback.emit('playback_started')
                relay._pending.join()
            thread.join(2)
        self.assertEqual([x['event'] for x in received],
                         ['listening.started', 'inference.started', 'playback.started'])
        self.assertEqual([x['sequence'] for x in received], [0, 1, 2])
        self.assertEqual(len({x['session_id'] for x in received}), 1)
        self.assertTrue(all(set(x) == {
            'version', 'command', 'schema_version', 'source',
            'session_id', 'sequence', 'event'} for x in received))
