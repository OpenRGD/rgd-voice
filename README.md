# OpenRGD Voice — source baseline 0.3.1

**Status: candidate, not yet registered or installable from OpenRGD Console.**
Provider-agnostic speech input/output laboratory: faster-whisper, Piper,
OpenAI, ElevenLabs, Voice Studio, local per-robot output profile, and
conservative usage accounting.

This is a source preservation/bootstrap package based on `rgd-voice-lab-v0.3.1.zip`
(SHA-256 recorded in `docs/BASELINE.md`), not a new tested deployment release.
There is no RGD identity, cognition, Chronon, motor, CAN or firmware authority.

## Paky-01 confirmed hardware / limits

On Paky-01 Ubuntu 22.04, previous operator testing confirmed BC-Master USB
capture, Bose SoundLink Bluetooth A2DP playback, local Whisper/Piper,
Serena Medium/High and ElevenLabs v4, and a VAD + Ollama conversation experiment.
WebSocket v4 Turbo is implemented as a preview, but has not been verified
on the actual provider account/hardware. The one-off VAD bridge still depends
on the prior `rgd-voice-paky01-smoke-v0.1` directory.

## Offline tests

```sh
python3 -m unittest discover -s tests -v
```

To use already installed providers on Paky-01, run with the known working
virtualenv instead of changing BHL's system Python:

```sh
PY="$HOME/openrgd/rgd-voice-paky01-smoke-v0.1/.venv/bin/python"
"$PY" -m unittest discover -s tests -v
"$PY" server.py
```

Default server binds on loopback only (`http://127.0.0.1:8765`).
Do not expose it to a LAN or publish it through the Console gateway before
reviewing authorization and CSRF protections in the target architecture.

The API keys are stored **outside this repository**, under
`~/.config/openrgd/voice-lab/.env`; operator-selected output profiles and
accounting live under `~/.local/share/openrgd/voice-lab/`.
Never copy these to GitHub. The `.env` file is plaintext, chmod 0600, not a vault.

See: `README_IT.md`, `README_UPGRADE_v0.3.1.md`,
`docs/CONSOLE_HANDOFF.md`, and `docs/BASELINE.md`.

## Installation into Console (future work)

`module.candidate.json` is a non-normative draft only. `rgd-modules`
currently accepts `pinned_git_cargo` modules and the v0.4A Console
Module Agent rejects privileges/device access. Voice requires a different
approved Python-service installer and a **host-side** audio agent.
Do not add `org.openrgd.voice` to the registry until those prerequisites and
conformance tests are satisfied. All changes to `rgd-console` go through a
branch and PR against its current `main`, never direct pushes.
