"""Provider catalog, PCM I/O and provider adapters. No robot actuation or RGD identity."""
from __future__ import annotations

import array
import math
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from pathlib import Path

from . import secrets
from . import voice_controls
from . import visual_feedback

APP_HOME = Path(os.environ.get('RGD_VOICE_LAB_DATA', str(Path.home() / '.local/share/openrgd/voice-lab'))).expanduser()
BIND_SOURCE_MATCH = 'alsa_input.usb-BC_Master_1610USB_'
BIND_SINK_MATCH = 'bluez_sink.60_AB_D2_0D_E2_01.a2dp_sink'
LEGACY_PIPER = Path.home() / 'openrgd/rgd-voice-paky01-smoke-v0.1/models/piper/it_IT-paola-medium.onnx'
MAX_AUDIO = 10 * 1024 * 1024
WORK_LOCK = threading.Lock()    # Avoid concurrent heavy CPU jobs on Paky-01.
MODEL_CACHE = {}

CATALOG = [
    dict(id='fw:base:5', name='Whisper Base · Beam 5', provider='faster-whisper', type='stt', mode='local', model='base', beam=5, note='FAST · Paky-01 benchmark: WER 13,7%, 1,47 s (3 frasi)'),
    dict(id='fw:small:1', name='Whisper Small · Beam 1', provider='faster-whisper', type='stt', mode='local', model='small', beam=1, note='ACCURATE · Paky-01 benchmark: WER 7,8%, 4,17 s (3 frasi)'),
    dict(id='fw:tiny:5', name='Whisper Tiny · Beam 5', provider='faster-whisper', type='stt', mode='local', model='tiny', beam=5, note='Ultra-light · da misurare'),
    dict(id='el:scribe_v2', name='Scribe v2', provider='ElevenLabs', type='stt', mode='cloud', model='scribe_v2', env='ELEVENLABS_API_KEY', note='Trascrizione cloud · lingua italiana'),
    dict(id='oa:gpt-transcribe', name='OpenAI · GPT Transcribe', provider='OpenAI', type='stt', mode='cloud', model='gpt-transcribe', env='OPENAI_API_KEY', note='Modello STT raccomandato per file audio'),
    dict(id='oa:gpt-4o-mini-transcribe', name='GPT-4o mini Transcribe', provider='OpenAI', type='stt', mode='cloud', model='gpt-4o-mini-transcribe', env='OPENAI_API_KEY', note='Trascrizione cloud · legacy, da migrare entro febbraio 2027'),
    dict(id='oa:gpt-4o-transcribe', name='GPT-4o Transcribe', provider='OpenAI', type='stt', mode='cloud', model='gpt-4o-transcribe', env='OPENAI_API_KEY', note='Trascrizione cloud · legacy, da migrare entro febbraio 2027'),
    dict(id='piper:it_IT-paola-medium', name='Piper · Paola medium', provider='Piper', type='tts', mode='local', model='it_IT-paola-medium', language='it_IT', quality='medium', note='Voce italiana locale · già collaudata'),
    dict(id='piper:it_IT-serena-medium', name='Piper · Serena medium', provider='Piper', type='tts', mode='local', model='it_IT-serena-medium', language='it_IT', quality='medium', note='Voce italiana neutra · Serena'),
    dict(id='piper:it_IT-serena-high', name='Piper · Serena high', provider='Piper', type='tts', mode='local', model='it_IT-serena-high', language='it_IT', quality='high', note='Serena italiana in alta qualità · ~114 MB'),
    dict(id='piper:it_IT-riccardo-x_low', name='Piper · Riccardo x-low', provider='Piper', type='tts', mode='local', model='it_IT-riccardo-x_low', language='it_IT', quality='x_low', note='Seconda voce italiana, modello leggero · da collaudare'),
    dict(id='piper:en_US-lessac-medium', name='Piper · Lessac · American English', provider='Piper', type='tts', mode='local', model='en_US-lessac-medium', language='en_US', quality='medium', note='Voce inglese statunitense, naturale e versatile'),
    dict(id='piper:en_US-amy-medium', name='Piper · Amy · American English', provider='Piper', type='tts', mode='local', model='en_US-amy-medium', language='en_US', quality='medium', note='Voce femminile inglese US'),
    dict(id='piper:en_US-ryan-high', name='Piper · Ryan · American English', provider='Piper', type='tts', mode='local', model='en_US-ryan-high', language='en_US', quality='high', note='Voce inglese maschile in alta qualità'),
    dict(id='piper:en_US-joe-medium', name='Piper · Joe · American English', provider='Piper', type='tts', mode='local', model='en_US-joe-medium', language='en_US', quality='medium', note='Voce inglese US per conversazione'),
    dict(id='piper:en_GB-alan-medium', name='Piper · Alan · British English', provider='Piper', type='tts', mode='local', model='en_GB-alan-medium', language='en_GB', quality='medium', note='Voce maschile inglese britannico'),
    dict(id='piper:en_GB-alba-medium', name='Piper · Alba · British English', provider='Piper', type='tts', mode='local', model='en_GB-alba-medium', language='en_GB', quality='medium', note='Voce femminile inglese britannico'),
    dict(id='piper:es_ES-davefx-medium', name='Piper · DaveFX · Español', provider='Piper', type='tts', mode='local', model='es_ES-davefx-medium', language='es_ES', quality='medium', note='Voce spagnola della Spagna'),
    dict(id='piper:es_MX-ald-medium', name='Piper · ALD · Español México', provider='Piper', type='tts', mode='local', model='es_MX-ald-medium', language='es_MX', quality='medium', note='Voce spagnola del Messico'),
    dict(id='piper:fr_FR-siwis-medium', name='Piper · Siwis · Français', provider='Piper', type='tts', mode='local', model='fr_FR-siwis-medium', language='fr_FR', quality='medium', note='Voce francese'),
    dict(id='piper:de_DE-thorsten-medium', name='Piper · Thorsten · Deutsch', provider='Piper', type='tts', mode='local', model='de_DE-thorsten-medium', language='de_DE', quality='medium', note='Voce tedesca'),
    dict(id='piper:de_DE-thorsten_emotional-medium', name='Piper · Thorsten Emotional · Deutsch', provider='Piper', type='tts', mode='local', model='de_DE-thorsten_emotional-medium', language='de_DE', quality='medium', note='Variante espressiva in tedesco'),
    dict(id='piper:pt_BR-faber-medium', name='Piper · Faber · Português BR', provider='Piper', type='tts', mode='local', model='pt_BR-faber-medium', language='pt_BR', quality='medium', note='Voce portoghese brasiliana'),
    dict(id='piper:nl_NL-pim-medium', name='Piper · Pim · Nederlands', provider='Piper', type='tts', mode='local', model='nl_NL-pim-medium', language='nl_NL', quality='medium', note='Voce olandese'),
    dict(id='piper:sw_CD-lanfrica-medium', name='Piper · Lanfrica · Kiswahili', provider='Piper', type='tts', mode='local', model='sw_CD-lanfrica-medium', language='sw_CD', quality='medium', note='Voce Swahili, per prove linguistiche'),
    dict(id='el:eleven_flash_v2_5', name='ElevenLabs · Flash v2.5', provider='ElevenLabs', type='tts', mode='cloud', model='eleven_flash_v2_5', env='ELEVENLABS_API_KEY', note='Voce cloud conversazionale, PCM 16 kHz'),
    dict(id='el:eleven_multilingual_v2', name='ElevenLabs · Multilingual v2', provider='ElevenLabs', type='tts', mode='cloud', model='eleven_multilingual_v2', env='ELEVENLABS_API_KEY', note='Voce cloud espressiva, PCM 16 kHz'),
    dict(id='el:eleven_v4', name='ElevenLabs · Eleven v4 · Voice Studio', provider='ElevenLabs', type='tts', mode='cloud', model='eleven_v4', env='ELEVENLABS_API_KEY', note='Nuovo modello espressivo · Text to Dialogue REST, audio PCM 16 kHz (test esplicito con crediti)'),
    dict(id='el:eleven_v4_turbo', name='ElevenLabs · Eleven v4 Turbo · WebSocket', provider='ElevenLabs', type='tts', mode='cloud', model='eleven_v4_turbo', env='ELEVENLABS_API_KEY', note='Real-time TTD WebSocket, anteprima raccolta su WAV; richiede websockets + ffmpeg. Tariffa specifica da verificare.'),
    dict(id='oa:gpt-4o-mini-tts', name='OpenAI · GPT-4o mini TTS', provider='OpenAI', type='tts', mode='cloud', model='gpt-4o-mini-tts', env='OPENAI_API_KEY', note='Voce cloud, WAV 24 kHz'),
]
MODELS = {model['id']: model for model in CATALOG}
PIPER_ALLOWED = frozenset(m['model'] for m in CATALOG if m['provider'] == 'Piper')
OPENAI_VOICE_IDS = frozenset(('alloy', 'ash', 'ballad', 'coral', 'echo', 'fable', 'onyx', 'nova', 'sage', 'shimmer', 'verse', 'marin', 'cedar'))


