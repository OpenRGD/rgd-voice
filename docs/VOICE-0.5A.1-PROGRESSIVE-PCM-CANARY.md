# VOICE-0.5A.1 — Progressive Piper PCM / controlled canary

**Status: DRAFT, NOT MERGED, NOT INSTALLED.** Based on `OpenRGD/rgd-voice/main` commit `22fe6384`. Existing production-like half-duplex full-WAV + paplay workflow is unchanged unless operator explicitly passes `--progressive` to the detached VAD test script.

## What streaming means here

- Recent Piper Python API uses `PiperVoice.synthesize(text)` and yields `AudioChunk` values with sample rate, sample width, channel count, and `audio_int16_bytes`.
- An AudioChunk is normally **one sentence**. Piper does **not** generate word-by-word/token-by-token PCM in this API. A one-sentence utterance may show little improvement.
- The process-local resident Piper engine keeps one ONNX model and streams validated mono PCM16 chunks to a bounded Queue(maxsize=2). One consumer writes to `pacat --playback --raw --format=s16le --rate=RATE --channels=1 --device=SINK` using nonblocking pipe writes and finite stall timeouts.
- `playback_started` is emitted after first successful pipe write; `playback_stopped` after `pacat` completes drain. **No claim of real acoustic arrival** at Bose Bluetooth A2DP; Bluetooth has its own latency.
- Full WAV + paplay remains fallback **before** first PCM write on missing stream API/player, disabled residency or non-default speed/pitch. After **any** submitted PCM, failures terminate without repeating the phrase.
- `paky01_vad_studio_v01.py --progressive` is the ONLY opt-in in this wave; HTTP Voice Studio still uses the original endpoint and complete WAV. No global peripheral mode, auto-switch, microphone duplication, AEC, duplex, barge-in, CAN/motion or Cognition.

Source references: https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md ; https://github.com/OHF-Voice/piper1-gpl/blob/main/tests/test_piper.py ; https://manpages.ubuntu.com/manpages/stonking/man1/pacat.1.html .

## 0. Existing baseline and rollback invariants

- Keep the deployed Voice Studio process, Console containers (8088 / preview 8099, if still present), Visual BLE daemon, Bluetooth A2DP profile, PulseAudio default source and VAD configuration **unchanged**.
- Existing Paky-01 saved profile: `piper:it_IT-serena-high`, no cloud billing. Preset location: `~/.local/share/openrgd/voice-lab/profiles/paky-01-voice-output.json`.
- A separate detached worktree is all that is necessary. Do not `git checkout` an active worktree, edit a live systemd service or change `module.candidate.json` to installed.

## 1. Create separate experimental worktree

```bash
set -e
REPO="$HOME/openrgd/repos/rgd-voice"
WT="$HOME/openrgd/worktrees/rgd-voice-progressive05a1"
PY="$HOME/openrgd/rgd-voice-paky01-smoke-v0.1/.venv/bin/python"
git -C "$REPO" fetch origin
if [ -e "$WT" ]; then
  echo "Worktree già presente: $WT. Controllarla prima di aggiornare."
else
  git -C "$REPO" worktree add --detach "$WT" origin/feat/voice-0.5a1-progressive-pcm
fi
cd "$WT"
"$PY" -m unittest discover -s tests -q
```

## 2. Read-only API, sink and streamer preflight

```bash
cd ~/openrgd/worktrees/rgd-voice-progressive05a1
PY="$HOME/openrgd/rgd-voice-paky01-smoke-v0.1/.venv/bin/python"
"$PY" paky01_vad_studio_v01.py --check
command -v pacat || echo 'pacat ASSENTE: il pilota userà fallback WAV'
"$PY" - <<'PY'
import inspect, importlib.metadata
from piper import PiperVoice
try:
    print('piper-tts:', importlib.metadata.version('piper-tts'))
except importlib.metadata.PackageNotFoundError:
    print('piper-tts version: non determinabile')
print('PiperVoice.synthesize available:', callable(getattr(PiperVoice, 'synthesize', None)))
if callable(getattr(PiperVoice, 'synthesize', None)):
    print('Signature:', inspect.signature(PiperVoice.synthesize))
PY
```

STOP if the existing VAD `--check` cannot find input BC-Master or output Bose A2DP. No hardware is modified by this preflight.

## 3. Benchmark with no real audio playback

```bash
"$PY" -m rgd_voice_lab.benchmark_progressive_piper --scenario multi --repeat 3
"$PY" -m rgd_voice_lab.benchmark_progressive_piper --scenario single --repeat 3
```

Metrics: `first_pcm` = first block produced, `pcm_complete` = all chunks generated/consumed by a no-op callback, `full_wav` = complete full WAV ready. Piper was warmed once; no `pacat`, Bose, microphone or cloud call. Sample text is synthetic and WAVs are automatically deleted.

**Important:** the benchmark does not represent real time-to-first-audible-word. For single-sentence text, first PCM may appear nearly as late as the full WAV. For multi-sentence text, we want first PCM earlier than whole synthesis, with no unacceptable prosody/gaps.

## 4. Physical Visual + Bose controlled canary (two terminals)

Terminal A, optional status-only monitor:

```bash
PY="$HOME/openrgd/rgd-voice-paky01-smoke-v0.1/.venv/bin/python"
"$PY" ~/openrgd/worktrees/rgd-voice-progressive05a1/scripts/voice_visual_monitor.py --seconds 180 --interval 0.1
```

Terminal B, ONLY after offline tests and preflight:

```bash
cd ~/openrgd/worktrees/rgd-voice-progressive05a1
PY="$HOME/openrgd/rgd-voice-paky01-smoke-v0.1/.venv/bin/python"
RGD_VOICE_PIPER_RESIDENT=1 RGD_VOICE_VISUAL_FEEDBACK=1 \
  "$PY" paky01_vad_studio_v01.py --turns 3 --whisper base --debug-vad --progressive
```

Observe blue listening, purple thinking, green speaking and white idle (actual colors depend on Visual configuration). The command may report `fallback=full_wav` when the old Piper API or `pacat` is unavailable; this is **safe and not a successful streaming result**. Confirm no double speech, no audible gaps between sentences, and no Bluetooth disconnects. The `[STREAM]` metrics report when PCM is produced/written and when the player finishes, *not* speaker acoustics.

## 5. Abort / rollback / release gate

- Stop foreground test with Ctrl+C; the subprocess is reaped, no persistent config is changed. A terminal process crash should not replace the existing Visual daemon or change the Bose pairing.
- To return to the prior pathway, run the old command from the previously tested worktree **without `--progressive`**. No reinstall or Git rollback required.
- If Piper yields no chunks, if player fails before first PCM, or controls need full-WAV FFmpeg, fallback is allowed. A post-write failure reports an error and does not replay already heard/queued words.
- CI Python 3.10 + 3.12, local offline tests, hardware measurements and operator-led LED/Bose acceptance are REQUIRED before PR merge.
- Keep the candidate in Draft, no direct main push, no Console deploy, no activation of generic audio management until the separate PERIPH handoff.

## Known limitations

- Sentence granularity; some echo answers are just one sentence.
- Synthesizer may be slower than the current speaker output and produce gaps during subsequent sentences; benchmark and listening check required.
- `pacat` pipe-write timing is not a hardware audio-clock measurement. Bluetooth A2DP buffer delay and physical loudspeaker response remain unmeasured.
- This is not full duplex, does not listen while speaking, and does not yet provide true user barge-in.
