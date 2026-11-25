"""VOICE↔RI-01 candidate: consented, metadata-only segment fan-out.

This opt-in adapter NEVER opens a microphone, stores PCM, creates an RI
ObservationEnvelope/ObservationRef, sends network traffic or edits Visual/ASR.
It describes an already-closed, local WAV captured by the existing Voice owner.

ONLY an explicit caller-supplied consent window + local in-process consumer
enables it; the production Voice pilot passes neither by default. The callback
may perform its own work, but cannot block the VAD/capture thread.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from queue import Empty, Full, Queue
import re
import threading
import time
import uuid
import wave

SCHEMA_VERSION = "voice.audio-segment-closed-report.v0.1-candidate"
MAX_PENDING = 4
MAX_WINDOW_SECONDS = 120
MAX_WAV_BYTES = 2 * 1024 * 1024
SOURCE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
SESSION_ID_RE = re.compile(r"[0-9a-f]{32}\Z")

# No frame-granular callback here; no acquisition or second reader.
# A closed WAV is ephemeral, and a future RI store must independently
# authorize and record its actual bytes before issuing ObservationRef.


@dataclass(frozen=True)
class ConsentWindow:
    """Operator-supplied local *metadata-only* permission for one session."""

    producer_session_id: str
    purpose: str
    issued_monotonic_ns: int
    expires_monotonic_ns: int
    scope: str = "metadata_only"

    def __post_init__(self):
        if not SESSION_ID_RE.fullmatch(self.producer_session_id):
            raise ValueError("Invalid producer session id")
        if not isinstance(self.purpose, str) or not 1 <= len(self.purpose) <= 100:
            raise ValueError("Explicit purpose is required")
        if any(ord(ch) < 32 for ch in self.purpose):
            raise ValueError("Invalid purpose")
        if self.scope != "metadata_only":
            raise ValueError("No audio/media authorization in this candidate")
        a, b = self.issued_monotonic_ns, self.expires_monotonic_ns
        if type(a) is not int or type(b) is not int or a < 0 or b <= a:
            raise ValueError("Invalid monotonic consent window")
        if b - a > MAX_WINDOW_SECONDS * 1_000_000_000:
            raise ValueError("Consent window too long")

    @classmethod
    def for_explicit_local_session(cls, *, purpose: str, ttl_seconds: int):
        """Only for callers with independent operator consent. NOT a UI grant."""
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= MAX_WINDOW_SECONDS:
            raise ValueError("TTL must be 1–120 seconds")
        now = time.monotonic_ns()
        return cls(uuid.uuid4().hex, purpose, now,
                   now + ttl_seconds * 1_000_000_000)


def _boot_id() -> str | None:
    """Read host boot UUID as a *clock label*, not synchronization proof."""
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        return value if value and len(value) <= 128 else None
    except OSError:
        return None


def _pcm_metadata(path: Path) -> dict:
    """Read only RIFF header; no ambient speech payload is retained or sent."""
    if path.suffix.lower() != ".wav" or not path.is_file():
        raise ValueError("Closed WAV not available")
    if path.stat().st_size > MAX_WAV_BYTES:
        raise ValueError("Closed WAV exceeds bounded candidate size")
    with wave.open(str(path), "rb") as wav:
        if wav.getcomptype() != "NONE":
            raise ValueError("Compressed WAV not supported")
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.getnframes()
        if channels != 1 or sample_width != 2 or not 8000 <= rate <= 48000:
            raise ValueError("Only mono PCM16, 8–48kHz allowed")
        if not 0 < frames <= rate * 30:
            raise ValueError("Invalid segment duration")
        return {
            "media_type": "audio/wav",
            "pcm_sample_rate_hz": rate,
            "pcm_channels": channels,
            "pcm_sample_width_bytes": sample_width,
            "sample_frame_count": frames,
        }


def make_closed_segment_report(*, consent: ConsentWindow, source_id: str,
                               segment_sequence: int, pcm_format: dict,
                               closed_monotonic_ns: int,
                               host_boot_id: str | None) -> dict:
    """Pure metadata projection. NEVER produce an RI observation identifier."""
    if not isinstance(consent, ConsentWindow):
        raise ValueError("ConsentWindow is mandatory")
    if not isinstance(source_id, str) or not SOURCE_ID_RE.fullmatch(source_id):
        raise ValueError("Unverified local source hint required")
    if type(segment_sequence) is not int or segment_sequence < 0:
        raise ValueError("Invalid segment sequence")
    if type(closed_monotonic_ns) is not int or not (
        consent.issued_monotonic_ns <= closed_monotonic_ns <
        consent.expires_monotonic_ns
    ):
        raise ValueError("Segment outside consent window")
    if host_boot_id is not None and (
        not isinstance(host_boot_id, str) or not host_boot_id or len(host_boot_id) > 128
    ):
        raise ValueError("Invalid host boot label")
    if not isinstance(pcm_format, dict) or set(pcm_format) != {
        "media_type", "pcm_sample_rate_hz", "pcm_channels",
        "pcm_sample_width_bytes", "sample_frame_count",
    }:
        raise ValueError("Invalid PCM metadata keys")
    if pcm_format["media_type"] != "audio/wav" or (
        pcm_format["pcm_channels"] != 1 or
        pcm_format["pcm_sample_width_bytes"] != 2 or
        type(pcm_format["pcm_sample_rate_hz"]) is not int or
        not 8000 <= pcm_format["pcm_sample_rate_hz"] <= 48000 or
        type(pcm_format["sample_frame_count"]) is not int or
        not 0 < pcm_format["sample_frame_count"] <=
        pcm_format["pcm_sample_rate_hz"] * 30
    ):
        raise ValueError("Unsupported PCM metadata")
    return {
        "schema_version": SCHEMA_VERSION,
        "producer": "rgd-voice",
        "report_kind": "metadata_only_closed_audio_segment",
        "producer_session_id": consent.producer_session_id,
        "segment_sequence": segment_sequence,
        "source_id": source_id,
        "source_id_verified": False,
        "capture_owner_status": "voice_local_unmanaged",
        "consent": {
            "purpose": consent.purpose,
            "scope": "metadata_only",
            "expires_monotonic_ns": consent.expires_monotonic_ns,
        },
        "format": dict(pcm_format),
        "time": {
            "clock_domain_id": "host.monotonic.local_process",
            "boot_id": host_boot_id,
            "segment_closed_monotonic_ns": closed_monotonic_ns,
            "source_measurement_time_status": "unknown",
            "source_measurement_time": None,
            "chronograf_anchor_ref": None,
        },
        # RI creates these only if it really writes/verifies an Observation.
        "ri_observation_ref": None,
        "payload_attached": False,
        "storage_status": "not_persisted",
        "history_committed": False,
        "actuation_enabled": False,
    }


class LocalSegmentMetadataFanout:
    """Best-effort, non-blocking queue for a consented IN-PROCESS consumer.

    Default production Voice does not instantiate or connect this adapter.
    Worker reads WAV header only; no PCM or transcript enters the queue.
    """

    def __init__(self, *, consent: ConsentWindow, source_id: str, consumer):
        if not isinstance(consent, ConsentWindow):
            raise TypeError("Explicit consent required")
        if not isinstance(source_id, str) or not SOURCE_ID_RE.fullmatch(source_id):
            raise ValueError("Invalid unverified source hint")
        if not callable(consumer):
            raise TypeError("Explicit local metadata consumer required")
        self.consent = consent
        self.source_id = source_id
        self._consumer = consumer
        self._queue = Queue(maxsize=MAX_PENDING)
        self._lock = threading.Lock()
        self._worker = None
        self._sequence = 0
        self._stats = Counter()

    def submit_closed_wav(self, path) -> bool:
        """Called AFTER capture completes; no file I/O or waits in this method."""
        now = time.monotonic_ns()
        if not self.consent.issued_monotonic_ns <= now < self.consent.expires_monotonic_ns:
            with self._lock:
                self._stats["dropped_expired_or_unconsented"] += 1
            return False
        if not isinstance(path, (str, Path)):
            with self._lock:
                self._stats["dropped_invalid_path"] += 1
            return False
        with self._lock:
            sequence = self._sequence
            self._sequence += 1
            try:
                self._queue.put_nowait((Path(path), sequence, now))
            except Full:
                self._stats["dropped_queue_full"] += 1
                return False
            self._stats["scheduled"] += 1
            if self._worker is None:
                self._worker = threading.Thread(
                    target=self._run, daemon=True,
                    name="rgd-voice-ri-metadata-local",
                )
                self._worker.start()
        return True

    def _run(self):
        while True:
            path, sequence, closed_at = self._queue.get()
            try:
                # Consent may expire before a queued item is actually consumed.
                if time.monotonic_ns() >= self.consent.expires_monotonic_ns:
                    with self._lock:
                        self._stats["dropped_expired_in_queue"] += 1
                    continue
                pcm_format = _pcm_metadata(path)
                report = make_closed_segment_report(
                    consent=self.consent, source_id=self.source_id,
                    segment_sequence=sequence, pcm_format=pcm_format,
                    closed_monotonic_ns=closed_at, host_boot_id=_boot_id(),
                )
                # Worker may be delayed after reading header.
                if time.monotonic_ns() >= self.consent.expires_monotonic_ns:
                    with self._lock:
                        self._stats["dropped_expired_in_queue"] += 1
                    continue
                self._consumer(report)
                with self._lock:
                    self._stats["delivered_metadata"] += 1
            except Exception:
                # Includes callback absent/faulty, temp file expired or bad WAV.
                # No exception or retry escapes into capture/VAD critical path.
                with self._lock:
                    self._stats["dropped_consumer_or_source_error"] += 1
            finally:
                self._queue.task_done()

    def status(self) -> dict:
        """Counters only; no source audio, transcript, host path, or secrets."""
        with self._lock:
            return {
                "enabled_for_explicit_session": True,
                "queue_size": self._queue.qsize(),
                "max_pending": MAX_PENDING,
                **{k: int(v) for k, v in self._stats.items()},
            }

    def drain_for_test(self, timeout_seconds: float = 2.0) -> bool:
        """Bounded test helper; NEVER invoke from a live audio callback."""
        if not 0 <= timeout_seconds <= 10:
            raise ValueError("Invalid test wait")
        until = time.monotonic() + timeout_seconds
        while self._queue.unfinished_tasks and time.monotonic() < until:
            time.sleep(0.005)
        return self._queue.unfinished_tasks == 0
