# OpenRGD Voice Lab v0.3.1 — Cost & Usage + Eleven v4 Turbo

## New (Voice I/O only)

- Eleven v4 Turbo (`el:eleven_v4_turbo`) via **Text-to-Dialogue WebSocket**, with explicit operator authorization for each cloud synthesis request.
- Turbo needs `websockets` and `ffmpeg` installed in the existing Voice Lab Python environment. No model weights are downloaded or installed.
- WebSocket generates base64 audio chunks; v0.3.1 accumulates a bounded MP3, then converts to WAV for the existing Bose/browser playback. This **does not yet stream audio live to PulseAudio**. The API result includes measured *first audio chunk* latency.
- `Costi & utilizzo` dashboard: model metadata sync (`GET /v1/models`), subscription read if API key permits (`GET /v1/user/subscription`), curated public USD product-family reference tariffs, unknown-rate indicators, operator overrides, forecasts, and local usage ledger.
- Tracks only successful Lab/CLI STT/TTS calls occurring after upgrade; no audio, transcripts, text, key, or personal profile stored in the economic ledger.

## Pricing accuracy rules

- `/v1/models` exposes model metadata, including `model_rates` and `token_cost_factor` where available. It **does not provide an unambiguous USD price per model**.
- Versioned reference prices, as read on 2026-10-25 from <https://elevenlabs.io/pricing/api>:
  - Flash/Turbo published product family: USD $0.05 / 1,000 chars. Applied here only to `eleven_flash_v2_5`.
  - Multilingual v2: USD $0.10 / 1,000 chars.
  - Scribe v2: USD $0.22 / audio hour.
- **Eleven v4 and v4 Turbo lack an independently verified API USD tariff in the public price matrix consulted:** displayed as unconfirmed until a contract/plan-specific rate is explicitly entered by the local operator. Do not assume Turbo v4 is the old Turbo price.
- Prices are indicative, exclude subscription/overage terms, discounts, taxes, voice-level multipliers, and usage from other accounts/applications.
- No automatic scraping or invented price: "Sincronizza API" updates availability, documented multiplier metadata and limited subscription usage *when authorized*. Public USD reference prices remain pinned until a reviewed release or an operator change.
- Missing prices are stored as NULL, **not $0**. Dashboard totals explicitly flag unpriced requests.
- Forecast uses an **assumed** characters-per-voiced-minute (default 900); local usage also measures actual generated seconds. Speaking time is not the same as listening/uptime time.

## Security and boundaries

- Sync and overrides require localhost+same-origin controls, JSON and `X-Voice-Lab-Setup: local`; local changes require explicit confirmation.
- Key remains in `~/.config/openrgd/voice-lab/.env` with mode `0600`, never in UI/SQLite/metadata snapshots.
- Snapshot and overrides use 0600 JSON files under `~/.local/share/openrgd/voice-lab/pricing/` (dir 0700); usage in 0600 SQLite.
- No changes to BHL, CAN, motion control, Core RGD, Chronons, or robot cognitive identity.

## Verification

- `python -m unittest discover -s tests -v` — **51 tests** passed offline.
- `node --check rgd_voice_lab/static/app.js` — passed.
- Dashboard rendered and checked in a mocked Chromium browser, zero JS errors.
- v4 Turbo *real cloud account/streaming and Bose playback still require a mini-PC live test.*
