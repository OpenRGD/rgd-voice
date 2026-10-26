# ADR (candidate) — Audio I/O ownership: Voice Studio, PERIPH-0.2, Reality Ingress, Visual Feedback

Date: 2026-10-26  
Status: **PROPOSED, documentation only**  
Scope: **Voice Studio / Paky-01 audio I/O**, model-agnostic. No Cognition, RGD identity or motor work.

## Evidence / source status

- `OpenRGD/rgd-voice` main at `a2ea4e1`: the v0.3.1 audio laboratory uses `pactl` discovery, `parecord` capture and `paplay` playback on Paky-01. It does **not** use the PERIPH broker.
- Paky-01 host: Ubuntu 22.04, PulseAudio; BC-Master 1610USB **input** verified; Bose Micro SoundLink A2DP **output** verified. Voice Lab stores local preferences and per-robot TTS default independently of canonical RGD identity; cloud keys remain host-private.
- Console PR [#5](https://github.com/OpenRGD/rgd-console/pull/5) (PERIPH-0.1) defines candidate `console.peripheral-{descriptor,status,action,action-result}.v0.1-candidate`. Console PR [#6](https://github.com/OpenRGD/rgd-console/pull/6) (PERIPH-0.2) adds a rootless same-UID host broker; the only implemented physical probe is **USB serial IMU**. The PR is stacked/draft, and host deployment/Console buttons are **not** proven.
- Voice PR [#2](https://github.com/OpenRGD/rgd-voice/pull/2) and Visual PR [#2](https://github.com/OpenRGD/rgd-visual-feedback/pull/2) are existing **draft** complementary Voice→Visual lifecycle candidate implementations. Both require review, merge, then deliberate physical validation. Do not create another bridge.
- `OpenRGD/rgd-reality-ingress` exists as a private GitHub repository but is currently **empty**. Previous camera prototype work is not evidence of a microphone/Chronon service on Paky-01.

References: [PERIPH-0.1 design](https://github.com/OpenRGD/rgd-console/blob/feat/periph-0.2-host-manager/docs/PERIPHERAL-MANAGER-CORE-v01.md), [PERIPH-0.2 host commissioning](https://github.com/OpenRGD/rgd-console/blob/feat/periph-0.2-host-manager/docs/PERIPH-0.2-HOST-MANAGER-BENCH.md), [Voice visual bridge](https://github.com/OpenRGD/rgd-voice/blob/feat/visual-feedback-lifecycle-v0.3.2/docs/VISUAL_FEEDBACK_BRIDGE.md).

## Decision: four owners, not four physical device opens

| Scope | Owner | May perform | Must not perform |
| --- | --- | --- | --- |
| Device identity/presence, binding, resource leases, acquisition service lifecycle, host readiness | **Peripheral Manager + host acquisition owner** (future audio adapter) | Publish separate device/publisher/stream status; start/stop only through reviewed allowlisted explicit operator actions | Interpret user speech; own VAD or voices; assume a running process means stream is live; auto-record |
| Acoustic application / voice | **Voice Studio** | Configure capture/playback, VAD, STT, TTS, providers, voice presets, audio test/budget, emit source-authored lifecycle evidence | Own Canonical RGD life/relations; invent Chronons; issue motor actions; directly control LED BLE; clone PERIPH broker |
| Observation/evidence | **Reality Ingress** (future audio consumer) | Receive consented audio stream references or bounded frames from the one capture owner; provenance, immutable observation envelope, temporal quality, optional reviewed retention | Independently open the same mic as Voice; infer SELF; automatically persist ambient speech; claim Chronograf anchors without actual evidence |
| Display / LEDs | **Visual Feedback** | Subscribe to real Voice lifecycle events; LED policies, animations, BLE ownership and device status | Capture microphones; synthesize audio; control cognitive model; block voice on BLE failure |
| Cognition (outside this chat/repo) | **Separate Cognitive Runtime** | Interpret transcripts, consult RGD/Chronons and produce bounded responses | Directly own microphones, TTS provider credentials or LED service |

**Physical microphone opens:** transitional Voice Studio v0.3.1 runs `parecord` per request against PulseAudio. It is **not** a PERIPH-managed publisher. On a future managed runtime, exactly one authorized host capture service logically owns the selected microphone acquisition and fans out bounded audio frames/events; Voice and optional Reality Ingress become subscribers. PulseAudio may technically mix clients, but that is not a substitute for privacy or exclusive logical ownership.

**Audio output differs from sensor acquisition:** Voice owns conversation playback sequencing and media priority; a future peripheral status provider may report output device presence/health, but playback is not an `acquisition.start` action. A shared host audio output arbiter is proposed if multiple services need to speak.

## Existing PERIPH-0.1 contract applicability

| Field / semantic | Candidate mapping for BC-Master microphone | Current evidence |
| --- | --- | --- |
| `robot_id`, `peripheral_id` | `paky-01` + stable microphone inventory ID chosen on host verification | Stable ID **not yet registered** in PERIPH |
| `category` | `microphone` | Allowed by descriptor v0.1 |
| `binding.transport` | `usb_audio` | Allowed, but USB Audio device validator is **not implemented** in PERIPH-0.2 |
| `binding.binding_ref` | Opaque reference to reviewed host inventory; never transient `pactl` index, PulseAudio source name, `/dev` path from browser | Proposed only |
| `publisher.manager` | `external_read_only` **until** host-managed capture exists | Current Voice Lab is unmanaged from PERIPH |
| `publisher.ownership` | `externally_managed` until a reviewed host capture service is installed | No claim of manager control |
| `streams[].data_class` | `privacy_sensitive_media` for audio frames | Allowed candidate value; requires consent/privacy review |
| `streams[].state` | Independent `live/stale/no_source/invalid/unknown`; not equivalent to process `running` | Do not claim LIVE without observed frames and freshness |
| `controls` | **Disabled** (`can_start=false`, `can_stop=false`, `reason=unmanaged_owner` or `unsupported`) | No audio start/stop control is authorized by PERIPH-0.2 |

**Important schema gaps:** `peripheral-descriptor.v0.1-candidate` has **no** `audio_output` category and **no Bluetooth audio** transport. Bose should NOT be falsely registered as `microphone` or `generic` to bypass the schema. Propose a reviewed v0.2 extension for output devices and `bluetooth_audio` (plus an explicit split of input acquisition versus output playback authorization), instead of modifying the existing candidate or inventing a local competing Console schema.

PulseAudio source/sink names are *runtime endpoints*, not permanent physical identifiers. The proposed resolver binds a reviewed device ID to the current PipeWire/PulseAudio endpoint via verified host adapter; `default_source` or index is never proof of speaker or mic identity. BC-Master output is not used; Bose should remain in **A2DP** (the HFP quality/connection problems were physically observed). No automated A2DP→HFP switching in a mixed USB-mic/Bose configuration.

**Different rates:** a stream's `nominal_rate_hz` in the PERIPH descriptor is an observation/event rate (max 1000 Hz), not necessarily raw PCM sample frequency (e.g. 16000 Hz); that media-format metadata belongs to a separate audio stream format profile. Do not falsify nominal_rate_hz=16000.

## Two different status planes

1. **PERIPH host state**: hardware presence, verified binding, host publisher lifecycle, actual stream freshness, manager availability and `status_revision`.
2. **Voice application state**: audio endpoint selected/available, microphone capture armed, VAD detection, STT request, TTS generation, playback start/end, selected model, latency/errors, active voice profile.

`source present` != `capture active` != `utterance recognized` != `speaker audible`.

Until PERIPH audio is deployed, the Voice Studio dashboard should show **LOCAL / UNMANAGED** for device control. Its existing `GET /api/devices` remains a local PulseAudio discovery endpoint, **not** a synthesized `console.peripheral-status`. Never expose start/stop buttons for an unmanaged service or optimistic `running` after a subprocess launch.

## Voice → Visual: one existing bridge, two distinct studios

**Voice Studio** shows selectable audio devices, VAD/STT/TTS tests, voice profile, waveform/latency and read-only current voice lifecycle. **Visual Feedback Studio** (if built) belongs to `rgd-visual-feedback` and configures color/animation/hardware adapter; it must NOT be embedded as a second independent BLE controller inside Voice Studio. Console may later show a combined overview via explicit read-only contracts.

Use the **existing** draft Voice PR #2 / Visual PR #2 mapping (off-by-default; same-user Unix IPC):

- `listening.started` when actual microphone **capture/VAD listening** begins; merely detecting microphone presence or waking the server is insufficient.
- `inference.started` when actual STT/inference work begins. The LED's `thinking` is a presentation label; it does **not** assert a Cognitive Runtime exists.
- `playback.started` only when a local player is actually started, **not** when an ElevenLabs/Piper synthesis request starts. A player subprocess-start callback is not independent acoustic proof.
- `playback.ended` when the player completes; then `voice.ready`/idle or a fresh `listening.started` if VAD immediately rearms.
- `voice.error` only on a meaningful Voice I/O fault; a BLE disconnection is **not** a Voice failure.

The draft bridge currently covers `core.capture`, `core.transcribe`, `core.playback` (server-side `paplay`) only. **HTML `<audio>` browser playback and the external legacy `capture_phrase` VAD loop are not covered**; do not claim they light the LEDs. A follow-up after both PRs merge should instrument the real unified audio owner and avoid emitting misleading idle transitions between adjacent stages.

Concurrency must be defined before enabling full duplex: one logical microphone session, explicit playback arbitration (including generated prompts/alarms), no mic self-feedback; observable state is derived from authoritative active operations rather than the last user click. Visual events remain best effort, bounded, ordered, non-blocking. LED `accepted` is not physically verified illumination.

Do not conflate the sockets:
- PERIPH (future managed host service): `$XDG_RUNTIME_DIR/openrgd/peripherals.sock` — same-UID, manager allowlist; currently IMU pilot.
- Visual Feedback: `$XDG_RUNTIME_DIR/paky01-visual-feedback.sock` — existing BLE daemon.
- Voice local UI: `http://127.0.0.1:8765` — standalone localhost service, not an operator-authorized PERIPH control plane.

## Reality Ingress — opt-in, derived observations only

- Microphone and capture service do **not** belong to Reality Ingress; it is a **consumer of the authorized acquisition stream**, whenever such a stream exists.
- For a future consented recording, attach stable `robot_id`, `peripheral_id`, `publisher_id`, `stream_id`, capture session, start/end monotonic timing domain, source quality and evidence reference. Distinguish host receipt timestamps from Chronograf time anchors; never invent a Chronon.
- STT text is a **derived interpretation** linked to its source recording/observation ID (if one is retained), with provider/model/parameters and quality; it is not raw microphone truth.
- Default **no persistent ambient audio and no automatic upload**. VAD and local transcription can work with ephemeral buffers. External cloud STT must require explicit consent and a clear indicator.
- For the current Reality Ingress repository, implement this only after its canonical observation/runtime and privacy contracts are actually available; do not pull Voice capture code into the empty repo.

## Proposed UX and migration waves

1. **VOICE/PERIPH alignment (now):** this ADR + precise operator-facing LOCAL/UNMANAGED labels. No broker calls, no new device control permissions, no visual bridge duplication.
2. **PERIPH audio descriptor v0.2 (separate Console PR):** reviewed host probes for ALSA/USB and Bluetooth (status only), stable identity, output categories/transport, privacy-first permissions, no implicit start. Add schema/fixtures and mock contracts. Do not mutate PERIPH-0.2 IMU service allowlist to include audio.
3. **Host Audio Capture owner (separate release):** explicit operator consent, single capture/PCM fan-out, bounded queues, STOP with resource release, no automatic startup or recordings, physical on-device tests. Reality Ingress subscribes only if configured and authorized.
4. **Voice adapters:** select stable logical input/output ID and resolve to the actual current source/sink; implement fallback if available, never steal another publisher's device. Keep old v0.3.1 direct PulseAudio path as *manual laboratory mode* until commissioned.
5. **Voice/Visual PR #2 commissioning:** CI and protocol reviews, explicit daemon upgrade, verify events from true capture + playback and BLE fault injection. Voice Studio reports visual bridge `disabled/available/sending` separately from audio health.
6. **Console integration (later):** approved read-only device/status and operator auth for any lifecycle mutations; no direct host device socket in an unprivileged Docker container, no Caddy exposure of localhost Voice Lab without reviewed authentication.

## Acceptance tests required before activation

- BC-Master present/unplugged/replugged and PulseAudio names changed: logical binding never silently rebinds to the C920/another mic.
- Bose available/A2DP-disconnected/returned: no surprise HFP transition, playback timeout and source status accurately separate from capture.
- VAD idle, armed, voice detected, STT running, TTS generated but not playing, `paplay` started/ended, short-gap immediate rearm: lifecycle LED states follow **real** operations, not optimistic UI clicks.
- Concurrent speaker+mic and unrelated app capture: exclusive logical ownership, no unexpected termination of other processes, no Voice/Reality double-open; cloud fallback never overrides local privacy decision.
- PERIPH broker unavailable: status `unknown/unmanaged`, audio Lab continues in explicit local mode, **no** ghost start/stop success.
- LED daemon offline/behind API revision: Voice playback and STT unaffected. Duplicate/reordered event/session and old event after restart cannot regress actual state.
- Privacy: explicit consent, recording indicator, no automatic ambient recording or cloud upload, no full audio/text in logs; FFmpeg and model packages remain isolated from Canonical RGD and motor/CAN.
- Provider swap, voice default, price sync, model install and API key handling behave exactly as v0.3.1 (regression suite + actual Ubuntu audio smoke test).

## Gates

This ADR is a **proposal**, not a declaration of a physically commissioned PERIPH audio device or merged Voice→Visual integration. Keep both draft event-bridge PRs open until tests and approvals. Avoid changes to `rgd-console`, `rgd-reality-ingress`, Visual Feedback or the mini-PC hardware runtime from this branch.
