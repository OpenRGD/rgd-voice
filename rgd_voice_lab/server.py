"""Local-only HTTP backend for Voice Lab. No CORS, no motor authority, no RGD mutation."""
from __future__ import annotations

import argparse
import collections
import json
import mimetypes
import os
import re
import socket
import sys
import threading
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import __version__
from . import core
from . import secrets
from . import voice_profile
from . import pricing

HERE = Path(__file__).parent
STATIC = HERE / 'static'
RECENT = collections.deque(maxlen=40)
SETTINGS_LOCK = threading.Lock()
VERIFICATION_STATE = {}    # Read-only probe results for current process, never secrets.


def ensure_dirs():
    core.APP_HOME.mkdir(parents=True, exist_ok=True, mode=0o700)
    (core.APP_HOME / 'clips').mkdir(parents=True, exist_ok=True, mode=0o700)


def settings_path():
    return core.APP_HOME / 'preferences.json'


def get_settings():
    defaults = {'source': '', 'sink': '', 'default_stt': 'fw:base:5', 'default_tts': 'piper:it_IT-paola-medium'}
    p = settings_path()
    if not p.is_file():
        return defaults
    try:
        data = json.loads(p.read_text())
        return {**defaults, **{key: data[key] for key in defaults if key in data}}
    except (OSError, ValueError):
        return defaults


def save_settings(new):
    if not isinstance(new, dict):
        raise ValueError('Configurazione non valida')
    previous = get_settings()
    sources = core.endpoints('sources')
    sinks = core.endpoints('sinks')
    for name in ('source', 'sink'):
        if name in new:
            if not isinstance(new[name], str) or (new[name] and new[name] not in (sources if name == 'source' else sinks)):
                raise ValueError(f'Dispositivo {name} non disponibile')
            previous[name] = new[name]
    for name, kind in [('default_stt','stt'), ('default_tts','tts')]:
        if name in new:
            if new[name] not in core.MODELS or core.MODELS[new[name]]['type'] != kind:
                raise ValueError(f'Modello {name} non valido')
            previous[name] = new[name]
    with SETTINGS_LOCK:
        target = settings_path()
        tmp = target.with_suffix('.tmp')
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(previous, f, indent=2)
        tmp.replace(target)
    return previous


def clip_path(clip_id):
    if not isinstance(clip_id, str) or not re.fullmatch(r'[0-9a-f]{32}', clip_id):
        raise ValueError('Identificativo clip non valido')
    return core.APP_HOME / 'clips' / (clip_id + '.wav')


def existing_clip(clip_id):
    target = clip_path(clip_id)
    if not target.is_file():
        raise FileNotFoundError('Registrazione non trovata')
    return target


def new_clip_path():
    id_ = uuid.uuid4().hex
    return id_, clip_path(id_)


def add_recent(kind, model, elapsed):
    # Privacy: store only model and latency in RAM, not raw speech or texts.
    RECENT.appendleft({'kind': kind, 'model': model, 'seconds': elapsed})


def allowed_origin(value, port):
    if not value:
        return True
    return value in (f'http://127.0.0.1:{port}', f'http://localhost:{port}', f'http://[::1]:{port}')


