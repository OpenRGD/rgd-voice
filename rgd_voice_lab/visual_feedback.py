"""Optional, best-effort Voice lifecycle -> Visual Feedback local IPC bridge.

This is intentionally not a second BLE driver, an audio observer or a network API.
Enable with RGD_VOICE_VISUAL_FEEDBACK=1 in the *Voice* process environment.

Only fixed, source-authored lifecycle names cross the socket. The worker is
non-blocking to audio code, bounded, time-limited and drops stale events.
"""
from __future__ import annotations

import os
from pathlib import Path
import queue
import socket
import threading
import time
import uuid

VISUAL_SCHEMA = "openrgd.visual.voice-event.v0.1-candidate"
VOICE_SOURCE = "rgd-voice"
VOICE_TO_VISUAL = {
    "capture_started": "listening.started",
    "capture_stopped_processing": "inference.started",
    "playback_started": "playback.started",
    "playback_stopped": "playback.ended",
    "ready_idle": "voice.ready",
    "service_error": "voice.error",
}

# The receiving daemon must be Visual Feedback >= v0.3 candidate (PR #2).
# Existing Visual v0.2 does not support the voice_event command.
SOCKET_NAME = "paky01-visual-feedback.sock"
MAX_PENDING = 12
MAX_AGE_SECONDS = 1.5
SOCKET_TIMEOUT_SECONDS = 0.25


def socket_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(runtime) / SOCKET_NAME


def packet(session_id: str, sequence: int, evidence_kind: str) -> dict:
    """Explicit projection of native Voice evidence names onto Visual IPC."""
    if evidence_kind not in VOICE_TO_VISUAL:
        raise ValueError("Unknown Voice lifecycle evidence")
    return {
        "version": 1,
        "command": "voice_event",
        "schema_version": VISUAL_SCHEMA,
        "source": VOICE_SOURCE,
        "session_id": session_id,
        "sequence": sequence,
        "event": VOICE_TO_VISUAL[evidence_kind],
    }


class VisualRelay:
    """One optional producer; one ordered, bounded sender thread per process.

    Newer transitions never block audio threads. Late queued transitions are
    dropped rather than replaying an obsolete 'listening' or 'speaking' state.
    """

    def __init__(self):
        self.session_id = uuid.uuid4().hex
        self.sequence = 0
        self._pending = queue.Queue(maxsize=MAX_PENDING)
        self._lock = threading.Lock()
        self._worker = None

    def submit(self, evidence_kind: str) -> None:
        if evidence_kind not in VOICE_TO_VISUAL:
            raise ValueError("Unknown Voice lifecycle evidence")
        with self._lock:
            payload = packet(self.session_id, self.sequence, evidence_kind)
            self.sequence += 1
            item = (time.monotonic(), payload)
            try:
                self._pending.put_nowait(item)
            except queue.Full:
                try:
                    self._pending.get_nowait()
                    self._pending.task_done()
                except queue.Empty:
                    pass
                self._pending.put_nowait(item)
            if self._worker is None:
                self._worker = threading.Thread(
                    target=self._run, daemon=True, name="rgd-voice-visual-ipc"
                )
                self._worker.start()

    def _send(self, payload: dict) -> None:
        # Fixed request only: no URLs, shell, credentials, audio or user text.
        import json

        raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(SOCKET_TIMEOUT_SECONDS)
            conn.connect(str(socket_path()))
            conn.sendall(raw)
            # Read the ack so that the server can finish gracefully. This does
            # not prove the light changed; the reply may be a logical accept.
            response = conn.recv(1024)
            if not response:
                raise ConnectionError("Visual Feedback returned no response")

    def _run(self) -> None:
        while True:
            enqueued_at, payload = self._pending.get()
            try:
                if time.monotonic() - enqueued_at <= MAX_AGE_SECONDS:
                    self._send(payload)
            except (OSError, ValueError, ConnectionError):
                # Visual unavailable or still v0.2: optional effects are lost,
                # but Voice never blocks, retries, or reports a false success.
                pass
            finally:
                self._pending.task_done()


_RELAY = VisualRelay()


def emit(evidence_kind: str) -> bool:
    """Return scheduling status only, not an LED delivery/physical ack."""
    if os.environ.get("RGD_VOICE_VISUAL_FEEDBACK") != "1":
        return False
    try:
        _RELAY.submit(evidence_kind)
    except (ValueError, RuntimeError, OSError):
        return False
    return True