def run(argv, timeout=12):
    """Fixed-argv subprocess, never shell=True."""
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    if p.returncode:
        raise RuntimeError(f'{argv[0]}: {p.stderr.strip()[-350:] or "exit " + str(p.returncode)}')
    return p.stdout


def endpoints(kind):
    if kind not in ('sources', 'sinks'):
        raise ValueError('Invalid endpoint kind')
    raw = run(['pactl', 'list', 'short', kind])
    choices = []
    for line in raw.splitlines():
        cols = line.split()
        if len(cols) < 2 or not cols[0].isdigit():
            continue
        name = cols[1]
        if kind == 'sources' and (name.endswith('.monitor') or name.startswith('bluez_sink.')):
            continue
        choices.append(name)
    return choices


def select_endpoint(kind, preferred=''):
    available = endpoints(kind)
    if preferred:
        if preferred not in available:
            raise RuntimeError('Il dispositivo selezionato non è disponibile. Apri Dispositivi.')
        return preferred
    token = BIND_SOURCE_MATCH if kind == 'sources' else BIND_SINK_MATCH
    return next((s for s in available if token in s), available[0] if available else '')


def get_audio_state():
    try:
        src = endpoints('sources')
        sink = endpoints('sinks')
        return {'sources': src, 'sinks': sink, 'input_suggested': select_endpoint('sources'),
                'output_suggested': select_endpoint('sinks'), 'available': True}
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as ex:
        return {'sources': [], 'sinks': [], 'input_suggested': '', 'output_suggested': '',
                'available': False, 'error': str(ex)}


