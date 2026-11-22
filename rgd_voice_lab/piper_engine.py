"""VOICE-0.5A — bounded, process-local Piper model residency.

Exactly one PiperVoice/ONNX model is retained per Voice Studio Python process.
Audio/BT devices, PERIPH acquisition, cognition, and model downloads are not
owned here. Caller owns authorization, text validation and WAV destination.
"""
from __future__ import annotations

import os
from pathlib import Path
import threading
import time
import wave


class PiperStreamUnavailable(RuntimeError):
    """Old Piper API or punctuation-only text produced no streaming PCM."""


class PiperEngine:
    """Serialize ONNX load + inference and keep at most one voice in RAM."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._voice = None
        self._signature = None
        self._loads = 0
        self._syntheses = 0

    @staticmethod
    def enabled() -> bool:
        # Explicit laboratory rollback: set to 0 BEFORE starting Voice process.
        return os.environ.get("RGD_VOICE_PIPER_RESIDENT", "1") != "0"

    @staticmethod
    def signature(path: Path) -> tuple:
        model = Path(path).resolve(strict=True)
        if not model.is_file() or model.suffix != ".onnx":
            raise ValueError("Modello Piper ONNX non valido")
        config = Path(str(model) + ".json")
        if not config.is_file():
            raise FileNotFoundError("Configurazione Piper ONNX mancante")
        ms = model.stat()
        cs = config.stat()
        return (str(model), ms.st_size, ms.st_mtime_ns, cs.st_size, cs.st_mtime_ns)

    def _model(self, model_path: Path) -> tuple[object, bool, float]:
        """Caller holds _lock. Never return a cached model for a changed file."""
        sig = self.signature(model_path)
        if self.enabled() and sig == self._signature and self._voice is not None:
            return self._voice, True, 0.0
        from piper import PiperVoice  # lazy: catalog and CI do not need Piper
        started = time.monotonic()
        voice = PiperVoice.load(sig[0])  # unchanged CPU provider/defaults
        loading_seconds = max(0.0, time.monotonic() - started)
        self._loads += 1
        if self.enabled():
            # Replace, never accumulate one ONNX Runtime session per voice.
            self._voice, self._signature = voice, sig
        else:
            self._voice, self._signature = None, None
        return voice, False, loading_seconds

    def prepare(self, model_path: Path) -> dict:
        """Optional explicit prewarm. No audio I/O and no model download."""
        if not self.enabled():
            return {"resident_enabled": False, "cache_hit": False, "load_seconds": 0.0}
        with self._lock:
            _voice, hit, seconds = self._model(model_path)
            return {
                "resident_enabled": True, "cache_hit": hit,
                "load_seconds": round(seconds, 4),
            }

    def synthesize_wav(self, model_path: Path, text: str, target: Path) -> dict:
        """Produce the original full WAV contract; no streaming in VOICE-0.5A."""
        with self._lock:
            voice, hit, load_seconds = self._model(model_path)
            started = time.monotonic()
            with wave.open(str(target), "wb") as wav:
                voice.synthesize_wav(text, wav)
            synth_seconds = max(0.0, time.monotonic() - started)
            self._syntheses += 1
            return {
                "resident_enabled": self.enabled(),
                "cache_hit": hit,
                "load_seconds": round(load_seconds, 4),
                "synthesis_seconds": round(synth_seconds, 4),
            }


    def synthesize_pcm(self, model_path: Path, text: str, on_chunk, *, cancel=None) -> dict:
        """Generate chunks one sentence at a time, delivering immediately.

        The callback receives (raw_pcm_bytes, sample_rate, channels). It must
        apply bounded backpressure and must not keep unbounded audio history.
        The Piper RLock stays held through generation and callback transport,
        preventing another request from modifying the active ONNX voice.
        """
        with self._lock:
            voice, hit, load_seconds = self._model(model_path)
            generator = getattr(voice, "synthesize", None)
            if not callable(generator):
                raise PiperStreamUnavailable("PiperVoice.synthesize non disponibile")
            started = time.monotonic()
            first_pcm_seconds = None
            last_pcm_seconds = None
            pcm_format = None
            chunk_count = 0
            pcm_bytes = 0
            for chunk in generator(text):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError("Streaming Piper annullato")
                rate = getattr(chunk, "sample_rate", None)
                width = getattr(chunk, "sample_width", None)
                channels = getattr(chunk, "sample_channels", None)
                pcm = getattr(chunk, "audio_int16_bytes", None)
                # Piper 1.3+ yields s16 native-endian PCM, one sentence/chunk.
                if type(rate) is not int or not 8000 <= rate <= 48000:
                    raise ValueError("Piper PCM: sample rate non valido")
                if width != 2 or channels != 1:
                    raise ValueError("Piper PCM: solo mono PCM16 supportato")
                if not isinstance(pcm, (bytes, bytearray, memoryview)):
                    raise ValueError("Piper PCM: payload non valido")
                if not pcm:
                    continue
                if len(pcm) > 16 * 1024 * 1024 or len(pcm) % (width * channels):
                    raise ValueError("Piper PCM: blocco eccessivo o non allineato")
                # Bound pathological/buggy voices even with short input text.
                if chunk_count >= 128 or pcm_bytes + len(pcm) > 64 * 1024 * 1024:
                    raise ValueError("Piper PCM: limite complessivo del flusso")
                if pcm_format is None:
                    pcm_format = (rate, width, channels)
                    first_pcm_seconds = max(0.0, time.monotonic() - started)
                elif pcm_format != (rate, width, channels):
                    raise ValueError("Piper PCM: cambio formato nel flusso")
                last_pcm_seconds = max(0.0, time.monotonic() - started)
                on_chunk(pcm, rate, channels)
                chunk_count += 1
                pcm_bytes += len(pcm)
            if not chunk_count:
                raise PiperStreamUnavailable("Piper non ha prodotto blocchi audio PCM")
            self._syntheses += 1
            return {
                "resident_enabled": self.enabled(),
                "cache_hit": hit,
                "load_seconds": round(load_seconds, 4),
                "first_pcm_seconds": round(first_pcm_seconds, 4),
                "last_pcm_seconds": round(last_pcm_seconds, 4),
                # Includes callback backpressure: NOT pure ONNX inference time.
                "producer_wall_seconds": round(time.monotonic() - started, 4),
                "chunk_count": chunk_count,
                "pcm_bytes": pcm_bytes,
                "sample_rate": pcm_format[0],
                "channels": pcm_format[2],
                "audio_seconds": round(pcm_bytes / (pcm_format[0] * pcm_format[1] * pcm_format[2]), 4),
            }

    def status(self) -> dict:
        """Safe monitoring metadata; no raw paths or transcript text."""
        with self._lock:
            return {
                "resident_enabled": self.enabled(),
                "loaded": self._voice is not None and self._signature is not None,
                "model_filename": Path(self._signature[0]).name if self._signature else None,
                "load_count": self._loads,
                "synthesis_count": self._syntheses,
            }

    def clear(self) -> None:
        """Manual benchmark/test cache reset; not exposed over HTTP."""
        with self._lock:
            self._voice = None
            self._signature = None


ENGINE = PiperEngine()
