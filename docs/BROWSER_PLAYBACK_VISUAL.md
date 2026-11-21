# Voice Studio browser Play → Visual Feedback (candidate v0.1)

## Why the Play buttons behaved differently

- **▶ Ascolta sulla Bose** calls `POST /api/play` → `core.playback()` → real `paplay` subprocess. The Voice v0.3.2 candidate reports the server-side subprocess lifecycle to Visual Feedback.
- **▶ Play in the embedded HTML `<audio>` player** loads `/api/audio/{clip_id}` directly from Firefox and plays through the *browser's selected audio output*. The existing Voice backend never saw a `core.playback()` callback. The OS can happen to route both outputs to the same Bose speaker, but this is not guaranteed or evidence of a specific speaker.

## This branch

- Each Voice Studio-created HTML media player gets local `playing`, `pause`, `ended`, `error`, `emptied` handlers; it emits one best-effort event when it starts and stops.
- The browser sends an **allowlisted, local-only** JSON request to `POST /api/browser-playback` with `X-Voice-Lab-Setup: local`. Existing Host/Origin checks apply, untrusted paths and unknown event fields fail closed, and `clip_id` must identify a locally known WAV for `playing`.
- New playback sessions use a fresh random hex ID and a monotonic request counter. A per-process bounded gate rejects duplicate/stale start/end messages; an old player's `ended` cannot stop a newer player's state. Only one browser HTML player at a time is deliberately active per tab.
- Backend converts these to existing Voice lifecycle evidence `playback_started` / `playback_stopped`. The existing optional nonblocking Voice→Visual sender owns the IPC. The browser never connects to BLE, the Visual Unix socket, or a physical speaker.
- Browser notifications are **OFF until `RGD_VOICE_VISUAL_FEEDBACK=1`** on the running Voice backend (flag inherited from the parent PR). Without the flag or without an updated Visual daemon, audio playback stays unaffected.

## Evidence boundaries

An HTMLMediaElement `playing` event is **browser-reported playback**, not measured sound at Bose or any specific output device. An HTTP event acceptance is not proof the LED received or emitted light. This source must remain tagged as browser media and not be promoted to physical audio evidence.

`Visual Feedback` v0.3 Voice ingress is required for live LED changes; the current running Visual daemon on Paky-01 has passed a synthetic smoke test, but this feature is not yet deployed.

## Follow-up gates

- Merge/review `OpenRGD/rgd-voice` PR #2 (Voice lifecycle), and `OpenRGD/rgd-visual-feedback` PR #2 (Visual IPC).
- Test ordinary HTML Play, pause, resume, playback end, switching clips and the server-side Bose button on Paky-01 with no cloud costs.
- Confirm disabling LED service or feature flag cannot stop or delay browser audio.
- Define rule-engine arbitration for multiple concurrent Voice sources (browser preview, `paplay`, STT/VAD), with TTL/priority so an abandoned browser tab cannot leave a visually stale `speaking` state.
- Console Visual Effects Studio profile authoring is a separate preview-only feature, **not** a live controller.

This PR is built atop `feat/visual-feedback-lifecycle-v0.3.2` and only adds the browser event source. It must not be merged into Voice `main` independently until the parent has been reviewed.