def piper_path(name='it_IT-paola-medium'):
    if name not in PIPER_ALLOWED:
        raise ValueError('Modello Piper non consentito')
    candidates = [os.environ.get('PIPER_VOICE_PATH', '') if name == 'it_IT-paola-medium' else '',
                  str(APP_HOME / 'models/piper' / (name + '.onnx')), str(LEGACY_PIPER) if name == 'it_IT-paola-medium' else '']
    for path in candidates:
        if path and Path(path).is_file() and Path(str(path) + '.json').is_file():
            return Path(path)
    return None


def whisper_path(name):
    if name not in ('tiny', 'base', 'small'):
        raise ValueError('Modello STT non consentito')
    local = APP_HOME / 'models/whisper' / name
    if (local / 'model.bin').is_file() and (local / 'config.json').is_file():
        return local
    try:
        from faster_whisper.utils import download_model
        # local_files_only=True: listing and inference MUST NOT download silently.
        resolved = download_model(name, local_files_only=True)
        if (Path(resolved) / 'model.bin').is_file() and (Path(resolved) / 'config.json').is_file():
            return Path(resolved)
    except Exception:
        pass
    return None


def availability(model):
    if model['id'] == 'el:eleven_v4_turbo':
        from . import turbo
        if not turbo.available():
            return 'missing_dependency'
    if model['mode'] == 'cloud':
        return 'ready' if bool(secrets.get_secret('elevenlabs' if model['provider'] == 'ElevenLabs' else 'openai')) else 'configure'
    if model['provider'] == 'Piper':
        try:
            import piper  # noqa: F401
            return 'ready' if piper_path(model['model']) else 'install'
        except ImportError:
            return 'missing_dependency'
    try:
        import faster_whisper  # noqa: F401
        return 'ready' if whisper_path(model['model']) else 'install'
    except ImportError:
        return 'missing_dependency'


def catalog():
    return [{**m, 'status': availability(m)} for m in CATALOG]


def install_model(model_id):
    """Only allowlisted offline model downloads. No arbitrary package install."""
    m = MODELS.get(model_id)
    if not m or m['mode'] != 'local':
        raise ValueError('Questo elemento non è installabile localmente')
    if availability(m) == 'missing_dependency':
        raise RuntimeError('Dipendenza Python assente: avvia il server con la .venv del prototipo Voice')
    if availability(m) == 'ready':
        return {'status': 'ready', 'message': 'Modello già installato'}
    if m['provider'] == 'Piper':
        d = APP_HOME / 'models/piper'
        d.mkdir(parents=True, exist_ok=True)
        run([sys.executable, '-m', 'piper.download_voices', '--download-dir', str(d), m['model']], timeout=900)
    elif m['provider'] == 'faster-whisper':
        from faster_whisper.utils import download_model
        d = APP_HOME / 'models/whisper' / m['model']
        d.mkdir(parents=True, exist_ok=True)
        download_model(m['model'], output_dir=str(d))
    else:
        raise ValueError('Provider non installabile')
    if availability(m) != 'ready':
        raise RuntimeError('Download completato ma modello non verificabile')
    return {'status': 'ready', 'message': 'Modello disponibile per i test'}


