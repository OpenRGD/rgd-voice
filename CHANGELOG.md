# 0.3.1 — Cost & Usage / Eleven v4 Turbo (2026-10-25)

Vedi [CHANGELOG_v0.3.1.md](CHANGELOG_v0.3.1.md) per dettagli, limiti e provenienza prezzi.

# OpenRGD Voice Lab — Changelog

## 0.3.0 — Voice Studio / Paky-01 default voice (2026-10-25)

- New **Regia vocale** controls inside the existing Laboratory: native ElevenLabs stability and similarity; style on supported older ElevenLabs models; explicit v4 emotion/delivery tags; provider-agnostic local WAV speed (0.80–1.20×) and pitch (±3 semitones) processing through optional FFmpeg.
- On Eleven v4, **Studio controls use the official Text to Speech API** (rather than v0.2.1's default Text to Dialogue call) to send the supported `voice_settings` values. The existing no-controls v4 call remains backward compatible with v0.2.1.
- `Set as Paky-01 default` persists an operator-selected, audio-only profile in `~/.local/share/openrgd/voice-lab/profiles/paky-01-voice-output.json`, preserving saved models, WAVs, and API keys across upgrade.
- New `GET/POST /api/voice-default` contract, guarded POST with explicit local setup header/confirmation, profile validation and private local file permissions.
- `python -m rgd_voice_lab.say` reads and uses the selected profile; **cloud calls require --allow-cloud**.
- Optional `paky01_vad_studio_v01.py` uses the already verified VAD to speak back via the chosen default voice; no identity prompt, no cognition, no BHL/CAN.
- 39 offline Python test cases; legacy tests remain compatible.

## 0.2.1

- API key verification distinguishes permissions rather than treating read-only 403 as TTS failure.
- Eleven v4 Text to Dialogue voice lab support.

## 0.2

- Expanded curated Piper voice library, ElevenLabs v2 voices, secure local `.env` setup.
