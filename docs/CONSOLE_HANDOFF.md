# Voice Studio ↔ Console / Visual Feedback — integration handoff v0.1

Date: 2026-10-25. **Design only; no Console/Registry install behavior enabled.**

## Verified upstream boundaries

- Console source of truth: `OpenRGD/rgd-console` branch `main`
  baseline `3395b66f1eae8013b6ffd9cf15866394cbc70c29`.
  Console v0.4A Module Agent is an unprivileged Docker sidecar.
- Module Registry: `OpenRGD/rgd-modules` supports only
  `distribution.kind=pinned_git_cargo`, with Calibration currently listed.
- Visual Feedback: `OpenRGD/rgd-visual-feedback` v0.2 runs independently
  via local Unix socket or `paky-led` CLI.

## Boundaries to preserve

- Voice owns audio capture/playback, VAD, speech recognition, synthesis,
  voice preset per robot and usage telemetry. It does **not** define identity,
  cognitive prompts, RGD schema, Chronon memory, motor commands or body safety.
- Console owns operator UI, explicit approvals, lifecycle visibility and
  discovery. It must not embed ElevenLabs/OpenAI/Piper/Whisper SDKs.
- Registry owns pinned, verified distributions and install descriptions.
- Host Audio Agent (future) owns playback/capture in the logged-in user's
  PulseAudio/PipeWire session. The existing Docker module-agent must never
  inherit direct access to `/dev/snd`, D-Bus or the Docker socket as a shortcut.
- Visual Feedback owns BLE LEDs and desired/applied light state. It is
  non-blocking, never a controller/safety acknowledgement.

## Required changes before the Console can install Voice

1. Define and review a Python service distribution in the registry:
   pinned Git SHA, reviewed manifests, dependency lock/hashes,
   install timeout, read-only audit and explicit operator permission set.
   Current registry schema does not accept this distribution.
2. Extend Module Agent's declared allowlist, confirmations and install
   states for the new package type, without arbitrary subprocess command or
   operator-supplied URLs. Preserve `source_only` as inspectable, not runnable.
3. Implement a separate authenticated host-side service with
   user-level access to audio devices and bounded IPC. Module Agent can
   ask it to deploy via an explicit allowlisted workflow, but cannot
   execute arbitrary host commands or mount host user secrets directly.
4. Expose only minimal API and event contracts to the Console;
   no cloud credentials returned to the browser. Local `.env` remains
   a lab-only secret store; use OpenRGD Vault before wider deployment.
5. Verify the Console's existing module UI with this candidate using
   synthetic and live non-actuating scenarios. Keep audio and BLE errors
   isolated from the Somatic Runtime.

## Voice lifecycle events → Visual Feedback

See `contracts/voice-lifecycle.v0.1.schema.json` (candidate, not canonical):

| Emission criterion (real runtime event) | Visual state |
| --- | --- |
| Microphone actually active and capture started | listening |
| Phrase closed; STT/cognition work starts | thinking |
| PCM playback actually begins (NOT mere TTS request) | speaking |
| Playback stops, no active microphone | idle |
| Capture resumes following playback | listening |
| Service cannot proceed normally | error |

Delivery must be non-blocking, bounded and best-effort, with a queue
and state coalescing for out-of-order/stale transitions. LED acknowledgements
are not proof of audio output. Never synthesize `speaking` from a TTS
request alone. Do not emit `idle` while audio is still playing.

## Voice I/O generic interface (proposed, not implemented)

`GET /health`, `GET /capabilities`, `GET /devices`,
`POST /preview/tts`, `POST /preview/stt`, `GET /voice-profiles/{robot_id}`,
`PUT /voice-profiles/{robot_id}` and read-only lifecycle event stream.
Exact paths/auth/version to be decided in the host agent contract; these
names are illustrative and **must not be treated as supported endpoints**.

## Exit gates

- Voice repository source committed, default branch protected or PR-only
  workflow agreed; prior SHA pinned in Registry descriptor.
- CI green on supported Python versions and Paky-01 hardware smoke tests.
- Python install/source/build verification and untrusted dependency review.
- Sidecar remains non-actuating, not exposed on LAN; approval/audit complete.
- Voice generates real lifecycle events; Visual Feedback handles loss
  or reconnect without blocking I/O.
- Console branch & Pull Request from current main, no Docker volume changes.
