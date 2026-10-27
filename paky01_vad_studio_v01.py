#!/usr/bin/env python3
"""Optional I/O-only bridge: proven v0.1D VAD input -> Voice Studio default -> Bose.

No cognitive model or identity prompt, no memory access and no actuator privileges.
Requires the existing, already-commissioned ~/openrgd/rgd-voice-paky01-smoke-v0.1.
"""
from __future__ import annotations

import argparse
import importlib
import sys
import tempfile
import time
from pathlib import Path

from rgd_voice_lab import core, voice_profile, visual_feedback, vad_lifecycle, piper_engine, progressive_audio

SMOKE = Path.home() / 'openrgd/rgd-voice-paky01-smoke-v0.1'
def load_legacy_vad():
    """Lazy import avoids injecting hardware dependencies into offline CI."""
    if not (SMOKE / 'paky01_voice_vad_v02.py').is_file():
        raise RuntimeError(f'Manca il VAD locale già collaudato: {SMOKE}')
    if str(SMOKE) not in sys.path:
        sys.path.insert(0, str(SMOKE))
    return importlib.import_module('paky01_voice_vad_v02')


def speak(profile, text, sink, wav, *, allow_cloud=False, progressive=False):
    model_id = profile['model_id']
    model = core.MODELS[model_id]
    if model['mode'] == 'cloud' and not allow_cloud:
        raise RuntimeError('Modello cloud selezionato. Passa --allow-cloud per autorizzare invio del testo al provider.')
    if progressive:
        # Experimental local Piper-only path; any pre-audio failure falls
        # back to the proven full-WAV path (including Visual lifecycle).
        result = progressive_audio.speak(
            profile, text, sink,
            fallback=lambda: speak(profile, text, sink, wav, allow_cloud=allow_cloud),
        )
        if result['mode'] == 'progressive_pcm':
            print(f'[STREAM] first_pcm={result["first_pcm_seconds"]:.3f}s '
                  f'first_write={result["first_write_seconds"]:.3f}s '
                  f'producer_done={result["synthesis_done_seconds"]:.3f}s '
                  f'player_drained={result["playback_drain_seconds"]:.3f}s '
                  f'chunks={result["chunk_count"]} '
                  f'audio={result["audio_seconds"]:.2f}s', flush=True)
        else:
            print(f'[STREAM] fallback=full_wav reason={result["fallback_reason"]}', flush=True)
        return result['synthesis_seconds'], result['playback_seconds'], result['total_seconds']
    start=time.monotonic()
    try:
        synth = core.synthesize(model_id, text, profile['voice_id'], wav, controls=profile['controls'])
    except Exception:
        visual_feedback.emit('service_error')
        raise
    before_play=time.monotonic()
    core.playback(wav, sink)
    return synth['seconds'], time.monotonic() - before_play, time.monotonic() - start


def main():
    p=argparse.ArgumentParser(description='Paky-01 VAD v0.1D + Voice Studio (ECHO only; no Cognition)')
    p.add_argument('--turns',type=int,default=3)
    p.add_argument('--allow-cloud',action='store_true',help='Autorizza chiamate TTS al provider cloud selezionato')
    p.add_argument('--debug-vad',action='store_true')
    p.add_argument('--whisper',choices=('base','small'),default='base')
    p.add_argument('--check',action='store_true')
    p.add_argument('--progressive', action='store_true', help='Pilota Piper PCM via pacat, fallback WAV prima del primo PCM; default OFF')
    a=p.parse_args()
    if not 1 <= a.turns <= 20: p.error('--turns deve essere 1–20')
    if a.check:
        # Hardware discovery must work without a saved voice and must not
        # start a capture session or change the Bluetooth audio profile.
        try:
            profile=voice_profile.read()
        except (OSError, ValueError) as exc:
            print(f'[VOICE DEFAULT] Profilo non valido/non leggibile ({type(exc).__name__})', flush=True)
            profile=None
    else:
        # Fail BEFORE any audio-profile switching when the TTS preset is absent.
        profile=voice_profile.read()
        if profile is None:
            p.error(f'Nessuna voce predefinita. Salvala da Voice Lab → Laboratorio → Imposta su Paky-01. Percorso: {voice_profile.path()}')
        model=core.MODELS[profile['model_id']]
        if a.progressive and model['provider']!='Piper':
            p.error('--progressive è consentito solo con voce locale Piper')
        if model['mode']=='cloud' and not a.allow_cloud:
            p.error('Voce cloud: passa --allow-cloud per autorizzare invio del testo al provider')
    legacy=load_legacy_vad()
    mic,bose=legacy.resolve_devices(activate_a2dp=not a.check)
    print(f'[AUDIO] INPUT={mic}\n[AUDIO] OUTPUT={bose}', flush=True)
    if a.check:
        if profile is None:
            print(f'[VOICE DEFAULT] Non impostata (facoltativa per --check): {voice_profile.path()}', flush=True)
        else:
            print(f'[VOICE DEFAULT] {profile["model_id"]} · {profile["voice_id"]}', flush=True)
        return 0
    print(f'[VOICE DEFAULT] {model["id"]} · {profile["voice_id"]}', flush=True)
    import webrtcvad  # noqa: F401
    stt=legacy.load_whisper(a.whisper)
    if model['provider'] == 'Piper' and piper_engine.ENGINE.enabled():
        # Explicit one-time warmup before the first spoken greeting. This
        # does not capture audio, change devices, or send text to cloud.
        prepared=piper_engine.ENGINE.prepare(core.piper_path(model['model']))
        print(f'[PIPER] residente: {model["model"]} · load={prepared["load_seconds"]:.3f}s · cache_hit={prepared["cache_hit"]}',flush=True)
    progress=legacy.TurnProgress(a.turns,5)
    with tempfile.TemporaryDirectory(prefix='paky01_studio_io_') as d:
        folder=Path(d)
        speak(profile,'Ti ascolto.',bose,folder/'reply.wav',allow_cloud=a.allow_cloud,progressive=a.progressive)
        time.sleep(0.5)
        while not progress.done:
            print(f'\n[TURN {progress.completed+1}/{a.turns}] Parla quando vuoi.',flush=True)
            result=vad_lifecycle.capture_phrase_observed(legacy, mic, folder/'input.wav',
                vad_mode=2,input_gain=6.0,rms_gate_pct=0.4,
                listen_timeout=15,max_phrase=18,speech_gap_ms=900,min_speech_ms=240,
                start_window_frames=12,start_hits=8,debug_vad=a.debug_vad)
            if not result:
                if not progress.failed():break
                continue
            recognized,stt_s=vad_lifecycle.transcribe_observed(legacy, stt, folder/'input.wav')
            if not recognized.strip():
                if not progress.failed():break
                continue
            response='Ho sentito: '+recognized
            print(f'[STT] {recognized}',flush=True)
            synth,played,_=speak(profile,response,bose,folder/'reply.wav',allow_cloud=a.allow_cloud,progressive=a.progressive)
            print(f'[METRICS] stt={stt_s:.2f}s synth={synth:.2f}s playback={played:.2f}s',flush=True)
            progress.accepted()
            time.sleep(0.5)
    print(f'[DONE] {progress.completed}/{a.turns} turni; nessun WAV mantenuto.',flush=True)
    return 0

if __name__=='__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('[STOP] Terminato dall’operatore', file=sys.stderr)
        raise SystemExit(130)
    except Exception as e:
        print(f'[ERROR] {e}',file=sys.stderr)
        raise SystemExit(1)
