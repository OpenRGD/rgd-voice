"""Eleven v4 Turbo Text-to-Dialogue WebSocket preview.

V0.3.1 collects full MP3 on backend and converts to WAV for existing audio player.
Does NOT yet progressively stream playback on Bose; first-chunk latency reported.
"""
from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
import time
from pathlib import Path

ENDPOINT = 'wss://api.elevenlabs.io/v1/text-to-dialogue/stream-input?model_id=eleven_v4_turbo&output_format=mp3_44100_128'
MAX_BYTES = 6 * 1024 * 1024


def available():
    if not shutil.which('ffmpeg'):
        return False
    try:
        import websockets  # noqa: F401
        return True
    except ImportError:
        return False


async def collect_mp3(text, voice_id, api_key, voice_settings=None, *, connection_factory=None):
    if connection_factory is None:
        try:
            import websockets
        except ImportError as exc:
            raise RuntimeError("Adapter Turbo: installa 'websockets' nell'ambiente virtuale Voice Lab") from exc
        connection_factory = websockets.connect
    chunks=[]
    nbytes=0
    t0=time.monotonic()
    first=None
    # WS TTD authenticates in first frame: no token in URI, no browser access.
    async with connection_factory(ENDPOINT, open_timeout=12, close_timeout=4, max_size=MAX_BYTES) as ws:
        hello={'voices':[voice_id], 'xi_api_key':api_key}
        if voice_settings:
            hello['voice_settings']=voice_settings
        await ws.send(json.dumps(hello))
        await ws.send(json.dumps({'inputs':[{'text':text,'voice_id':voice_id,'new_turn':False}]}))
        await ws.send(json.dumps({'close_socket':True}))
        while True:
            try:
                raw=await asyncio.wait_for(ws.recv(),timeout=45)
            except asyncio.TimeoutError as exc:
                raise RuntimeError('ElevenLabs Turbo: timeout ricezione audio') from exc
            msg=json.loads(raw)
            if msg.get('error'):
                # Avoid returning untrusted arbitrary provider payloads to UI/logs.
                raise RuntimeError('ElevenLabs Turbo: servizio ha rifiutato la richiesta (controlla modello, crediti e permessi)')
            payload=msg.get('audio')
            if payload:
                try:
                    buf=base64.b64decode(payload,validate=True)
                except ValueError as exc:
                    raise RuntimeError('ElevenLabs Turbo: chunk base64 invalido') from exc
                nbytes+=len(buf)
                if nbytes>MAX_BYTES:
                    raise RuntimeError('ElevenLabs Turbo: audio oltre il limite di 6MB')
                if first is None:
                    first=time.monotonic()-t0
                chunks.append(buf)
            if msg.get('is_final'):
                break
    if nbytes<100:
        raise RuntimeError('ElevenLabs Turbo: audio restituito vuoto')
    return b''.join(chunks), round(first,3) if first is not None else None


def synthesize_to_wav(text: str, voice_id: str, api_key: str, path: Path, voice_settings=None):
    """WebSocket v4 turbo -> MP3 chunks -> FFmpeg PCM16 mono WAV 16k."""
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('Eleven v4 Turbo richiede ffmpeg per la conversione WAV')
    audio,ttfb=asyncio.run(collect_mp3(text,voice_id,api_key,voice_settings))
    mp3=path.with_suffix('.partial.mp3')
    temp_wav=path.with_suffix('.partial.wav')
    try:
        mp3.write_bytes(audio)
        p=subprocess.run([ffmpeg,'-nostdin','-hide_banner','-loglevel','error','-y',
                        '-i',str(mp3),'-ar','16000','-ac','1','-c:a','pcm_s16le',str(temp_wav)],
                        capture_output=True,text=True,timeout=45,check=False)
        if p.returncode or not temp_wav.is_file() or temp_wav.stat().st_size<100:
            raise RuntimeError('ElevenLabs Turbo: conversione MP3 → WAV non riuscita')
        temp_wav.replace(path)
    finally:
        mp3.unlink(missing_ok=True)
        temp_wav.unlink(missing_ok=True)
    return {'time_to_first_audio_seconds':ttfb,
            'audio_streaming_transport':'eleven_v4_turbo_ttd_websocket',
            'playback_mode':'complete_wav_preview'}
