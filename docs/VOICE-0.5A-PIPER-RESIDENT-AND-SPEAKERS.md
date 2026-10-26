# VOICE-0.5A — Resident Piper, benchmark and speaker research

Status: CANDIDATE / Paky-01 CPU and physical acceptance pending. Stacked on Voice PR #2, no changes to PERIPH, Cognition, Visual Feedback or Canonical RGD.

## Evidence and objective

Real Paky-01 results from 2026-10-26, BC-Master input, Bose A2DP output, Whisper base + Piper Serena High:

- Turn 1: STT 2.85s, Piper 10.11s, playback 3.85s.
- Turn 2: STT 2.83s, Piper 6.01s, playback 4.59s.
- Total STT+Piper before playback 8.84–12.96s, **excluding** VAD end-of-turn detection and Bluetooth transport delay.

The previous core implementation ran PiperVoice.load(...) for every utterance. This is an avoidable per-turn model initialization overhead. Do not assume all 6–10s are spent in loading: benchmark on Paky-01 before claiming an improvement.

## Implementation contract

- Exactly one ONNX PiperVoice object per Python Voice process; lazy load on first use.
- Existing full PCM WAV output and all current provider routes are preserved; no audio streaming in this wave.
- Lock serializes Piper load and synthesis; model replacement evicts the previous object, never accumulate a catalog of large ONNX sessions.
- Path + file size/mtime_ns + JSON config size/mtime_ns identify the loaded model. Changes trigger reload, not silent reuse.
- Legacy VAD ECHO pilot explicitly prewarms Piper once before greeting (after local STT load); later requests reuse the warmed object.
- Process-local safe status from Voice health: resident_enabled, loaded, model_filename, load_count and synthesis_count. No raw model paths, audio, text, API keys or network calls.
- Easy rollback for experimental process only: environment RGD_VOICE_PIPER_RESIDENT=0 (re-loads for each request). No modifications to host PulseAudio devices or saved voice default.
- Two independent Python processes load two independent sessions. No global daemon or shared memory is claimed.

Piper API source: https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_PYTHON.md

## Real Paky-01 CPU benchmark — no speaker/microphone/cloud

Create a separate detached worktree from the reviewed branch; do not overwrite active Voice Studio files:

    set -e
    cd ~/openrgd/repos/rgd-voice
    git fetch origin
    git worktree add --detach \
      ~/openrgd/worktrees/rgd-voice-piper05a \
      origin/feat/voice-0.5a-piper-resident-benchmark
    cd ~/openrgd/worktrees/rgd-voice-piper05a
    PY="$HOME/openrgd/rgd-voice-paky01-smoke-v0.1/.venv/bin/python"
    "$PY" -m unittest discover -s tests -q

Compare the selected default saved Piper voice (Serena High) with identical built-in synthetic phrase:

    "$PY" -m rgd_voice_lab.benchmark_piper --repeat 5
    RGD_VOICE_PIPER_RESIDENT=0 "$PY" -m rgd_voice_lab.benchmark_piper --repeat 5

Or compare another installed local model without changing Paky-01 default:

    "$PY" -m rgd_voice_lab.benchmark_piper --model piper:it_IT-serena-medium --repeat 5 --json

Measures first total synthesis, load duration and median of later turns, produces WAV duration and real-time factor. Each run uses temporary local WAVs deleted on completion; no speaker playback, VAD, recordings, clouds, ledger charges or saved profile updates. Start a new Python process for each mode and use same phrase and approximate CPU temperature/load. Cached OS filesystem pages can speed repeated "cold" runs; do not call these power-on cold boot numbers. First-audio latency is NOT measured because playback still starts after full WAV creation.

Full real echo regression after benchmarks (does not add cognition):

    RGD_VOICE_VISUAL_FEEDBACK=1 "$PY" paky01_vad_studio_v01.py --turns 3 --whisper base --debug-vad