class Handler(BaseHTTPRequestHandler):
    server_version = f'RGDVoiceLab/{__version__}'
    protocol_version = 'HTTP/1.1'

    def log_message(self, fmt, *args):
        # Explicitly avoid logging submitted text, audio, URLs with secrets.
        if os.environ.get('RGD_VOICE_LAB_DEBUG') == '1':
            print('[HTTP] ' + (fmt % args), file=sys.stderr)

    def reply(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def file(self, filename, mime):
        data = filename.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; connect-src 'self'; media-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def check_host(self):
        value = self.headers.get('Host', '')
        port = self.server.server_port
        if value not in (f'127.0.0.1:{port}', f'localhost:{port}', f'[::1]:{port}'):
            raise PermissionError('Voice Lab è disponibile soltanto su localhost')
        if not allowed_origin(self.headers.get('Origin', ''), port):
            raise PermissionError('Richiesta cross-origin non autorizzata')

    def check_setup_mutation(self):
        # JSON + custom header prevents cross-site forms/simple browser requests.
        # Combined with loopback-only binding, strict Host/Origin, no CORS.
        if self.headers.get('Content-Type', '').split(';', 1)[0] != 'application/json':
            raise PermissionError('Setup richiede una richiesta JSON')
        if self.headers.get('X-Voice-Lab-Setup', '') != 'local':
            raise PermissionError('Setup non autorizzato: header locale mancante')

    def body(self, raw=False, max_bytes=core.MAX_AUDIO):
        try:
            size = int(self.headers.get('Content-Length', '-1'))
        except ValueError:
            size = -1
        if size < 0 or size > max_bytes:
            raise ValueError('Corpo richiesta assente o troppo grande')
        content = self.rfile.read(size)
        if raw:
            return content
        try:
            result = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('JSON non valido') from None
        if not isinstance(result, dict):
            raise ValueError('È richiesto un oggetto JSON')
        return result

    def do_GET(self):
        try:
            self.check_host()
            parsed = urlparse(self.path)
            path = parsed.path
            if path == '/':
                return self.file(STATIC / 'index.html', 'text/html; charset=utf-8')
            if path == '/styles.css':
                return self.file(STATIC / 'styles.css', 'text/css; charset=utf-8')
            if path == '/app.js':
                return self.file(STATIC / 'app.js', 'text/javascript; charset=utf-8')
            if path == '/api/health':
                audio = core.get_audio_state()
                models = core.catalog()
                return self.reply(200, {'version': __version__, 'robot': 'Paky-01 · profilo host audio non identità RGD',
                                        'host': socket.gethostname(), 'audio': audio['available'],
                                        'ready_local': sum(m['status'] == 'ready' for m in models if m['mode'] == 'local'),
                                        'connected_cloud': sum(m['status'] == 'ready' for m in models if m['mode'] == 'cloud'),
                                        'python': sys.executable,
                                        'ffmpeg': bool(core.shutil.which('ffmpeg')),
                                        'piper_runtime': core.piper_engine.ENGINE.status()})
            if path == '/api/models':
                return self.reply(200, {'models': core.catalog()})
            if path == '/api/costs':
                return self.reply(200, pricing.dashboard(core.APP_HOME))
            if path == '/api/devices':
                return self.reply(200, core.get_audio_state())
            if path == '/api/settings':
                return self.reply(200, get_settings())
            if path == '/api/recent':
                return self.reply(200, {'items': list(RECENT)})
            if path == '/api/voice-default':
                return self.reply(200, {'profile': voice_profile.read(), 'robot_instance_id': 'paky-01'})
            if path == '/api/credentials':
                return self.reply(200, {'providers': secrets.status(),
                                        'checks': VERIFICATION_STATE.copy(),
                                        'storage': '~/.config/openrgd/voice-lab/.env',
                                        'plaintext': True})
            if path == '/api/voice-library':
                q = parse_qs(parsed.query)
                return self.reply(200, core.voice_library(
                    q.get('provider', ['piper'])[0],
                    query=q.get('query', [''])[0],
                    language=q.get('language', [''])[0],
                    page=q.get('page', ['0'])[0],
                    after=q.get('after', [''])[0]))
            if path == '/api/voices':
                provider = parse_qs(parsed.query).get('provider', [''])[0]
                model_id = parse_qs(parsed.query).get('model_id', [''])[0]
                return self.reply(200, {'voices': core.list_voices(provider, model_id)})
            if path.startswith('/api/audio/'):
                clip_id = path[len('/api/audio/'):]
                return self.file(existing_clip(clip_id), 'audio/wav')
            return self.reply(404, {'error': 'Percorso non trovato'})
        except Exception as e:
            return self.fail(e)

    def do_POST(self):
        try:
            self.check_host()
            path = urlparse(self.path).path
            if path == '/api/import':
                if self.headers.get('Content-Type', '').split(';', 1)[0] != 'audio/wav':
                    raise ValueError('Carica un file WAV (audio/wav)')
                data = self.body(raw=True)
                if not data.startswith(b'RIFF') or data[8:12] != b'WAVE':
                    raise ValueError('Il file non è WAV RIFF')
                id_, path_ = new_clip_path()
                path_.write_bytes(data)
                try:
                    duration = core.audio_duration(path_)
                    if not 0.3 <= duration <= 180:
                        raise ValueError('Durata WAV non consentita (0,3–180s)')
                except Exception:
                    path_.unlink(missing_ok=True)
                    raise
                return self.reply(200, {'clip_id': id_, 'duration': duration, 'name': 'WAV importato'})
            data = self.body(max_bytes=32000)
            if path == '/api/pricing/sync':
                self.check_setup_mutation()
                if data.get('confirm') is not True:
                    raise ValueError('Sincronizzazione esterna priva di conferma')
                key = secrets.get_secret('elevenlabs')
                result = pricing.sync_elevenlabs(core.APP_HOME, key=key, request_json=core.get_json)
                return self.reply(200, {'synced_at_utc': result['synced_at_utc'],
                                        'model_count':result['model_count'],
                                        'subscription_state':result['subscription_state'],
                                        'usd_tariffs_synced':False})
            if path == '/api/pricing/rate':
                self.check_setup_mutation()
                if data.get('confirm') is not True:
                    raise ValueError('Tariffa locale: conferma esplicita richiesta')
                if data.get('action') == 'save':
                    rate = pricing.set_override(core.APP_HOME, data.get('model_id'),
                                  data.get('unit_price_usd'), data.get('unit'))
                elif data.get('action') == 'delete':
                    rate = pricing.delete_override(core.APP_HOME, data.get('model_id'))
                else:
                    raise ValueError('Operazione tariffa non supportata')
                return self.reply(200, {'rate':rate})
            if path == '/api/pricing/forecast':
                return self.reply(200, pricing.forecast(core.APP_HOME, data.get('model_id'),
                    minutes_per_day=data.get('minutes_per_day', 60),
                    chars_per_spoken_minute=data.get('chars_per_spoken_minute', 900),
                    days=data.get('days', 30)))
            if path == '/api/credentials':
                self.check_setup_mutation()
                if data.get('confirm') is not True:
                    raise ValueError('Modifica credenziali priva di conferma')
                provider = data.get('provider')
                if data.get('action') == 'save':
                    res = secrets.set_secret(provider, data.get('key'))
                    VERIFICATION_STATE.pop(provider, None)
                elif data.get('action') == 'delete':
                    res = secrets.delete_secret(provider)
                    VERIFICATION_STATE.pop(provider, None)
                elif data.get('action') == 'verify':
                    res = core.verify_api_key(provider)
                    VERIFICATION_STATE[provider] = res
                else:
                    raise ValueError('Azione credenziali non valida')
                return self.reply(200, res)
            if path == '/api/voice-default':
                self.check_setup_mutation()
                if data.get('confirm') is not True:
                    raise ValueError('Selezione della voce predefinita richiede conferma')
                return self.reply(200, {'profile': voice_profile.save(data)})
            if path == '/api/voices/add-shared':
                self.check_setup_mutation()
                if data.get('confirm') is not True:
                    raise ValueError('Aggiunta voce non autorizzata')
                return self.reply(200, core.add_shared_voice(
                    data.get('public_owner_id'), data.get('voice_id'), data.get('name')))
            if path == '/api/settings':
                return self.reply(200, {'settings': save_settings(data)})
            if path == '/api/install':
                if data.get('confirm') is not True:
                    raise ValueError('Installazione non autorizzata: conferma esplicita richiesta')
                with core.WORK_LOCK:
                    result = core.install_model(data.get('model_id'))
                return self.reply(200, result)
            if path == '/api/record':
                preferences = get_settings()
                device = core.select_endpoint('sources', data.get('source') or preferences['source'])
                if not device:
                    raise RuntimeError('Nessun microfono rilevato')
                id_, out = new_clip_path()
                try:
                    with core.WORK_LOCK:
                        stats = core.capture(device, data.get('seconds', 8), out)
                except Exception:
                    out.unlink(missing_ok=True)
                    raise
                return self.reply(200, {'clip_id': id_, 'device': device, **stats})
            if path == '/api/transcribe':
                m = core.MODELS.get(data.get('model_id'))
                if m and m['mode'] == 'cloud' and data.get('confirm_cloud') is not True:
                    raise ValueError('Richiesta cloud priva di conferma esplicita')
                clip = existing_clip(data.get('clip_id'))
                model_id = data.get('model_id')
                with core.WORK_LOCK:
                    result = core.transcribe(model_id, clip)
                if isinstance(data.get('reference'), str) and data['reference'].strip():
                    result['wer'] = core.wer(data['reference'], result['text'])
                add_recent('stt', model_id, result['seconds'])
                return self.reply(200, result)
            if path == '/api/synthesize':
                m = core.MODELS.get(data.get('model_id'))
                if m and m['mode'] == 'cloud' and data.get('confirm_cloud') is not True:
                    raise ValueError('Richiesta cloud priva di conferma esplicita')
                id_, out = new_clip_path()
                model_id = data.get('model_id')
                try:
                    with core.WORK_LOCK:
                        result = core.synthesize(model_id, data.get('text'), data.get('voice_id'), out,
                                                 controls=data.get('controls'))
                except Exception:
                    out.unlink(missing_ok=True)
                    raise
                add_recent('tts', model_id, result['seconds'])
                return self.reply(200, {'clip_id': id_, **result})
            if path == '/api/play':
                clip = existing_clip(data.get('clip_id'))
                preferences = get_settings()
                sink = core.select_endpoint('sinks', data.get('sink') or preferences['sink'])
                if not sink:
                    raise RuntimeError('Nessun altoparlante rilevato')
                return self.reply(200, core.playback(clip, sink))
            if path == '/api/delete':
                clip = existing_clip(data.get('clip_id'))
                clip.unlink()
                return self.reply(200, {'deleted': True})
            return self.reply(404, {'error': 'Percorso non trovato'})
        except Exception as e:
            return self.fail(e)

    def fail(self, e):
        if isinstance(e, PermissionError):
            code = 403
        elif isinstance(e, FileNotFoundError):
            code = 404
        elif isinstance(e, (ValueError, TypeError)):
            code = 400
        else:
            code = 503
        # Keep error messages readable, no stack traces or credentials.
        try:
            self.reply(code, {'error': str(e)[:380]})
        except (BrokenPipeError, ConnectionResetError):
            pass


def main():
    parser = argparse.ArgumentParser(description='OpenRGD Voice Lab v0.3.1 (localhost only)')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('La porta deve essere compresa fra 1024 e 65535')
    ensure_dirs()
    # Do not expose services without authentication beyond loopback.
    httpd = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    httpd.daemon_threads = True
    print(f'OPENRGD VOICE LAB {__version__} — http://127.0.0.1:{args.port}', flush=True)
    print('Local-only • audio tests only • no CAN / motors / RGD changes', flush=True)
    try:
        httpd.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == '__main__':
    main()