def capture(device, seconds, path):
    if device not in endpoints('sources'):
        raise RuntimeError('Microfono non disponibile')
    sec = float(seconds)
    if not 2 <= sec <= 30:
        raise ValueError('Registrazione consentita fra 2 e 30 secondi')
    cmd = ['parecord', f'--device={device}', '--file-format=wav',
           '--format=s16le', '--rate=16000', '--channels=1', str(path)]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if p.poll() is None:
        # Real parecord subprocess is running, not merely requested by a UI.
        visual_feedback.emit('capture_started')
    try:
        try:
            time.sleep(sec)
        finally:
            if p.poll() is None:
                p.send_signal(signal.SIGINT)
            try:
                _o, err = p.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                p.kill()
                _o, err = p.communicate(timeout=3)
                raise RuntimeError('Timeout parecord')
        if not path.is_file() or path.stat().st_size < 4096:
            raise RuntimeError('Audio vuoto: ' + err.decode(errors='replace')[-180:])
        with wave.open(str(path), 'rb') as w:
            if w.getnchannels() != 1 or w.getframerate() != 16000 or w.getsampwidth() != 2:
                raise RuntimeError('Formato microfono non supportato')
            if w.getnframes() < w.getframerate():
                raise RuntimeError('Registrazione troppo breve')
            duration = w.getnframes() / w.getframerate()
            peak, rms = wav_level(w)
        visual_feedback.emit('ready_idle')
        return {'duration': round(duration, 2), 'peak_pct': round(peak, 3), 'rms_pct': round(rms, 3)}
    except Exception:
        visual_feedback.emit('service_error')
        raise

def wav_level(handle):
    handle.rewind()
    samples = array.array('h', handle.readframes(handle.getnframes()))
    if sys.byteorder != 'little':
        samples.byteswap()
    if not samples:
        return 0.0, 0.0
    peak = max(abs(int(x)) for x in samples) / 32768 * 100
    rms = math.sqrt(sum(int(x) * int(x) for x in samples) / len(samples)) / 32768 * 100
    return peak, rms


def audio_duration(path):
    with wave.open(str(path), 'rb') as f:
        return round(f.getnframes() / f.getframerate(), 2)


def playback(path, sink):
    if sink not in endpoints('sinks'):
        raise RuntimeError('Cassa selezionata non disponibile')
    # Popen permits a lifecycle event only once the real paplay process exists.
    # This observes subprocess start, not independent confirmation of audible PCM.
    cmd = ['paplay', f'--device={sink}', str(path)]
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    started = p.poll() is None
    if started:
        visual_feedback.emit('playback_started')
    try:
        try:
            _out, err = p.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate(timeout=5)
            raise
        if p.returncode:
            raise RuntimeError(f'paplay: {(err or "").strip()[-350:] or "exit " + str(p.returncode)}')
    except Exception:
        visual_feedback.emit('service_error')
        raise
    if started:
        visual_feedback.emit('playback_stopped')
    return {'status': 'played', 'sink': sink}


def require_ready(m):
    state = availability(m)
    if state != 'ready':
        raise RuntimeError(f'{m["name"]} non pronto ({state})')


