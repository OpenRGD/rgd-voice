"""Provider-agnostic TTS presentation controls; never RGD self/identity or cognition.

Native Eleven v4 settings: stability, similarity_boost (on Text to Speech).
Speed/pitch are POST-processing WAV effects via ffmpeg, explicitly not native v4 knobs.
"""
from __future__ import annotations

import math
import re
import shutil
import subprocess
import wave
from pathlib import Path

DEFAULT_CONTROLS = {
    'stability': 0.5,
    'similarity_boost': 0.75,
    'style': 0.0,
    'speaker_boost': True,
    'emotion': 'neutral',
    'speed': 1.0,
    'pitch_semitones': 0.0,
}
EMOTION_TAGS = {
    'neutral': '',
    'curious': '[curious]',
    'excited': '[excited]',
    'gentle': '[softly]',
    'whisper': '[whispers]',
    'concerned': '[concerned]',
    'playful': '[mischievously]',
}
ALLOWED = frozenset(DEFAULT_CONTROLS)


def normalize(raw=None):
    """Strict validation on server, even if the browser disables unsupported controls."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict) or set(raw) - ALLOWED:
        raise ValueError('Parametri vocali non riconosciuti')
    controls = {**DEFAULT_CONTROLS}
    controls.update(raw)
    for key, low, high in [
        ('stability', 0, 1), ('similarity_boost', 0, 1),
        ('style', 0, 1), ('speed', 0.8, 1.2), ('pitch_semitones', -3, 3),
    ]:
        value = controls[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f'{key}: numero non valido')
        if not low <= value <= high:
            raise ValueError(f'{key}: valore fuori intervallo ({low}–{high})')
        controls[key] = float(value)
    if type(controls['speaker_boost']) is not bool:
        raise ValueError('speaker_boost: richiesto booleano')
    if controls['emotion'] not in EMOTION_TAGS:
        raise ValueError('Emozione non supportata')
    return controls


def validate_for_model(model, controls):
    p = model['provider']
    is_v4 = p == 'ElevenLabs' and model['model'] in ('eleven_v4', 'eleven_v4_turbo')
    if controls['emotion'] != 'neutral' and not is_v4:
        raise ValueError('I tag emotivi di questo laboratorio richiedono Eleven v4')
    if controls['style'] != 0.0 and (p != 'ElevenLabs' or is_v4):
        raise ValueError('Style disponibile solo su ElevenLabs Flash/Multilingual')
    if (needs_local_fx(controls)) and not shutil.which('ffmpeg'):
        raise RuntimeError('Velocità e pitch richiedono ffmpeg; ripristina 1.0x / 0 semitoni oppure installalo')


def needs_local_fx(controls):
    return abs(controls['speed'] - 1.0) > 1e-6 or abs(controls['pitch_semitones']) > 1e-6


def expressive_text(text, controls, model):
    if model['provider'] != 'ElevenLabs' or model['model'] not in ('eleven_v4', 'eleven_v4_turbo'):
        return text
    tag = EMOTION_TAGS[controls['emotion']]
    return (tag + ' ' + text) if tag else text


def eleven_native_settings(controls, model):
    if model['provider'] != 'ElevenLabs':
        return None
    settings = {
        'stability': controls['stability'],
        'similarity_boost': controls['similarity_boost'],
    }
    if model['model'] not in ('eleven_v4', 'eleven_v4_turbo'):
        settings.update({'style': controls['style'], 'use_speaker_boost': controls['speaker_boost']})
    return settings


def apply_local_fx(path: Path, controls):
    """Explicit local postprocess: asetrate sets pitch, atempo restores desired speech rate.

    Pitch +/-3 semitones and tempo 0.8-1.2x only; artifacts possible. Safe fixed argv,
    no shell, no uploads, WAV PCM 16-bit mono output. Source replaced only on success.
    """
    if not needs_local_fx(controls):
        return False
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg non disponibile per il post-processing audio')
    with wave.open(str(path), 'rb') as w:
        rate = w.getframerate()
        if w.getnchannels() != 1 or w.getsampwidth() != 2 or rate < 8000 or rate > 48000:
            raise ValueError('Post-processing richiede WAV PCM mono 16 bit, 8–48 kHz')
    semitones = controls['pitch_semitones']
    ratio = 2 ** (semitones / 12)
    # asetrate changes speed as well as pitch. atempo compensates speed only.
    filters = f'asetrate={rate}*{ratio:.9f},aresample={rate},atempo={controls["speed"] / ratio:.9f}'
    out = path.with_name(path.stem + '.fx-tmp.wav')
    try:
        p = subprocess.run(
            [ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(path),
             '-af', filters, '-ac', '1', '-ar', str(rate), '-c:a', 'pcm_s16le', str(out)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if p.returncode or not out.is_file() or out.stat().st_size < 100:
            raise RuntimeError('ffmpeg: elaborazione non riuscita (controlla il formato WAV)')
        out.replace(path)
    finally:
        out.unlink(missing_ok=True)
    return True
