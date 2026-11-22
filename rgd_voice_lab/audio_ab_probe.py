"""VOICE-0.5A.1: controlled A/B acoustic probe using IDENTICAL PCM.

Generate one deterministic Piper stream into a temporary RIFF/WAV and a raw
PCM file, then optionally play the SAME samples using paplay and pacat.
No mic, cloud, visual event, or persistent config changes. Explicit --play.
"""
from __future__ import annotations

import argparse
from array import array
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import wave

from . import core, piper_engine, voice_controls, voice_profile

PROBE_TEXT = (
    "Questa è la frase numero uno. Dovresti sentire ogni parola dall'inizio alla fine. "
    "Questa è la frase numero due. Controlliamo che non venga saltata nessuna parola. "
    "Questa è la frase numero tre. Ora verifichiamo anche la fine della riproduzione."
)


def capture_fixture(model_path: Path, text: str, dest: Path) -> dict:
    """Write a single Piper synth run to both file formats, without playback.

    Every PCM callback appends to the WAV and raw files immediately. Both paths
    share byte-for-byte PCM data; there is NO second synthesize operation.
    """
    raw_path = dest / "fixture.pcm"
    wav_path = dest / "fixture.wav"
    chunks = []
    rate = None
    wav_file = None
    with raw_path.open("wb") as raw:
        try:
            def accept(pcm, sample_rate, channels):
                nonlocal rate, wav_file
                if rate is None:
                    rate = sample_rate
                    wav_file = wave.open(str(wav_path), "wb")
                    wav_file.setnchannels(channels)
                    wav_file.setsampwidth(2)
                    wav_file.setframerate(sample_rate)
                if sample_rate != rate or channels != 1:
                    raise ValueError("PCM format changed during fixture capture")
                # Ensure queue/raw player cannot mutate the test sample.
                block = bytes(pcm)
                raw.write(block)
                wav_file.writeframesraw(block)
                values = array("h")
                values.frombytes(block)
                peak = max((abs(n) for n in values), default=0)
                chunks.append({
                    "index": len(chunks) + 1,
                    "bytes": len(block),
                    "seconds": round(len(block) / (sample_rate * 2), 3),
                    "peak_percent": round(100 * peak / 32768, 1),
                    "sha256_prefix": hashlib.sha256(block).hexdigest()[:12],
                })

            stats = piper_engine.ENGINE.synthesize_pcm(model_path, text, accept)
        finally:
            if wav_file is not None:
                wav_file.close()

    if not chunks or rate is None:
        raise ValueError("Piper did not yield PCM audio")
    total_bytes = raw_path.stat().st_size
    with wave.open(str(wav_path), "rb") as reader:
        if reader.getframerate() != rate or reader.getnframes() * 2 != total_bytes:
            raise ValueError("Raw PCM and RIFF/WAV fixture disagree")
    if total_bytes != stats["pcm_bytes"] or len(chunks) != stats["chunk_count"]:
        raise ValueError("Generated PCM does not match Piper's chunk counters")
    return {
        "sample_rate": rate,
        "total_bytes": total_bytes,
        "duration_seconds": round(total_bytes / (2 * rate), 3),
        "raw_sha256": hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        "chunks": chunks,
        "piper": stats,
        "wav_path": wav_path,
        "raw_path": raw_path,
    }


def _run_player(label: str, cmd: list[str], *, raw_path: Path | None = None) -> None:
    print(f"\n[{label}] Avvio riproduzione", flush=True)
    started = time.monotonic()
    if raw_path is None:
        result = subprocess.run(cmd, capture_output=True, timeout=90, check=False)
    else:
        with raw_path.open("rb") as fd:
            result = subprocess.run(cmd, stdin=fd, capture_output=True, timeout=90, check=False)
    elapsed = time.monotonic() - started
    print(f"[{label}] exit={result.returncode} elapsed={elapsed:.3f}s", flush=True)
    if result.returncode:
        err = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"{label} failed: {err[-400:] or result.returncode}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="A/B identical Piper PCM: paplay WAV versus pacat RAW")
    p.add_argument("--play", action="store_true",
                   help="Actually play A and B to selected Bose A2DP sink; otherwise generate only")
    p.add_argument("--sink", default=core.BIND_SINK_MATCH,
                   help="Explicit enumerated PulseAudio sink; defaults to Paky-01 Bose A2DP")
    a = p.parse_args(argv)
    profile = voice_profile.read()
    if not profile or profile.get("model_id") != "piper:it_IT-serena-high":
        p.error("Questa prova richiede il profilo locale Piper Serena High")
    opts = voice_controls.normalize(profile.get("controls"))
    if voice_controls.needs_local_fx(opts):
        p.error("Il test raw richiede speed=1 e pitch=0 per usare identici campioni PCM")

    if a.play:
        names = core.endpoints("sinks")
        if a.sink not in names:
            p.error(f"Uscita non disponibile: {a.sink}")
        if not shutil.which("paplay") or not shutil.which("pacat"):
            p.error("Servono sia paplay che pacat per il confronto A/B")

    model_path = core.piper_path("it_IT-serena-high")
    if not model_path:
        p.error("Modello Piper Serena High mancante")
    prewarm = piper_engine.ENGINE.prepare(model_path)
    print(f"[MODEL] Serena High prewarm={prewarm['load_seconds']:.3f}s", flush=True)

    with tempfile.TemporaryDirectory(prefix="rgd_voice_ab_") as d:
        fixture = capture_fixture(model_path, PROBE_TEXT, Path(d))
        print(f"[FIXTURE] PCM bytes={fixture['total_bytes']} "
              f"duration={fixture['duration_seconds']:.2f}s "
              f"rate={fixture['sample_rate']} "
              f"chunks={len(fixture['chunks'])}", flush=True)
        print(f"[FIXTURE] SHA256={fixture['raw_sha256'][:20]}", flush=True)
        for info in fixture["chunks"]:
            print(f"[CHUNK {info['index']}] length={info['seconds']}s "
                  f"peak={info['peak_percent']}% SHA={info['sha256_prefix']}", flush=True)

        if not a.play:
            print("[DRY] Nessun player avviato. WAV/PCM temporanei eliminati.", flush=True)
            return 0

        print("\nLe prove A e B riprodurranno LA STESSA generazione Piper.", flush=True)
        print("Annota quali frasi senti: UNO, DUE, TRE, e se manca l'inizio o la fine.", flush=True)
        input("\nPremi INVIO per PROVA A: WAV completo via paplay... ")
        _run_player("A WAV/paplay", [
            "paplay", f"--device={a.sink}", str(fixture["wav_path"]),
        ])
        input("\nPremi INVIO per PROVA B: stessi PCM grezzi via pacat... ")
        _run_player("B RAW/pacat", [
            "pacat", "--playback", "--raw", "--format=s16le",
            f"--rate={fixture['sample_rate']}", "--channels=1",
            f"--device={a.sink}", "--client-name=OpenRGD-Voice-AB-Probe",
        ], raw_path=fixture["raw_path"])
        print("\n[FINITO] Dimmi quali frasi erano udibili in A e in B.", flush=True)
        print("[CLEANUP] I file temporanei WAV/PCM sono stati eliminati.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
