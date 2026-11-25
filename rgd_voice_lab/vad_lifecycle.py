"""VOICE-0.4D temporary observer for the commissioned Paky-01 legacy VAD.

The existing capture_phrase and 0.1D VAD algorithm remain unchanged.
This *single-threaded foreground lab bridge* observes the first nonempty
PCM frame returned from legacy read_exact (actual parec capture), rather
than assuming that starting a turn has opened a microphone.

This compatibility shim patches only the imported legacy module's local
read_exact binding while capture_phrase is active, under one process lock.
It MUST be removed after the PERIPH-reviewed Audio Capture owner exposes
its own frame/capture lifecycle callback. Not a general device manager.
"""
from __future__ import annotations

import os
import threading
from typing import Any

from . import visual_feedback

_OBSERVER_LOCK = threading.Lock()


def _submit_ri_metadata_after_capture(result: Any, path: Any, fanout: Any) -> None:
    """Never block or fail the VAD for a best-effort, explicitly authorized tap."""
    if not result or fanout is None:
        return
    try:
        # This is an O(1) queue scheduling operation: NO PCM reads here.
        fanout.submit_closed_wav(path)
    except Exception:
        # An opt-in RI consumer can fail without changing speech outcome.
        pass


def capture_phrase_observed(legacy: Any, mic: str, path: Any, *,
                            ri_metadata_fanout: Any = None,
                            **options: Any) -> Any:
    """Current Voice-owned microphone remains unchanged; RI metadata is opt-in."""
    if os.environ.get('RGD_VOICE_VISUAL_FEEDBACK') != '1':
        result = legacy.capture_phrase(mic, path, **options)
        _submit_ri_metadata_after_capture(result, path, ri_metadata_fanout)
        return result

    with _OBSERVER_LOCK:
        original_read = legacy.read_exact
        received_pcm = False

        def observed_read(stream: Any, length: int) -> bytes:
            nonlocal received_pcm
            frame = original_read(stream, length)
            if frame and not received_pcm:
                received_pcm = True
                visual_feedback.emit('capture_started')
            return frame

        # Compatibility shim: no patch to subprocess, system PulseAudio or the
        # VAD algorithm. Scoped restoration also covers exceptions/interrupts.
        legacy.read_exact = observed_read
        try:
            result = legacy.capture_phrase(mic, path, **options)
        except KeyboardInterrupt:
            if received_pcm:
                visual_feedback.emit('ready_idle')
            raise
        except Exception:
            visual_feedback.emit('service_error')
            raise
        finally:
            legacy.read_exact = original_read

        if not result and received_pcm:
            visual_feedback.emit('ready_idle')

    # Must run OUTSIDE the temporary legacy read_exact patch and observer lock.
    _submit_ri_metadata_after_capture(result, path, ri_metadata_fanout)
    return result


def transcribe_observed(legacy: Any, model: Any, capture_path: Any) -> tuple[str, float]:
    """Maintain the processing visual state through STT and subsequent TTS.

    Only the actual audio player, instrumented by core.playback, may emit
    playback_started. An empty transcript returns to idle.
    """
    visual_feedback.emit('capture_stopped_processing')
    try:
        recognized, elapsed = legacy.transcribe(model, capture_path, vad=False)
    except Exception:
        visual_feedback.emit('service_error')
        raise
    if not recognized or not recognized.strip():
        visual_feedback.emit('ready_idle')
    return recognized, elapsed