def transcribe(model_id, path):
    m = MODELS.get(model_id)
    if not m or m['type'] != 'stt':
        raise ValueError('Modello STT non valido')
    require_ready(m)
    t0 = time.monotonic()
    visual_feedback.emit('capture_stopped_processing')
    try:
        if m['provider'] == 'faster-whisper':
            from faster_whisper import WhisperModel
            model_path = whisper_path(m['model'])
            key = str(model_path)
            if key not in MODEL_CACHE:
                MODEL_CACHE.clear()  # Bound GPU/CPU RAM; only 1 local model cached.
                MODEL_CACHE[key] = WhisperModel(str(model_path), device='cpu', compute_type='int8', local_files_only=True)
            segs, _ = MODEL_CACHE[key].transcribe(str(path), language='it', beam_size=m['beam'],
                                                   vad_filter=False, condition_on_previous_text=False)
            result = ' '.join(s.text.strip() for s in segs).strip()
        elif m['provider'] == 'ElevenLabs':
            fields = {'model_id': m['model'], 'language_code': 'it', 'tag_audio_events': 'false'}
            raw, ctype = multipart(fields, 'file', path.name, path.read_bytes(), 'audio/wav')
            result = get_json('https://api.elevenlabs.io/v1/speech-to-text',
                              headers={'xi-api-key': secrets.get_secret('elevenlabs'), 'Content-Type': ctype}, data=raw).get('text', '')
        elif m['provider'] == 'OpenAI':
            fields = {'model': m['model'], 'language': 'it', 'response_format': 'json'}
            raw, ctype = multipart(fields, 'file', path.name, path.read_bytes(), 'audio/wav')
            result = get_json('https://api.openai.com/v1/audio/transcriptions',
                              headers={'Authorization': 'Bearer ' + secrets.get_secret('openai'), 'Content-Type': ctype}, data=raw).get('text', '')
        else:
            raise RuntimeError('STT provider non supportato')
        elapsed = round(time.monotonic() - t0, 3)
        # Local ledger contains aggregates, not audio, utterances or transcript text.
        from . import pricing
        try:
            accounting = pricing.record(APP_HOME, m['provider'], model_id, 'stt',
                                        audio_seconds=audio_duration(path), request_seconds=elapsed)
        except (OSError, RuntimeError, ValueError):
            accounting = {'estimated_usd': None, 'rate_status': 'ledger_unavailable'}
        output = {'text': str(result).strip(), 'seconds': elapsed, 'model': model_id,
                'cost_estimate': accounting}
        visual_feedback.emit('ready_idle')
        return output

    except Exception:
        visual_feedback.emit('service_error')
        raise

def synthesize(model_id, text, voice_id, path, controls=None):
    """Synthesize a bounded voice sample, optionally with Voice Studio controls.

    When no controls are provided, preserve the 0.2.1 endpoints and payloads,
    including Eleven v4 Text to Dialogue. Explicit v4 Studio controls use the
    officially documented Text to Speech voice_settings endpoint instead.
    """
    m = MODELS.get(model_id)
    if not m or m['type'] != 'tts':
        raise ValueError('Modello TTS non valido')
    require_ready(m)
    if not isinstance(text, str) or not (1 <= len(text.strip()) <= 650):
        raise ValueError('Testo richiesto: 1–650 caratteri')
    explicit_controls = controls is not None
    controls = voice_controls.normalize(controls)
    voice_controls.validate_for_model(m, controls)
    text = voice_controls.expressive_text(text, controls, m)
    t0 = time.monotonic()
    transport_stats = {}
    if m['provider'] == 'Piper':
        from piper import PiperVoice
        with wave.open(str(path), 'wb') as f:
            if voice_id != m['model']:
                raise ValueError('Voce Piper non coerente con il modello selezionato')
            PiperVoice.load(str(piper_path(m['model']))).synthesize_wav(text, f)
    elif m['provider'] == 'ElevenLabs':
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', voice_id or ''):
            raise ValueError('Seleziona una voce ElevenLabs dal catalogo')
        if m['model'] == 'eleven_v4_turbo':
            from . import turbo
            transport_stats = turbo.synthesize_to_wav(
                text, voice_id, secrets.get_secret('elevenlabs'), path,
                voice_settings=voice_controls.eleven_native_settings(controls, m) if explicit_controls else None)
        elif m['model'] == 'eleven_v4' and not explicit_controls:
            # Backward compatible with 0.2.1, still used by older callers.
            endpoint = 'https://api.elevenlabs.io/v1/text-to-dialogue?output_format=pcm_16000'
            payload = {'inputs': [{'text': text, 'voice_id': voice_id}], 'model_id': 'eleven_v4'}
        elif m['model'] != 'eleven_v4_turbo':
            # Official standard TTS endpoint; Turbo only supports TTD WebSocket.
            endpoint = 'https://api.elevenlabs.io/v1/text-to-speech/' + voice_id + '?output_format=pcm_16000'
            payload = {'text': text, 'model_id': m['model']}
            if explicit_controls:
                payload['voice_settings'] = voice_controls.eleven_native_settings(controls, m)
        if m['model'] != 'eleven_v4_turbo':
            data = get_binary(endpoint,
                          {'xi-api-key': secrets.get_secret('elevenlabs'), 'Content-Type': 'application/json', 'Accept': 'audio/pcm'},
                          json.dumps(payload, ensure_ascii=False).encode('utf-8'))
            with wave.open(str(path), 'wb') as f:
                f.setnchannels(1)
                f.setsampwidth(2)
                f.setframerate(16000)
                f.writeframes(data)
    elif m['provider'] == 'OpenAI':
        if voice_id not in OPENAI_VOICE_IDS:
            raise ValueError('Voce OpenAI non supportata')
        data = get_binary('https://api.openai.com/v1/audio/speech',
                          {'Authorization': 'Bearer ' + secrets.get_secret('openai'), 'Content-Type': 'application/json'},
                          json.dumps({'model': m['model'], 'voice': voice_id, 'input': text, 'response_format': 'wav'}, ensure_ascii=False).encode())
        path.write_bytes(data)
    else:
        raise RuntimeError('TTS provider non supportato')
    if path.stat().st_size < 100:
        raise RuntimeError('Il provider ha restituito audio vuoto')
    original_duration = audio_duration(path)
    effected = voice_controls.apply_local_fx(path, controls)
    elapsed = round(time.monotonic() - t0, 3)
    from . import pricing
    try:
        accounting = pricing.record(APP_HOME, m['provider'], model_id, 'tts', chars=len(text),
                                    audio_seconds=original_duration, request_seconds=elapsed)
    except (OSError, RuntimeError, ValueError):
        accounting = {'estimated_usd': None, 'rate_status': 'ledger_unavailable'}
    return {'seconds': elapsed, 'duration': audio_duration(path),
            'model': model_id, 'postprocess_applied': effected,
            'engine': 'eleven_v4_tts_settings' if m['model'] == 'eleven_v4' and explicit_controls else ('eleven_v4_turbo_websocket' if m['model'] == 'eleven_v4_turbo' else 'default'),
            'cost_estimate': accounting, **transport_stats}


