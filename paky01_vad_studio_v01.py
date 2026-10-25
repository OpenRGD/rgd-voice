#!/usr/bin/env python3
"""Optional I/O-only bridge: proven v0.1D VAD input -> Voice Studio default -> Bose.

No cognitive model or identity prompt, no memory access and no actuator privileges.
Requires the existing, already-commissioned ~/openrgd/rgd-voice-paky01-smoke-v0.1.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

from rgd_voice_lab import core, voice_profile

SMOKE = Path.home() / 'openrgd/rgd-voice-paky01-smoke-v0.1'
if not (SMOKE / 'paky01_voice_vad_v02.py').is_file():
    raise SystemExit(f'Installa prima la versione VAD già collaudata in {SMOKE}')
sys.path.insert(0, str(SMOKE))
from paky01_voice_vad_v02 import capture_phrase, resolve_devices, load_whisper, transcribe, TurnProgress  # noqa: E402


def speak(profile, text, sink, wav, *, allow_cloud=False):
    model_id = profile['model_id']
    model = core.MODELS[model_id]
    if model['mode'] == 'cloud' and not allow_cloud:
        raise RuntimeError('Modello cloud selezionato. Passa --allow-cloud per autorizzare invio del testo al provider.')
    start=time.monotonic()
    synth = core.synthesize(model_id, text, profile['voice_id'], wav, controls=profile['controls'])
    before_play=time.monotonic()
    core.playback(wav, sink)
    return synth['seconds'], time.monotonic() - before_play, time.monotonic() - start


def main():
    p=argparse.ArgumentParser(description='Paky-01 I/O VAD v0.1D + default Voice Studio v0.3.0 (ECHO only)')
    p.add_argument('--turns',type=int,default=3)
    p.add_argument('--allow-cloud',action='store_true',help='Autorizza chiamate TTS al provider cloud selezionato')
    p.add_argument('--debug-vad',action='store_true')
    p.add_argument('--whisper',choices=('base','small'),default='base')
    p.add_argument('--check',action='store_true')
    a=p.parse_args()
    if not 1 <= a.turns <= 20: p.error('--turns deve essere 1–20')
    profile=voice_profile.read()
    if profile is None: p.error('Nessuna voce predefinita. Salvala da Voice Lab → Laboratorio.')
    model=core.MODELS[profile['model_id']]
    if model['mode']=='cloud' and not a.allow_cloud and not a.check:
        p.error('Voce cloud: passa --allow-cloud per autorizzare invio del testo al provider')
    mic,bose=resolve_devices(activate_a2dp=not a.check)
    print(f'[AUDIO] INPUT={mic}\n[AUDIO] OUTPUT={bose}\n[VOICE DEFAULT] {model["id"]} · {profile["voice_id"]}',flush=True)
    if a.check:return 0
    import webrtcvad  # noqa: F401
    stt=load_whisper(a.whisper)
    progress=TurnProgress(a.turns,5)
    with tempfile.TemporaryDirectory(prefix='paky01_studio_io_') as d:
        folder=Path(d)
        speak(profile,'Ti ascolto.',bose,folder/'reply.wav',allow_cloud=a.allow_cloud)
        time.sleep(0.5)
        while not progress.done:
            print(f'\n[TURN {progress.completed+1}/{a.turns}] Parla quando vuoi.',flush=True)
            result=capture_phrase(mic,folder/'input.wav',
                vad_mode=2,input_gain=6.0,rms_gate_pct=0.4,
                listen_timeout=15,max_phrase=18,speech_gap_ms=900,min_speech_ms=240,
                start_window_frames=12,start_hits=8,debug_vad=a.debug_vad)
            if not result:
                if not progress.failed():break
                continue
            recognized,stt_s=transcribe(stt,folder/'input.wav',vad=False)
            if not recognized.strip():
                if not progress.failed():break
                continue
            response='Ho sentito: '+recognized
            print(f'[STT] {recognized}',flush=True)
            synth,played,_=speak(profile,response,bose,folder/'reply.wav',allow_cloud=a.allow_cloud)
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
