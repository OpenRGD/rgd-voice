"""VOICE-0.5A — repeatable, local Piper cold-vs-warm synthesis benchmark.

Run: python -m rgd_voice_lab.benchmark_piper --repeat 5
Uses the saved Paky-01 Piper profile by default (or --model).
Does NOT play audio, open microphones, call cloud providers, use BLE,
record any user speech or retain synthesized WAVs.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
from pathlib import Path

from . import core, piper_engine, voice_profile

DEFAULT_TEXT = (
    "Ciao. Sto verificando la velocità della voce locale di Paky zero uno. "
    "La risposta deve essere chiara e naturale."
)


def benchmark(model_id: str, text: str, repeat: int) -> dict:
    model = core.MODELS.get(model_id)
    if not model or model['provider'] != 'Piper' or model['type'] != 'tts':
        raise ValueError("Il benchmark accetta soltanto i modelli Piper locali")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 650:
        raise ValueError("Testo: 1–650 caratteri")
    if type(repeat) is not int or not 2 <= repeat <= 20:
        raise ValueError("Ripetizioni: 2–20 per distinguere primo e successivi")
    core.require_ready(model)  # never downloads a model
    path = core.piper_path(model['model'])
    if path is None:
        raise RuntimeError("Modello Piper non disponibile in locale")

    # IMPORTANT: use a FRESH Python process to measure the first load.
    # A pre-existing model in THIS process is reported as cache_hit=true.
    runs = []
    with tempfile.TemporaryDirectory(prefix='rgd_piper_bench_') as d:
        for n in range(repeat):
            wav = Path(d) / f'output-{n}.wav'
            data = piper_engine.ENGINE.synthesize_wav(path, text, wav)
            duration = core.audio_duration(wav)
            total = data['load_seconds'] + data['synthesis_seconds']
            runs.append({
                'turn': n + 1,
                **data,
                'audio_seconds': duration,
                'total_seconds': round(total, 4),
                'real_time_factor': round(data['synthesis_seconds'] / duration, 3) if duration else None,
            })
    subsequent = runs[1:]
    return {
        'contract': 'org.openrgd.voice.piper-benchmark.v0.5a-candidate',
        'model_id': model_id,
        'text_characters': len(text),
        'repetitions': repeat,
        'resident_enabled': piper_engine.ENGINE.enabled(),
        'first_turn_seconds': runs[0]['total_seconds'],
        'first_load_seconds': runs[0]['load_seconds'],
        'first_cache_hit': runs[0]['cache_hit'],
        'subsequent_median_seconds': round(statistics.median(
            r['total_seconds'] for r in subsequent), 4),
        'subsequent_median_synthesis_seconds': round(statistics.median(
            r['synthesis_seconds'] for r in subsequent), 4),
        'all_subsequent_cache_hits': all(r['cache_hit'] for r in subsequent),
        'runs': runs,
        'notes': [
            'Synthesis only; excludes VAD, STT, ffmpeg audio FX, audio playback and Bluetooth buffering.',
            'The first run is a cold Piper model load ONLY if this process had no cached model.',
            'No time-to-first-audio: the current playback waits for a complete WAV.',
            'All output WAV files are deleted after the benchmark. No cloud requests.',
        ],
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='VOICE-0.5A cold/warm Piper CPU benchmark')
    p.add_argument('--model', default=None, help='e.g. piper:it_IT-serena-high; default: saved Piper voice')
    p.add_argument('--repeat', type=int, default=5)
    p.add_argument('--text', default=DEFAULT_TEXT, help='Synthetic test phrase, no recorded speech')
    p.add_argument('--json', action='store_true', help='Print machine-readable JSON')
    args = p.parse_args(argv)
    model_id = args.model
    if model_id is None:
        profile = voice_profile.read()
        if not profile or not profile.get('model_id', '').startswith('piper:'):
            p.error('Salva una voce Piper locale su Paky-01 o passa --model piper:...')
        model_id = profile['model_id']
    try:
        report = benchmark(model_id, args.text, args.repeat)
    except (RuntimeError, ValueError, OSError) as e:
        p.error(str(e))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f'[PIPER 0.5A] model={model_id} repeat={args.repeat} '
              f'resident={report["resident_enabled"]}', flush=True)
        for entry in report['runs']:
            print(f'[TURN {entry["turn"]}] cache_hit={entry["cache_hit"]} '
                  f'load={entry["load_seconds"]:.3f}s '
                  f'synth={entry["synthesis_seconds"]:.3f}s '
                  f'audio={entry["audio_seconds"]:.2f}s '
                  f'RTF={entry["real_time_factor"]}', flush=True)
        print(f'[SUMMARY] first={report["first_turn_seconds"]:.3f}s '
              f'warm_median={report["subsequent_median_seconds"]:.3f}s '
              f'cached_all={report["all_subsequent_cache_hits"]}', flush=True)
        print('[NOTE] Benchmark CPU-only: nessun playback/registrazione/cloud.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