The preceding Visual lifecycle events and current BC-Master/Bose endpoints remain unchanged; the ECHO loop simply uses the saved default voice. No cloud authorization needed for the saved local Piper Serena High.

## Hardware shortlist (RESEARCH ONLY)

Requirement: new small speaker inside Paky-01, output via USB Audio Class (mini-PC onboard Intel audio/3.5mm not currently bound), preserve BC-Master USB input and Bose A2DP as a verified alternative. No device-level automatic switching or PERIPH changes.

1. Geekworm YB-MVEO3: USB2 sound card 66x18mm + separate 69.9x31x16.5mm 4-ohm/3W mono speaker, physical connector, output-only (no mic). Listed price US$15.90. Includes an audio reference header for **external** AEC hardware, NOT onboard echo cancellation. Excellent first mechanical pilot. Sources: https://geekworm.com/products/yb-mveo3 and https://wiki.geekworm.com/YB-MVEO3 .
2. Pimoroni PIM742 Picade Max USB Audio: RP2040 USB-C card, 3W stereo output, two screw terminal pairs, passive 4-ohm speaker(s) sold separately. Retail listing £13.75 (before applicable local tax/shipping). Good if symmetric stereo speakers are desired. Source: https://shop.pimoroni.com/products/picade-max-usb-audio .
3. Waveshare USB TO AUDIO (18833): USB2 audio codec with onboard mic and speaker headers, approx 2.6 W/channel into 4 ohms. Board retail in Italy ~€10, speakers separately. Do NOT silently select its onboard mic while BC-Master remains the commissioned input. Sources: https://www.waveshare.com/wiki/USB_TO_AUDIO and https://melopero.com/it/shop/accessories/usb-sound-card-driver-free-for-raspberry-pi-jetson-nano/ .
4. RASPIAUDIO Voice DSP+ XMOS XVF3800: listed €89.90; 4-mic array, real onboard AEC, USB audio and built-in/external speaker path. **Hold for VOICE-0.5B research after reviewed PERIPH audio-microphone handoff** (it changes mic acquisition ownership). Source: https://raspiaudio.com/product/voicedsp/ .

Keep the Bose Bluetooth A2DP output as fully supported: it remains both a tested fallback and a baseline against which to measure new USB output. Do not switch Bose to HFP (observed lower stability/quality). Do not modify the previous preferred sink or default source; use manual sink selection in the current Voice Lab until PERIPH provides identity, authorization and lifecycle rules.

## Mechanical and electrical acceptance

- Measure available chest/head space and design a compact printed mount/grille; confirm actual driver excursion and acoustic sealing. Avoid mechanically coupling speaker vibrations to BC-Master microphone or servo frame.
- Respect USB 5V host power limits and each manufacturer's speaker impedance/power. NEVER connect BHL motor-bus 24V to USB speaker or amplifier output pins. Verify powered USB budget and isolation/noise before enclosure installation.
- Compare same phrase at 0.5, 1.5 and 3m against Bose. Record intelligibility, max clean volume, echo leakage into BC-Master, CPU/speaker latency, power and USB unplug/replug behavior.
- Generic USB Audio Class may enumerate without a driver but must still be commissioned on Ubuntu 22.04. Absence/identity of outputs cannot be guessed from the order of pactl sinks.
- Wired USB output likely provides simpler render clock/latency control for future AEC than Bluetooth, but actual signal path and performance need physical tests; an AEC-reference header is not itself an echo canceller.
- PERIPH-0.2 management of audio devices remains FROZEN until its explicit handoff; purchase and hardware mounting are not part of this PR.

## Completion gate

- Python 3.10 and 3.12 CI green (resident cache, concurrency, model change, rollback flag, WAV backward compatibility, pure benchmark).
- CPU measurements repeated on Paky-01 with Serena High, both cache modes; do not invent a speedup.
- Three real VAD/Whisper/Piper/Bose turns with Visual Feedback still working.
- PR remains Draft until real host benchmark/response and LED non-regression are validated.
