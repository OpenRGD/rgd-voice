# VOICE-0.3.1 — Baseline provenance / release boundary

- Origin: assistant-created `rgd-voice-lab-v0.3.1.zip` dated 2026-10-25.
- Archive SHA-256: `0a2c23e66a3f114a7daeeacac1fe331508b6765a6ecd9044d0115198d4249013`.
- Archive content: 24 source files (see `baseline/original-files.sha256`).
- The baseline was unpacked and 51/51 offline `unittest` checks passed
  on the assistant's environment (Python 3.13.5).
- This **does not** establish an Ubuntu hardware pass, a signed release,
  a Cloud provider integration pass or registry compatibility.
- The uploaded archive excludes `.env`, downloaded weights, WAV/MP3,
  local usage SQLite databases and other operator data.
- A publishing script compares original tracked files with the
  actually installed directory on Paky-01. It refuses to publish when
  copies disagree, to avoid silently replacing a newer working version.
- Python packaging metadata and the docs/contract/CI files in this
  bootstrap kit were added after the v0.3.1 zip; no runtime logic was modified.

Every formal release needs a GitHub commit SHA, tagged version, tests on the
actual target environment, dependency pinning and a signed/pinned provenance
policy before appearing in the OpenRGD module registry.
