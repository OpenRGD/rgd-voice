# VOICE↔RI-01 — Consent-limited audio metadata bridge (candidate)

**25 November 2026. Draft, non-normative, non-actuating, not deployed on Paky-01.**

Grounding: [RI → Voice handoff](https://github.com/OpenRGD/rgd-reality-ingress/blob/feat/ri-integration-01-pipeline-contract/docs/handoffs/RI-TO-VOICE.md), [RI integration pipeline](https://github.com/OpenRGD/rgd-reality-ingress/blob/feat/ri-integration-01-pipeline-contract/docs/RI-INTEGRATION-01-PIPELINE.md), and [RI consumer ref schema](https://github.com/OpenRGD/rgd-reality-ingress/blob/feat/ri-integration-01-pipeline-contract/contracts/consumer-observation-ref.v0.1-candidate.schema.json) on Reality Ingress Draft PR #4.

## Ownership decisions

- **Voice** (current Paky-01 laboratory) owns local PulseAudio/parecord acquisition, VAD, ASR/Whisper, TTS, voice profile and playback. The future **PERIPH** host acquisition owner has not yet delivered reviewed audio capture access. Current audio is LOCAL/UNMANAGED from PERIPH.
- **RI** is an OPTIONAL evidence consumer, not another microphone owner, VAD/STT provider or mandatory pipeline stage. RI v0.4 has been merged to main; PR #4 remains Draft and candidate.
- **Visual Feedback** independently owns BLE and display of source-authored Voice lifecycle. `playback.started` means a player report, not acoustic sound detected. Voice→Visual IPC remains unchanged.
- **Cognition** owns semantic interpretation and reasoning; **Chronograf/History** own time qualification/history commit; Somatic controls remain strictly off-limits.

## Incompatibilities to avoid

1. RI `ri.consumer-observation-ref.v0.1-candidate` applies only to an **actually recorded** legacy v0.4 ObservationEnvelope with original `observation_id` and payload hash. **A transient Voice WAV is not an RI ObservationRef**. No Voice-side fabricated `obs_...`, false `verified_local_hash` or claim of history commit.
2. Existing legacy Voice VAD uses a scoped `read_exact` observer and local temporary WAV; don't add a second physical microphone reader or feed a slow RI callback into `read_exact`. Current operator flows discard WAVs when temporary session ends.
3. An ASR transcript is **derived** from input audio, not physical waveform evidence. A source ref can be attached only after RI actually stores/verifies the source observation; before that, use producer session ID and segment sequence with explicitly transient status. Never mix TTS/playback/Visual lifecycle reports into microphone observations.
4. RI's generic `source-sample:v0` JSONL and consumer-observation-ref contracts do not yet specify a consented raw audio transport or media retrieval capability. The following Voice report is deliberately a DIFFERENT candidate schema, not an RI ingest message.

## Minimal adapter in this separate Voice PR

- New `rgd_voice_lab/ri_segment_metadata.py` exposes a **consent-windowed, metadata-only** `LocalSegmentMetadataFanout`. Construction requires an explicit local consent object and in-process callback, not a network URL. Max consent window 120s. A bounded `Queue(maxsize=4)` sends only references to already-closed local WAV files to a worker; `submit_closed_wav` never reads media bytes and returns immediately. The worker reads **RIFF/WAV header metadata** only, not sample contents. Failures/expiry/queue saturation increment drop counters; never block VAD or retry.
- New `contracts/voice-audio-segment-closed-report.v0.1-candidate.schema.json` defines source hint (unverified), producer session ID and per-session segment sequence, PCM sample rate/channel/sample-width/frame count, **host segment-close** monotonic timestamp (NOT sensor/latch time), optional host boot label, consent purpose/expiry, and explicit non-authority flags. No audio bytes, transcript, raw path, RI observation ID/hash, Chronograf anchor, canonical memory or action permissions.
- `vad_lifecycle.capture_phrase_observed(..., ri_metadata_fanout=None)` schedules an optional metadata report only **after** successful closed WAV capture and outside the temporary legacy read patch/lock. It does not pass this argument to legacy VAD. Existing callers pass no fanout and run unchanged, even when Visual is active.
- No new microphone subprocess, background service, local network socket, CLI switch, browser UI, cloud integration, external audio owner or database is installed. The callback exists only as an explicit **unconnected** extension point. It does not make unapproved operator consent by itself.

### Sample local-only contract exercise

```python
from rgd_voice_lab import ri_segment_metadata as bridge
# The owning local application must have independently obtained consent first:
consent = bridge.ConsentWindow.for_explicit_local_session(
    purpose="operator_local_contract_pilot", ttl_seconds=30)
received = []
relay = bridge.LocalSegmentMetadataFanout(
    consent=consent, source_id="voice.local.unverified.bc-master",
    consumer=received.append)
relay.submit_closed_wav(existing_already_closed_wav)
relay.drain_for_test()  # TEST-ONLY helper, not for live audio loop
print(relay.status())   # counts only
```

This illustrative code does **not** authorize recording or remote transfer. A future UI/agent must verify the operator's grant, source/receiver rights and retention policy before constructing the local consent object.

## Tests / release gates

Synthetic offline tests assert no duplicate microphone open, no change to normal Voice/Visual behavior, no submission on timeout/error, source metadata exactness, invalid/expired consent, full queue/drop behavior, RI disconnected/slow and media-file expiry. Optional JSON Schema validation checks absence of fabricated authority.

Future genuine Voice→RI media ingestion requires separate reviews:
1. PERIPH audio owner design: single authorized input publisher and explicit read-only subscriptions, stable source identity, stream freshness and PCM format.
2. Explicit, audited operator consent to raw audio, bounded purpose/retention/expiry, local access control and zero-default ambient archival.
3. RI-owned **local** ingest protocol, authenticated receiver/media handling, bounded bytes and separate evidence store. **Only RI** records/hashes evidence and issues an actual read-only ObservationRef.
4. Optional derived ASR/Whisper record with processor/model/config identity and true RI input ObservationRef; if no persisted evidence, use only transient Voice producer key, not an RI observation ID.
5. Verified drop semantics under receiver disconnect, privacy denial, expired consent, missing files and power failure, without blocking VAD/STT/TTS, Visual, Somatic or motion controls.

## Decision for the RI team

**Agree:** local consent-limited metadata-only closed-segment report as a safe candidate seam. **Defer:** actual audio/PCM transport, storage, observational ID/crypto proof and transcript binding until the above gates. **Reject:** hardware duplicate capture, automatic remote streaming, fake Chronograf/History binding and rewriting the verified Voice→Visual lifecycle.

References: [Voice PR #3 ownership ADR](https://github.com/OpenRGD/rgd-voice/pull/3), [Voice PR #7 progressive playback Draft](https://github.com/OpenRGD/rgd-voice/pull/7), [RI PR #4 candidate](https://github.com/OpenRGD/rgd-reality-ingress/pull/4). This adapter PR stays **Draft, not merged**.