def list_voices(provider, model_id=None):
    if provider == 'Piper':
        m = MODELS.get(model_id)
        if not m or m['provider'] != 'Piper':
            raise ValueError('Seleziona prima una voce Piper')
        return [{'id': m['model'], 'name': m['model'].replace('it_IT-', '').replace('-', ' ').capitalize() + ' · italiano'}]
    if provider == 'OpenAI':
        return [{'id': k, 'name': k.capitalize()} for k in ('marin', 'cedar', 'alloy', 'coral', 'nova', 'sage', 'shimmer', 'echo', 'onyx')]
    if provider == 'ElevenLabs':
        if not secrets.get_secret('elevenlabs'):
            raise RuntimeError('ELEVENLABS_API_KEY non configurata')
        url = 'https://api.elevenlabs.io/v2/voices?page_size=100'
        response = get_json(url, headers={'xi-api-key': secrets.get_secret('elevenlabs')})
        return [{'id': x['voice_id'], 'name': x.get('name', x['voice_id'])} for x in response.get('voices', [])
                if isinstance(x, dict) and x.get('voice_id')]
    raise ValueError('Provider voci non supportato')



# Voice catalogue is generated from a curated model list (safe model IDs only).
# ElevenLabs library/search is remote metadata, fetched only by explicit UI actions.
def voice_library(provider, query='', language='', page=0, after=''):
    if provider not in ('piper', 'elevenlabs', 'elevenlabs_shared', 'openai'):
        raise ValueError('Provider del catalogo non supportato')
    if not isinstance(query, str) or len(query) > 90 or not isinstance(language, str) or len(language) > 20:
        raise ValueError('Filtro non valido')
    if language and not re.fullmatch(r'[a-z]{2}(?:_[A-Z]{2})?', language):
        raise ValueError('Codice lingua non valido')
    if provider == 'piper':
        voice_models = [m for m in CATALOG if m['provider'] == 'Piper']
        voices = []
        for m in voice_models:
            lang = m.get('language', m['model'].split('-')[0])
            if language and not (lang == language or lang.lower().startswith(language.lower())):
                continue
            if query and query.casefold() not in (' '.join([m['name'], m['model'],m.get('note','')])).casefold():
                continue
            voices.append({'id': m['model'], 'name': m['name'].replace('Piper · ', ''),
                           'model_id': m['id'], 'language': lang, 'quality': m.get('quality', ''),
                           'provider': 'Piper', 'status': availability(m), 'mode': 'local', 'note': m['note']})
        return {'voices': voices, 'has_more': False, 'configured': True}
    if provider == 'openai':
        voices = [{'id': x['id'], 'name': x['name'], 'model_id': 'oa:gpt-4o-mini-tts',
                   'language': 'multilingual', 'provider': 'OpenAI', 'status': 'ready' if secrets.get_secret('openai') else 'configure',
                   'mode': 'cloud', 'note': 'Voce API, non richiede download sul mini-PC'}
                  for x in list_voices('OpenAI')]
        if query:
            voices = [v for v in voices if query.casefold() in v['name'].casefold()]
        return {'voices': voices, 'has_more': False, 'configured': bool(secrets.get_secret('openai'))}
    key = secrets.get_secret('elevenlabs')
    if not key:
        return {'voices': [], 'has_more': False, 'configured': False,
                'message': 'Inserisci la chiave ElevenLabs nella sezione Setup'}
    if provider == 'elevenlabs':
        if after and (not isinstance(after, str) or not re.fullmatch(r'[A-Za-z0-9_.=~-]{1,256}', after)):
            raise ValueError('Pagina non valida')
        args = {'page_size': 24}
        if query: args['search'] = query
        if language: args['language'] = language.split('_')[0]
        if after: args['next_page_token'] = after
        url = 'https://api.elevenlabs.io/v2/voices?' + urllib.parse.urlencode(args)
        data = get_json(url, headers={'xi-api-key': key})
        voices = []
        for v in data.get('voices', []):
            if not isinstance(v, dict) or not v.get('voice_id'): continue
            voices.append({'id': str(v['voice_id']), 'name': str(v.get('name') or 'Voice')[:100],
                           'language': str((v.get('labels') or {}).get('language') or '')[:32],
                           'category': str(v.get('category') or '')[:40],
                           'provider': 'ElevenLabs', 'mode': 'cloud', 'status': 'ready',
                           'model_id': 'el:eleven_flash_v2_5', 'note': str(v.get('description') or '')[:180]})
        return {'voices': voices, 'has_more': bool(data.get('has_more')),
                'after': data.get('next_page_token') if data.get('has_more') else None,
                'configured': True}
    page = int(page)
    if not 0 <= page <= 50:
        raise ValueError('Pagina non valida')
    args = {'page_size': 24, 'page': page, 'sort': 'trending'}
    if query: args['search'] = query
    if language: args['language'] = language.split('_')[0]
    url = 'https://api.elevenlabs.io/v1/shared-voices?' + urllib.parse.urlencode(args)
    data = get_json(url, headers={'xi-api-key': key})
    voices = []
    for v in data.get('voices', []):
        if not isinstance(v, dict) or not v.get('voice_id') or not v.get('public_owner_id'): continue
        voices.append({'id': str(v['voice_id']), 'public_owner_id': str(v['public_owner_id']),
                       'name': str(v.get('name') or 'Voice')[:100], 'language': str(v.get('locale') or v.get('language') or '')[:32],
                       'category': str(v.get('category') or 'shared')[:40], 'provider': 'ElevenLabs', 'mode': 'cloud',
                       'status': 'available', 'note': str(v.get('description') or '')[:180]})
    return {'voices': voices, 'has_more': bool(data.get('has_more')), 'page': page, 'configured': True}


