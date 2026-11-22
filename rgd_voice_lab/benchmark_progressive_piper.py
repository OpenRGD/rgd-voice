"""VOICE-0.5A.1 — CPU-only first-PCM vs complete-WAV benchmark.

No pacat, no speakers, no mic, no cloud, no Visual BLE and no persistent WAVs.
Models stay resident within the selected Python process. The actual first
speaker sound on Bose A2DP is NOT measured here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import tempfile
import time

from . import core, piper_engine, voice_profile

TEXTS = {
    "single": "Ciao, sto misurando una singola frase completa di Paky zero uno.",
    "multi": (
        "Ciao, sono Paky zero uno. Questa è una seconda frase di prova "
        "per misurare il tempo al primo blocco. E questa è la terza frase."
    ),
}


def benchmark(model_id: str, scenario: str, repeat: int = 3) -> dict:
    m = core.MODELS.get(model_id)
    if not m or m["provider"] != "Piper" or m["type"] != "tts":
        raise ValueError("Benchmark disponibile solo per Piper locale")
    if scenario not in TEXTS:
        raise ValueError("Scenario non valido")
    if type(repeat) is not int or not 2 <= repeat <= 10:
        raise ValueError("Numero ripetizioni: 2–10")
    core.require_ready(m)
    path = core.piper_path(m["model"])
    if path is None:
        raise FileNotFoundError("Voce Piper non disponibile")
    if not piper_engine.ENGINE.enabled():
        raise ValueError("Piper residente deve essere abilitato per questo benchmark")
    warm = piper_engine.ENGINE.prepare(path)
    text = TEXTS[scenario]
    records = []
    with tempfile.TemporaryDirectory(prefix="rgd_progressive_benchmark_") as folder:
        for run in range(repeat):
            pcm_bytes = 0
            def discard(chunk, rate, channels):
                nonlocal pcm_bytes
                pcm_bytes += len(chunk)
            stream = piper_engine.ENGINE.synthesize_pcm(path, text, discard)
            started = time.monotonic()
            wav = Path(folder) / f"full-{run}.wav"
            full = piper_engine.ENGINE.synthesize_wav(path, text, wav)
            full_wall = max(0.0, time.monotonic() - started)
            records.append({
                "turn": run + 1,
                "stream_first_pcm_seconds": stream["first_pcm_seconds"],
                "stream_complete_seconds": stream["producer_wall_seconds"],
                "stream_chunk_count": stream["chunk_count"],
                "pcm_bytes": pcm_bytes,
                "full_wav_ready_seconds": round(full_wall, 4),
                "full_wav_piper_inference_seconds": full["synthesis_seconds"],
                "full_wav_duration_seconds": core.audio_duration(wav),
            })
    return {
        "contract": "org.openrgd.voice.first-pcm-benchmark.v0.5a1-candidate",
        "model_id": model_id,
        "scenario": scenario,
        "text_characters": len(text),
        "repetitions": repeat,
        "prewarm_seconds": warm["load_seconds"],
        "medians": {
            "stream_first_pcm_seconds": round(statistics.median(
                r["stream_first_pcm_seconds"] for r in records), 4),
            "stream_complete_seconds": round(statistics.median(
                r["stream_complete_seconds"] for r in records), 4),
            "full_wav_ready_seconds": round(statistics.median(
                r["full_wav_ready_seconds"] for r in records), 4),
        },
        "runs": records,
        "notes": [
            "Model loaded once; audio chunks discarded, no PulseAudio playback.",
            "Piper typically streams one AudioChunk per sentence.",
            "No claim of measured acoustic sound onset or Bluetooth A2DP latency.",
            "CPU scheduling and thermals may affect results; run again under similar host load.",
        ],
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="VOICE-0.5A.1 no-device Piper latency benchmark")
    p.add_argument("--model", help="Model ID, default: saved Paky-01 Piper profile")
    p.add_argument("--scenario", choices=("single", "multi", "both"), default="multi")
    p.add_argument("--repeat", type=int, default=3)
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    model_id = a.model
    if not model_id:
        profile = voice_profile.read()
        if not profile or not profile.get("model_id", "").startswith("piper:"):
            p.error("Salva un profilo Piper oppure passa --model piper:...")
        model_id = profile["model_id"]
    modes = ("single", "multi") if a.scenario == "both" else (a.scenario,)
    try:
        results = [benchmark(model_id, mode, a.repeat) for mode in modes]
    except (ValueError, RuntimeError, OSError) as exc:
        p.error(str(exc))
    if a.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        for report in results:
            print(f'[STREAM BENCH] model={model_id} scenario={report["scenario"]} '
                  f'repeat={report["repetitions"]}', flush=True)
            for line in report["runs"]:
                print(f'[TURN {line["turn"]}] first_pcm={line["stream_first_pcm_seconds"]:.3f}s '
                      f'pcm_complete={line["stream_complete_seconds"]:.3f}s '
                      f'full_wav={line["full_wav_ready_seconds"]:.3f}s '
                      f'chunks={line["stream_chunk_count"]}', flush=True)
            m = report["medians"]
            print(f'[SUMMARY] first_pcm={m["stream_first_pcm_seconds"]:.3f}s '
                  f'pcm_complete={m["stream_complete_seconds"]:.3f}s '
                  f'full_wav={m["full_wav_ready_seconds"]:.3f}s', flush=True)
        print('[NOTE] Tutti i blocchi PCM scartati: nessuna riproduzione o rete.', flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
