# VOICE-0.3.2 candidate — Voice Studio → Visual Feedback

**Status:** implementation on a draft PR, not deployed on Paky-01. No change to the saved output profile, credentials, audio devices, provider calls, prices, CAN, BHL or Somatic Runtime.

This integration sends optional **explicit audio lifecycle events** to Visual Feedback's existing host-user Unix socket. It does not create a BLE connection, open a new API endpoint or connect a container to host hardware.

## Exact working scope

| Voice Studio source | Voice evidence kind | Visual event | Requested LED state |
| --- | --- | --- | --- |
| `core.capture` after `parecord` process starts | `capture_started` | `listening.started` | blue / `listening` |
| `core.capture` successfully completes, no STT yet | `ready_idle` | `voice.ready` | white / `idle` |
| `core.transcribe` after validation, before STT computation | `capture_stopped_processing` | `inference.started` | purple / `thinking` |
| `core.transcribe` completes | `ready_idle` | `voice.ready` | white / `idle` |
| `core.playback` after `paplay` subprocess begins running | `playback_started` | `playback.started` | green / `speaking` |
| `core.playback` process completes successfully | `playback_stopped` | `playback.ended` | white / `idle` |
| Confirmed capture/transcription/playback failure | `service_error` | `voice.error` | red / `error` |

The `paplay` process-start callback is the best available start evidence in this v0.3.1 laboratory; it does **not** independently measure audible PCM from the physical Bose speaker. `accepted` from Visual also does not prove light emission. These are local UI status reports, not Chronons.

**Important UX distinction:** Voice Studio has both native HTML `<audio>` controls and a **"▶ Bose" / "▶ Ascolta sulla Bose"** button. This first integration follows only *server-side* `/api/play` / `core.playback` and the `rgd-voice-say --play` CLI. Browser HTML audio playback has a different lifecycle and is not instrumented here; do not claim an LED reaction for it.

The legacy external VAD bridge in `paky01_vad_studio_v01.py` calls `core.playback` but uses an external `capture_phrase`. Its playback follows this integration; its VAD capture/Whisper lifecycle is **not** claimed to be wired up.

## Enabling it (later, on Paky-01)

The feature is **OFF by default**. Launching Voice Studio as usual, without the flag, leaves audio behavior unaffected:

```bash
rgd-voice-lab
```

After Visual Feedback PR #2 is merged and the running Visual daemon is explicitly updated to support `voice_event`, and after this Voice PR is reviewed/merged and its code is checked out on Paky-01:

```bash
# In the same terminal where the intended official Voice Studio checkout is launched:
export RGD_VOICE_VISUAL_FEEDBACK=1
rgd-voice-lab
```

Or use the Voice repository's installed Python environment for `python -m rgd_voice_lab.server`. Never set credentials, default-voice profile contents or arbitrary endpoints in this flag. Stop the old localhost Voice Lab first so that two instances do not compete for port 8765.

Set `RGD_VOICE_VISUAL_FEEDBACK=0`, then restart the **Voice process only**, to roll back the optional events. The LED service is independent.

## Non-blocking and failure behavior

- One daemonized sender thread per Voice process; no socket operations on the Voice capture, STT or playback thread.
- Fixed six-field+command candidate message matches `OpenRGD/rgd-visual-feedback` PR #2: `openrgd.visual.voice-event.v0.1-candidate`, one 32-hex producer session, increasing sequence.
- Queue is bounded (12 events), ordered, drops oldest on overflow and drops events older than 1.5 seconds rather than replaying obsolete states.
- Socket connect/read operations each have a short timeout (0.25 s).
- No automatic enabling or installing Visual Feedback; absent/older LED daemon causes silent best-effort loss, not a Voice failure.
- `RGD_VOICE_VISUAL_FEEDBACK=1` affects only this process, never Voice providers, saved defaults or Git configuration.
- Currently the Audio Lab assumes serialized operator usage. Multiple simultaneous audio requests are **not** yet arbitrated at this stage; the next runtime contract should choose explicit priority and idle resolution before supporting concurrent listening/playback.

## Local verification gates

1. Run `python3 -m unittest discover -s tests -v` and check the new Visual bridge regressions.
2. With feature OFF, test native Voice Studio mic, transcription, Bose output and `rgd-voice-say` against the baseline v0.3.1 without cloud charges.
3. Confirm updated Visual daemon `paky-led status` and `voice_event` support.
4. With feature ON, use **▶ Bose** playback; expect speaking during `paplay`, idle when it finishes.
5. Simulate Visual daemon disconnection; Voice must still finish playback normally.
6. Check 2-30 sec recording: blue during actual `parecord` process; purple during STT; no speaker claim from a generated TTS WAV alone.
7. Do not merge as a production Voice Agent or registry-installable module until the full host lifecycle/permission contract has been reviewed.

This is a service-local candidate integration. The generic Voice lifecycle contract at `contracts/voice-lifecycle.v0.1.schema.json` is unchanged; `rgd_voice_lab.visual_feedback` maps its evidence names onto the existing Visual IPC candidate and does not invent a new canonical OpenRGD domain.
