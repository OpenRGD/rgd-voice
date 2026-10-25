"""Operator-selected output profile for Paky-01. Not RGD self, life or relationship kernel."""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import core, voice_controls

_LOCK = threading.Lock()
PROFILE_ID = 'paky-01'


def path():
    return core.APP_HOME / 'profiles' / f'{PROFILE_ID}-voice-output.json'


def validate(model_id, voice_id, controls):
    model = core.MODELS.get(model_id)
    if not model or model['type'] != 'tts':
        raise ValueError('Modello TTS non valido')
    if not isinstance(voice_id, str):
        raise ValueError('Voice ID non valido')
    if model['provider'] == 'Piper':
        if voice_id != model['model']:
            raise ValueError('Voce Piper non coerente con il modello')
    elif model['provider'] == 'OpenAI':
        if voice_id not in core.OPENAI_VOICE_IDS:
            raise ValueError('Voce OpenAI non consentita')
    elif model['provider'] == 'ElevenLabs':
        import re
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', voice_id):
            raise ValueError('ID voce ElevenLabs non valido')
    else:
        raise ValueError('Provider TTS non supportato')
    opts = voice_controls.normalize(controls)
    voice_controls.validate_for_model(model, opts)
    return model, opts


def read():
    target = path()
    if not target.is_file():
        return None
    data = json.loads(target.read_text(encoding='utf-8'))
    if data.get('schema') != 'org.openrgd.voice.output-profile.v0.1' or data.get('robot_instance_id') != PROFILE_ID:
        raise ValueError('Profilo voce sconosciuto o appartenente a un altro robot')
    # An obsolete model or invalid option must not be accepted silently.
    validate(data.get('model_id'), data.get('voice_id'), data.get('controls'))
    return data


def save(data):
    if not isinstance(data, dict):
        raise ValueError('Il profilo deve essere JSON')
    model, opts = validate(data.get('model_id'), data.get('voice_id'), data.get('controls'))
    if core.availability(model) != 'ready':
        raise ValueError('Modello non pronto: installa o configura il provider prima di salvarlo')
    if data.get('robot_instance_id') not in (None, PROFILE_ID):
        raise ValueError('Questo laboratorio supporta esclusivamente Paky-01')
    record = {
        'schema': 'org.openrgd.voice.output-profile.v0.1',
        'robot_instance_id': PROFILE_ID,
        'scope': 'audio_output_only',
        'configured_by': 'local_operator',
        'model_id': model['id'],
        'provider': model['provider'],
        'voice_id': data['voice_id'],
        'controls': opts,
        'updated_at_utc': datetime.now(timezone.utc).isoformat(),
    }
    with _LOCK:
        folder = path().parent
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(folder, 0o700)
        tmp = folder / (path().name + '.tmp')
        fd = os.open(str(tmp), os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        tmp.replace(path())
    return record