def add_shared_voice(owner_id, voice_id, name):
    # Side effect: explicitly adds a third-party voice to the user's ElevenLabs account.
    if not isinstance(owner_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,100}', owner_id):
        raise ValueError('Identificativo proprietario non valido')
    if not isinstance(voice_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', voice_id):
        raise ValueError('Identificativo voce non valido')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 70:
        raise ValueError('Nome voce non valido')
    key = secrets.get_secret('elevenlabs')
    if not key:
        raise RuntimeError('Configura prima la chiave ElevenLabs in Setup')
    raw = json.dumps({'new_name': name.strip(), 'bookmarked': True}, ensure_ascii=False).encode('utf-8')
    endpoint = 'https://api.elevenlabs.io/v1/voices/add/' + owner_id + '/' + voice_id
    result = get_json(endpoint, headers={'xi-api-key': key, 'Content-Type': 'application/json'}, data=raw)
    if not isinstance(result, dict) or not result.get('voice_id'):
        raise RuntimeError('La libreria ElevenLabs non ha confermato l’aggiunta')
    return {'added': True, 'voice_id': result['voice_id'], 'name': name.strip()}


def _probe_readonly_endpoint(url, headers):
    """No credits are spent: read-only metadata permission probe.

    HTTP errors are returned as sanitized scope states. Never expose
    response bodies, keys, subscription info, or request headers.
    """
    req = urllib.request.Request(url, headers=headers, method='GET')
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            if 200 <= response.status < 300:
                return {'status': 'available', 'http_status': response.status}
            return {'status': 'unknown', 'http_status': response.status}
    except urllib.error.HTTPError as error:
        labels = {401: 'unauthorized', 402: 'payment_required',
                  403: 'restricted', 429: 'rate_limited'}
        return {'status': labels.get(error.code, 'provider_error'), 'http_status': error.code}
    except (urllib.error.URLError, TimeoutError, OSError):
        return {'status': 'unreachable', 'http_status': None}


def verify_api_key(provider):
    """Check API scopes without paid synthesis, preserving least privilege.

    A /v1/user 403 means USER_READ is unavailable, NOT that TTS is broken.
    TTS/STT ability is only proven by explicit user-initiated generation.
    """
    if provider not in secrets.PROVIDERS:
        raise ValueError('Provider non supportato')
    token = secrets.get_secret(provider)
    if not token:
        raise ValueError('Nessuna chiave configurata')
    if provider == 'elevenlabs':
        headers = {'xi-api-key': token}
        probes = {
            'user_read': 'https://api.elevenlabs.io/v1/user',
            'models_read': 'https://api.elevenlabs.io/v1/models',
            'voices_read': 'https://api.elevenlabs.io/v2/voices?page_size=1',
        }
    else:
        headers = {'Authorization': 'Bearer ' + token}
        probes = {'models_read': 'https://api.openai.com/v1/models'}
    checks = {scope: _probe_readonly_endpoint(url, headers) for scope, url in probes.items()}
    statuses = [v['status'] for v in checks.values()]
    confirmed = any(status == 'available' for status in statuses)
    limited = any(status == 'restricted' for status in statuses)
    if confirmed and limited:
        state = 'verified_limited'
        message = ('Chiave accettata per alcuni endpoint; altri permessi di lettura sono limitati. '
                   'TTS/STT si verificano con i rispettivi test nel Laboratorio.')
    elif confirmed:
        state = 'verified'
        message = ('Accesso ai metadati verificato. La generazione TTS/STT si verifica '
                   'con i rispettivi test nel Laboratorio.')
    elif all(status == 'unauthorized' for status in statuses):
        state = 'unauthorized'
        message = 'Autenticazione rifiutata (401). Controlla la chiave API.'
    elif limited:
        state = 'limited_unconfirmed'
        message = ('Verifica dei metadati limitata dai permessi della chiave (403). '
                   'Questo NON prova che TTS/STT non funzionino: le chiavi possono avere scope separati.')
    else:
        state = 'inconclusive'
        message = ('Impossibile confermare l’accesso tramite le letture non a pagamento: '
                   'controlla codici HTTP e connessione. Le prove TTS/STT sono indipendenti.')
    return {'verified': confirmed, 'provider': provider, 'state': state,
            'message': message, 'checks': checks, 'no_generation_performed': True}

def get_binary(url, headers, data=None):
    req = urllib.request.Request(url, headers=headers, data=data, method='POST' if data is not None else 'GET')
    try:
        with urllib.request.urlopen(req, timeout=55) as res:
            body = res.read(MAX_AUDIO + 1)
            if len(body) > MAX_AUDIO:
                raise RuntimeError('Output API troppo grande')
            return body
    except urllib.error.HTTPError as err:
        # Never log request headers, keys or the full remote error body.
        raise RuntimeError(f'Provider cloud HTTP {err.code}; verifica crediti, modello e permessi API') from None
    except urllib.error.URLError:
        raise RuntimeError('Provider cloud non raggiungibile') from None


def get_json(url, headers, data=None):
    raw = get_binary(url, headers, data)
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError('Risposta provider non valida') from None


def multipart(fields, file_field, name, content, mime):
    if len(content) > MAX_AUDIO:
        raise ValueError('WAV troppo grande')
    boundary = '----RGDVoice' + uuid.uuid4().hex
    pieces = []
    for k, v in fields.items():
        if not re.fullmatch(r'[a-z_]+', k):
            raise ValueError('Invalid multipart field')
        pieces.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()])
    pieces.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{name}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
    pieces.append(content)
    pieces.append(f'\r\n--{boundary}--\r\n'.encode())
    return b''.join(pieces), 'multipart/form-data; boundary=' + boundary


def wer(reference, hypothesis):
    def toks(s):
        text = unicodedata.normalize('NFKD', s.lower())
        text = ''.join(ch for ch in text if not unicodedata.combining(ch))
        text = re.sub(r'[^\w ]+', ' ', text, flags=re.UNICODE)
        text = re.sub(r'\s+', ' ', text).strip()
        return text.split()
    ref, hyp = toks(reference), toks(hypothesis)
    if not ref:
        return None
    prev = list(range(len(hyp) + 1))
    for i, a in enumerate(ref, 1):
        cur = [i]
        for j, b in enumerate(hyp, 1):
            cur.append(min(cur[-1] + 1, prev[j] + 1, prev[j-1] + int(a != b)))
        prev = cur
    return {'errors': prev[-1], 'words': len(ref), 'wer_pct': round(100 * prev[-1] / len(ref), 1)}
