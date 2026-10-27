"""VOICE-0.5A.1 — opt-in, bounded Piper -> PulseAudio raw PCM playback.

This is experimental HALF DUPLEX, not a capture service, AEC or barge-in.
The established full WAV + paplay path remains the default and fallback.
Piper emits sentence-level AudioChunk objects (NOT phoneme or token streams).
"""
from __future__ import annotations

import os
import select
import shutil
import subprocess
import sys
import time

from . import core, piper_engine, visual_feedback, voice_controls

MAX_WRITE_BYTES = 32768
WRITE_STALL_SECONDS = 10.0
PLAYBACK_DRAIN_SECONDS = 120.0


class ProgressivePlaybackError(RuntimeError):
    """Stream failed. May have already played partial audio: do not replay."""


class RawPulsePlayer:
    """One pacat process, native PCM format, kernel-pipe backpressure."""

    def __init__(self, sink: str, *, origin: float, cancel=None):
        self.sink = sink
        self.origin = origin
        self.cancel = cancel
        self.process = None
        self.first_write_seconds = None
        self.process_start_seconds = None
        self.bytes_written = 0
        self.completed = False

    def _start(self, rate: int, channels: int) -> None:
        exe = shutil.which("pacat")
        if not exe:
            raise FileNotFoundError("pacat non disponibile sul mini-PC")
        args = [exe, "--playback", "--raw", "--format=s16le",
                f"--rate={rate}", f"--channels={channels}", f"--device={self.sink}",
                "--client-name=OpenRGD-Voice-Progressive"]
        self.process = subprocess.Popen(
            args, stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
        self.process_start_seconds = time.monotonic() - self.origin
        if self.process.stdin is None or self.process.poll() is not None:
            raise ProgressivePlaybackError("pacat non avviato")
        os.set_blocking(self.process.stdin.fileno(), False)

    def write(self, pcm, rate: int, channels: int) -> None:
        if self.process is None:
            self._start(rate, channels)
        fd = self.process.stdin.fileno()
        view = memoryview(pcm)
        last_write = time.monotonic()
        while view:
            if self.cancel is not None and self.cancel.is_set():
                raise InterruptedError("Riproduzione PCM annullata")
            if self.process.poll() is not None:
                raise ProgressivePlaybackError(f"pacat interrotto ({self.process.returncode})")
            if time.monotonic() - last_write > WRITE_STALL_SECONDS:
                raise TimeoutError("pacat: pipe PCM bloccata")
            _read, writable, _errors = select.select([], [fd], [], 0.1)
            if not writable:
                continue
            try:
                sent = os.write(fd, view[:MAX_WRITE_BYTES])
            except BlockingIOError:
                continue
            if sent <= 0:
                raise ProgressivePlaybackError("pacat: nessun byte scritto")
            view = view[sent:]
            last_write = time.monotonic()
            self.bytes_written += sent
            if self.first_write_seconds is None:
                self.first_write_seconds = time.monotonic() - self.origin
                # A successful pipe write marks *player activity*, not sound
                # actually heard over a buffered Bluetooth A2DP link.
                visual_feedback.emit("playback_started")

    def finish(self) -> float:
        if not self.process or self.first_write_seconds is None:
            raise ProgressivePlaybackError("Nessun PCM inviato al player")
        if self.process.stdin and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            code = self.process.wait(timeout=PLAYBACK_DRAIN_SECONDS)
        except subprocess.TimeoutExpired:
            raise TimeoutError("pacat: timeout durante lo svuotamento dell'audio") from None
        if code:
            raise ProgressivePlaybackError(f"pacat: uscita con codice {code}")
        self.completed = True
        visual_feedback.emit("playback_stopped")
        return time.monotonic() - self.origin

    def close(self) -> None:
        """Always reap the subprocess; never leave a second BLE/audio owner."""
        if self.process is None:
            return
        p = self.process
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=2)
        if p.stdin is not None and not p.stdin.closed:
            p.stdin.close()


def _fallback(fallback, reason: str) -> dict:
    """Fallback before any PCM has reached the pipe, never after."""
    synth, playback, elapsed = fallback()
    return {
        "mode": "full_wav_fallback", "fallback_reason": reason,
        "synthesis_seconds": synth, "playback_seconds": playback,
        "total_seconds": elapsed,
    }


def speak(profile: dict, text: str, sink: str, *, fallback, cancel=None) -> dict:
    """Stream only local Piper with default speed/pitch; all else uses WAV.

    The fallback callback must implement the existing authorized Voice path.
    It may be invoked ONLY when no PCM bytes have been queued to PulseAudio.
    """
    model = core.MODELS.get(profile.get("model_id"))
    if not model or model["provider"] != "Piper":
        raise ValueError("Streaming progressivo disponibile solo per Piper locale")
    if profile.get("voice_id") != model["model"]:
        raise ValueError("Voce Piper incoerente con modello predefinito")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 650:
        raise ValueError("Testo: 1–650 caratteri")
    options = voice_controls.normalize(profile.get("controls"))
    voice_controls.validate_for_model(model, options)
    # Local FFmpeg postprocessing acts on *complete* WAV; never silently
    # drop the saved speed and pitch settings for progressive output.
    if voice_controls.needs_local_fx(options):
        return _fallback(fallback, "speed_pitch_require_full_wav")
    if not piper_engine.ENGINE.enabled():
        return _fallback(fallback, "piper_resident_disabled")
    if sys.byteorder != "little":
        return _fallback(fallback, "pcm_native_endian_unsupported")
    if not shutil.which("pacat"):
        return _fallback(fallback, "pacat_unavailable")
    core.require_ready(model)
    if sink not in core.endpoints("sinks"):
        raise RuntimeError("Cassa selezionata non disponibile")
    origin = time.monotonic()
    player = RawPulsePlayer(sink, origin=origin, cancel=cancel)
    try:
        stats = piper_engine.ENGINE.synthesize_pcm(
            core.piper_path(model["model"]), text, player.write, cancel=cancel)
        synthesized_at = time.monotonic() - origin
        playback_ended_at = player.finish()
        return {
            "mode": "progressive_pcm",
            **stats,
            "process_start_seconds": round(player.process_start_seconds, 4),
            "first_write_seconds": round(player.first_write_seconds, 4),
            "synthesis_done_seconds": round(synthesized_at, 4),
            "playback_drain_seconds": round(playback_ended_at, 4),
            "synthesis_seconds": round(synthesized_at, 4),
            "playback_seconds": round(playback_ended_at - player.process_start_seconds, 4),
            "total_seconds": round(playback_ended_at, 4),
            "audio_observed_on_bose": False,
        }
    except piper_engine.PiperStreamUnavailable as exc:
        if player.first_write_seconds is None:
            return _fallback(fallback, "piper_stream_unavailable")
        visual_feedback.emit("service_error")
        raise ProgressivePlaybackError(str(exc)) from exc
    except (OSError, TimeoutError, ProgressivePlaybackError) as exc:
        if player.first_write_seconds is None:
            # No bytes submitted yet: safe to synthesize and play the old WAV.
            return _fallback(fallback, "raw_player_unavailable")
        visual_feedback.emit("service_error")
        raise
    except BaseException:
        visual_feedback.emit("service_error")
        raise
    finally:
        player.close()
