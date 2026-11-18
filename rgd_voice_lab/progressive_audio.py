"""VOICE-0.5A.1 — opt-in Piper sentence PCM -> PulseAudio output.

Real overlap: one ONNX producer, one pacat consumer, bounded queue (2 chunks).
This does NOT implement simultaneous microphone capture, AEC, barge-in or
Piper token-level audio generation. Default full WAV behavior is unchanged.
"""
from __future__ import annotations

import os
from queue import Empty, Full, Queue
import select
import shutil
import subprocess
import sys
import threading
import time

from . import core, piper_engine, visual_feedback, voice_controls

MAX_WRITE_BYTES = 32768
WRITE_STALL_SECONDS = 10.0
PLAYBACK_DRAIN_SECONDS = 120.0
PRODUCER_STOP_WAIT_SECONDS = 15.0
QUEUE_CAPACITY_CHUNKS = 2
_END = object()


class ProgressivePlaybackError(RuntimeError):
    """PCM output failed; may have played some audio, so never blind-replay."""


class RawPulsePlayer:
    """One subprocess, native mono PCM16, sink explicitly selected by caller."""

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
        cmd = [exe, "--playback", "--raw", "--format=s16le",
               f"--rate={rate}", f"--channels={channels}", f"--device={self.sink}",
               "--client-name=OpenRGD-Voice-Progressive"]
        self.process = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, shell=False)
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
            _r, writable, _x = select.select([], [fd], [], 0.1)
            if not writable:
                continue
            try:
                sent = os.write(fd, view[:MAX_WRITE_BYTES])
            except BlockingIOError:
                continue
            if sent <= 0:
                raise ProgressivePlaybackError("pacat: nessun byte scritto")
            view = view[sent:]
            self.bytes_written += sent
            last_write = time.monotonic()
            if self.first_write_seconds is None:
                self.first_write_seconds = time.monotonic() - self.origin
                # This is first PCM written to pacat, not measured acoustic
                # onset; Bose A2DP buffering adds an unknown latency.
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
        """Reap the player after success/error/interrupt, no orphan process."""
        p = self.process
        if p is None:
            return
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=2)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=2)
        if p.stdin is not None and not p.stdin.closed:
            try:
                p.stdin.close()
            except BrokenPipeError:
                pass


def _fallback(fallback, reason: str) -> dict:
    synth, played, elapsed = fallback()
    return {
        "mode": "full_wav_fallback",
        "fallback_reason": reason,
        "synthesis_seconds": synth,
        "playback_seconds": played,
        "total_seconds": elapsed,
    }


def speak(profile: dict, text: str, sink: str, *, fallback, cancel=None) -> dict:
    """Prefer local Piper sentence streaming; switch to WAV only pre-audio.

    The callback 'fallback' is the existing authorized synthesize + playback
    path; it MUST NOT be called after any PCM has reached PulseAudio.
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
    # ffmpeg's saved speed/pitch effects require a complete WAV. Preserve
    # the operator's settings instead of silently outputting unmodified audio.
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
    event = cancel if cancel is not None else threading.Event()
    q = Queue(maxsize=QUEUE_CAPACITY_CHUNKS)
    stats = {}
    errors = []
    producer_done = []
    player = RawPulsePlayer(sink, origin=origin, cancel=event)

    def enqueue(pcm, rate, channels):
        # Piper generates complete sentence chunks; cap is enforced in
        # piper_engine. Max queued: 2 chunks, no unbounded audio retention.
        while not event.is_set():
            try:
                q.put((pcm, rate, channels), timeout=0.1)
                return
            except Full:
                pass
        raise InterruptedError("Producer PCM annullato")

    def produce():
        try:
            stats.update(piper_engine.ENGINE.synthesize_pcm(
                core.piper_path(model["model"]), text, enqueue, cancel=event))
        except BaseException as exc:
            errors.append(exc)
        finally:
            producer_done.append(time.monotonic() - origin)
            # Guaranteed sentinel on normal exit/failure. On cancellation,
            # the consumer has stopped and does not need another queue item.
            while not event.is_set():
                try:
                    q.put(_END, timeout=0.1)
                    break
                except Full:
                    pass

    producer = threading.Thread(target=produce, name="rgd-piper-pcm", daemon=True)
    producer.start()
    try:
        while True:
            if event.is_set():
                raise InterruptedError("Streaming PCM annullato")
            try:
                item = q.get(timeout=0.1)
            except Empty:
                if not producer.is_alive():
                    # A producer crash must not hang the audio loop.
                    break
                continue
            if item is _END:
                break
            pcm, rate, channels = item
            player.write(pcm, rate, channels)

        producer.join(timeout=PRODUCER_STOP_WAIT_SECONDS)
        if producer.is_alive():
            raise TimeoutError("Thread Piper non terminato")
        if errors:
            raise errors[0]
        if not stats:
            raise ProgressivePlaybackError("Nessuna metrica di sintesi PCM")
        # Production can finish before the consumer drains its queue.
        synthesized_at = producer_done[0]
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
    except BaseException as exc:
        event.set()
        player.close()
        producer.join(timeout=PRODUCER_STOP_WAIT_SECONDS)
        if producer.is_alive():
            visual_feedback.emit("service_error")
            raise RuntimeError("Producer Piper non terminato; interrompere sessione") from exc
        if player.first_write_seconds is None:
            if isinstance(exc, piper_engine.PiperStreamUnavailable):
                return _fallback(fallback, "piper_stream_unavailable")
            if isinstance(exc, (OSError, ProgressivePlaybackError, TimeoutError)):
                return _fallback(fallback, "raw_player_unavailable")
        visual_feedback.emit("service_error")
        raise
    finally:
        event.set()
        player.close()
