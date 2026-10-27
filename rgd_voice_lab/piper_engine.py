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
