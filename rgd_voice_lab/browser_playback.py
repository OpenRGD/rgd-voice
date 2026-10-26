"""Browser HTMLMediaElement event ingress for localhost Voice Studio.

A browser 'playing' callback indicates media playback reported by the browser,
not independently verified audio at a specific physical speaker.

This module does not open devices or call BLE. Events are nonpersistent,
bounded and handed to the optional, existing Visual relay.
"""
from __future__ import annotations

from collections import deque
import re
from threading import Lock
from typing import Callable, Any

_SESSION = re.compile(r"^[a-f0-9]{32}$")
_CLIP = re.compile(r"^[a-f0-9]{32}$")
_ALLOWED_EVENTS = frozenset(("playing", "stopped"))
_FIELDS = frozenset(("clip_id", "playback_session_id", "sequence", "event"))


def validate_browser_playback(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise ValueError("Invalid browser playback event fields")
    if not isinstance(payload["clip_id"], str) or not _CLIP.fullmatch(payload["clip_id"]):
        raise ValueError("Invalid clip_id")
    if not isinstance(payload["playback_session_id"], str) or not _SESSION.fullmatch(payload["playback_session_id"]):
        raise ValueError("Invalid playback session")
    if type(payload["sequence"]) is not int or not 0 <= payload["sequence"] < 2**63:
        raise ValueError("Invalid playback sequence")
    if payload["event"] not in _ALLOWED_EVENTS:
        raise ValueError("Invalid playback event")
    return payload


class BrowserPlaybackGate:
    """One active browser-preview owner. Stale end events cannot clear a newer preview.

    It is a local best-effort UI event source, not media attestation/authn.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._active: tuple[str, str] | None = None
        self._last_sequence: dict[str, int] = {}
        self._retired: deque[str] = deque(maxlen=64)

    def consume(self, payload: Any, emit: Callable[[str], bool]) -> dict:
        p = validate_browser_playback(payload)
        session = p["playback_session_id"]
        clip = p["clip_id"]
        sequence = p["sequence"]
        kind = p["event"]
        with self._lock:
            if session in self._retired:
                return {"accepted": False, "ignored": "retired_browser_session", "visual_scheduled": False}
            if sequence <= self._last_sequence.get(session, -1):
                return {"accepted": False, "ignored": "duplicate_or_out_of_order", "visual_scheduled": False}
            self._last_sequence[session] = sequence
            if kind == "playing":
                if self._active == (session, clip):
                    return {"accepted": False, "ignored": "already_playing", "visual_scheduled": False}
                if self._active is not None:
                    self._retired.append(self._active[0])
                self._active = (session, clip)
                scheduled = bool(emit("playback_started"))
            else:
                if self._active != (session, clip):
                    return {"accepted": False, "ignored": "not_current_playback", "visual_scheduled": False}
                self._active = None
                self._retired.append(session)
                scheduled = bool(emit("playback_stopped"))
            # Cap memory; recently retired session tracking is sufficient.
            if len(self._last_sequence) > 128:
                keep = set(self._retired)
                if self._active is not None:
                    keep.add(self._active[0])
                self._last_sequence = {key: value for key, value in self._last_sequence.items() if key in keep}
            return {
                "accepted": True, "visual_scheduled": scheduled,
                "evidence_kind": "browser_media_element_event_unverified_physical_output",
                "physical_audio_observed": False,
            }
