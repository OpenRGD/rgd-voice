# VOICE-0.4D — Paky-01 VAD ↔ Visual Feedback: real event canary

Status: CODE CANDIDATE / NOT PHYSICALLY TESTED. No PERIPH, cognition, motor, CAN, Canonical RGD, secret or host unit changes.

## Complementary PRs

- Voice PR #2: https://github.com/OpenRGD/rgd-voice/pull/2 — Voice core audio callbacks, VAD PCM observer, Unix relay.
- Visual Feedback PR #2: https://github.com/OpenRGD/rgd-visual-feedback/pull/2 — accepts voice_event through existing local Unix socket.
- Voice PR #4: https://github.com/OpenRGD/rgd-voice/pull/4 — browser HTML audio Play/Pause bridge, stacked on Voice #2.
- Visual PR #3: https://github.com/OpenRGD/rgd-visual-feedback/pull/3 — separate preview-only Visual Effects Studio.

Installed Visual Feedback v0.2 does NOT support voice_event. Voice's flag alone cannot make v0.2 LEDs react; v0.3 candidate must be tested explicitly.

## Observable lifecycle (real, not guessed)

- First nonempty PCM frame returned from the commissioned legacy parec VAD reader => listening.started => desired listening. Presence or the start button is NOT evidence of capture.
- VAD finishes a valid phrase and actual Whisper transcription begins => inference.started => desired thinking.
- Whisper ends, TTS generation begins => NO idle or speaking event: retain thinking.
- Paplay starts in Voice core => playback.started => speaking; paplay returns successfully => playback.ended => idle.
- No speech/timeout after PCM => voice.ready => idle. Real audio fault => voice.error => error.
- Native browser HTML playing/stopped is in the separate stacked Voice PR #4. Browser event means browser media engine status, NOT a physical Bose signal.
- Visual status desired != applied. BLE applied means packet write, NOT optical observation; validate color with your eyes.

The legacy 0.1D capture algorithm, CLI device resolver and parec command are unchanged. The observer temporarily replaces ONLY its imported Python read_exact reference, serialized for one foreground VAD loop and restored on exit. Disabled by default (RGD_VOICE_VISUAL_FEEDBACK=0). This is a transitional LAB adapter, not multi-producer-ready acquisition management; remove after a future PERIPH-approved PCM callback.

## Stage from reviewed branches without touching the active runtime

    set -e
    mkdir -p ~/openrgd/worktrees
    cd ~/openrgd/repos/rgd-voice
    git fetch origin
    git worktree add --detach ~/openrgd/worktrees/rgd-voice-visual04d origin/feat/visual-feedback-lifecycle-v0.3.2
    VOICE="$HOME/openrgd/worktrees/rgd-voice-visual04d"
    PY="$HOME/openrgd/rgd-voice-paky01-smoke-v0.1/.venv/bin/python"
    (cd "$VOICE" && "$PY" -m unittest discover -s tests -v)

Stage Visual in a separate checkout:

    set -e
    REPO="$HOME/openrgd/repos/rgd-visual-feedback"
    if [ ! -d "$REPO/.git" ]; then
        test ! -e "$REPO" || { echo "Review existing Visual directory"; exit 1; }
        gh repo clone OpenRGD/rgd-visual-feedback "$REPO"
    fi
    cd "$REPO"
    git fetch origin
    git worktree add --detach ~/openrgd/worktrees/rgd-visual-voiceipc03 origin/feat/voice-lifecycle-ipc-v0.3
    VISUAL="$HOME/openrgd/worktrees/rgd-visual-voiceipc03"
    VISUAL_PY="$HOME/openrgd/repos/paky01-visual-feedback/.venv/bin/python"
    test -x "$VISUAL_PY" || { echo "Read systemctl --user cat and set correct venv before stop"; exit 1; }
    PYTHONPATH="$VISUAL/src" "$VISUAL_PY" -m unittest discover -s "$VISUAL/tests" -v

Preflight, READ ONLY:

    systemctl --user cat paky01-visual-feedback.service
    systemctl --user is-active paky01-visual-feedback.service
    paky-led status
    test -f ~/.config/paky-visual-feedback/config.json

## Optional deliberate physical canary, only at an operator-chosen maintenance window

Do NOT run two BLE owners. The candidate foreground server needs exclusive control of the existing Visual socket/BLE. It changes no unit/config files and automatically restores the original unit on exit.

TERMINAL A — swap v0.2 service only while this foreground command runs:

    (
      set -e
      trap 'systemctl --user start paky01-visual-feedback.service' EXIT
      systemctl --user stop paky01-visual-feedback.service
      VISUAL="$HOME/openrgd/worktrees/rgd-visual-voiceipc03"
      VISUAL_PY="$HOME/openrgd/repos/paky01-visual-feedback/.venv/bin/python"
      PYTHONPATH="$VISUAL/src" "$VISUAL_PY" -m rgd_visual.server \
          --config "$HOME/.config/paky-visual-feedback/config.json"
    )

TERMINAL B — poll accepted Voice event, desired/applied and BLE status (READ ONLY):

    VOICE="$HOME/openrgd/worktrees/rgd-voice-visual04d"
    PY="$HOME/openrgd/rgd-voice-paky01-smoke-v0.1/.venv/bin/python"
    "$PY" "$VOICE/scripts/voice_visual_monitor.py" --seconds 90 --interval 0.1

TERMINAL C — three local Voice VAD/STT/TTS echo turns:

    VOICE="$HOME/openrgd/worktrees/rgd-voice-visual04d"
    PY="$HOME/openrgd/rgd-voice-paky01-smoke-v0.1/.venv/bin/python"
    cd "$VOICE"
    "$PY" paky01_vad_studio_v01.py --check
    RGD_VOICE_VISUAL_FEEDBACK=1 "$PY" paky01_vad_studio_v01.py --turns 3 --whisper base --debug-vad

If the saved voice profile is ElevenLabs/OpenAI, append --allow-cloud ONLY after operator consent (cloud calls send text to provider and cost credits). Alternatively choose an already-installed local Piper voice in the UI before the test.

Expect after intro greeting: playback.started, playback.ended, listening.started, inference.started, playback.started, playback.ended; verify desired/applied and actual light with eyes. Silence/retry and BLE disconnect must not prevent speech completion.

Browser HTML Play/Pause canary is in stacked Voice PR #4 and is done SEPARATELY, after VAD exits. Do not run concurrent VAD and Voice server visual senders: Visual v0.3 has only one active producer session gate; another producer will retire the old session.

Press Ctrl+C in Terminal A to restore v0.2. Verify:

    systemctl --user is-active paky01-visual-feedback.service
    paky-led status

Do NOT merge or declare the real LEDs proven until Ubuntu hardware results (BC-Master capture, Bose playback, Visual daemon IPC/applied status, actual light observation, rollback) are recorded.
