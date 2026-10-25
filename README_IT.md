> **Versione 0.3.1:** per Turbo e costo API vedi [README_UPGRADE_v0.3.1.md](README_UPGRADE_v0.3.1.md). Il resto del documento descrive la base v0.3.0.

# OpenRGD Voice Lab v0.3.0 — Voice Studio

**Local-first, non-attuatore.** Estende v0.2.1 mantenendo la dashboard e il catalogo. L'identità del robot e le relazioni non sono dati del modulo Voice; nessun RGD o Chronon viene modificato. Nessun accesso CAN, firmware, motori o autorizzazioni al movimento.

## Novità

Laboratorio → Text to Speech → **Regia vocale**:

- Per ElevenLabs v4: `stability` e `similarity_boost` (parametri nativi del provider) e tag espressivi selezionabili come «curiosa», «entusiasta», «sussurrata», «delicata», «preoccupata», «giocosa». Sono tag *best-effort*, non garantiscono un'emozione specifica.
- Per ElevenLabs v2: parametri nativi stabilità, somiglianza, stile. Sul provider v4 non è presente lo slider native `style`.
- Velocità 0.80–1.20× e pitch ±3 semitoni: **elaborazione locale del WAV tramite `ffmpeg`**. Non sono impostazioni native di Eleven v4 e possono introdurre artefatti. La qualità Piper e Eleven v4 potrebbe cambiare con post-processing. Se `ffmpeg` non è disponibile i controlli sono disabilitati; la sintesi standard continua a funzionare.
- Altri provider: regolazione locale velocità/pitch, ove disponibile.
- **Imposta su Paky-01**: salva modello, voce, controlli e provenienza operatore. Non invia testo/audio al provider e non modifica i preset del tuo account ElevenLabs.

### Differenza tra v4 Dialogue e v4 Voice Studio

La v0.2.1 generava v4 tramite `POST /v1/text-to-dialogue`. Eleven v4 permette anche `POST /v1/text-to-speech/{voice_id}` con `voice_settings`. La v0.3.0 usa questo secondo endpoint **quando usi il pannello Regia vocale** per applicare davvero stabilità/somiglianza. La vecchia chiamata senza `controls` resta invariata per compatibilità. La resa potrebbe differire leggermente da una precedente generazione con Dialogue.

## Aggiornamento sul mini-PC Paky-01 (Ubuntu 22.04)

Chiudi il server precedente con Ctrl+C. Il ZIP viene estratto in una cartella indipendente e non sovrascrive la vecchia versione. I modelli scaricati, i WAV, le chiavi ElevenLabs/OpenAI e le preferenze restano in directory condivise nel tuo home.

```bash
cd ~/openrgd
unzip -o ~/Downloads/rgd-voice-lab-v0.3.0.zip
cd ~/openrgd/rgd-voice-lab-v0.3.0

~/openrgd/rgd-voice-paky01-smoke-v0.1/.venv/bin/python -m unittest discover -s tests -v
~/openrgd/rgd-voice-paky01-smoke-v0.1/.venv/bin/python server.py
```

Apri Firefox su **http://127.0.0.1:8765** (solo localhost). Nella pagina Laboratorio scegli una voce, regola gli slider, premi **Genera la voce** per l'anteprima (le chiamate cloud richiedono conferma) e poi **Imposta su Paky-01**.

Per verificare FFmpeg senza cambiare Ubuntu:

```bash
command -v ffmpeg || echo 'FFmpeg assente (post-processing non disponibile)'
```

Non sono necessari aggiornamenti kernel/GPU o modifiche BHL. FFmpeg è opzionale; puoi omettere speed/pitch e utilizzare i controlli nativi v4 in ogni caso.

## Profilo salvato / API

Il preset audio viene salvato in:

```text
~/.local/share/openrgd/voice-lab/profiles/paky-01-voice-output.json
```

- `GET /api/voice-default` restituisce il profilo (nessuna chiave API).
- `POST /api/voice-default` salva il profilo con conferma e header locale `X-Voice-Lab-Setup: local`.
- Schema locale candidato `org.openrgd.voice.output-profile.v0.1`; `scope=audio_output_only`; istanza `paky-01`.
- Chiavi ElevenLabs/OpenAI restano separatamente in `~/.config/openrgd/voice-lab/.env` con permessi 0600 e non entrano nel profilo.

**Importante:** il vecchio script di conversazione `paky01_voice_vad_v02.py` NON legge automaticamente questo profilo. Selezionare la voce di default **non lo modifica**. Il nuovo adapter CLI/bridge qui sotto lo legge già e permette di verificarlo fisicamente sul mini-PC; l'integrazione nel Cognitive Runtime richiederà la successiva interfaccia Voice I/O.

## Test audio con profilo predefinito (nessun input cognitivo)

Per generare e ascoltare una frase con il preset scelto:

```bash
cd ~/openrgd/rgd-voice-lab-v0.3.0
PY=~/openrgd/rgd-voice-paky01-smoke-v0.1/.venv/bin/python
$PY -m rgd_voice_lab.say --text 'Ciao, sto usando la voce predefinita di Paky zero uno.' --play --dry-run
# Senza --dry-run, per il provider locale:
$PY -m rgd_voice_lab.say --text 'Ciao, sto usando la mia voce predefinita.' --play
# Per ElevenLabs o OpenAI devi autorizzare esplicitamente il cloud:
$PY -m rgd_voice_lab.say --text 'Ciao, sono la voce scelta per Paky zero uno.' --play --allow-cloud
```

Per registrare con VAD e riascoltare la trascrizione con la voce predefinita (**test ECHO, nessuna identità statica**):

```bash
$PY paky01_vad_studio_v01.py --turns 3 --debug-vad
# Se la voce predefinita è cloud:
$PY paky01_vad_studio_v01.py --turns 3 --debug-vad --allow-cloud
```

Il bridge riusa `paky01_voice_vad_v02.py` e `voice_smoke.py` già installati in `~/openrgd/rgd-voice-paky01-smoke-v0.1`, e mantiene BC-Master/Bose A2DP. Non modifica tali file e non comunica con BHL/CAN. La modalità ECHO ripete il testo riconosciuto; non è una prova di intelligenza cognitiva.

## Sicurezza e limiti

- Nessuna generazione cloud automatica in seguito al salvataggio del preset. I test REST richiedono `confirm_cloud=true`; le chiamate da CLI richiedono `--allow-cloud`.
- Localhost only: non pubblicare Voice Lab in LAN/Internet senza autenticazione. Non includere il file `.env` negli archivi.
- I controlli del Laboratorio non definiscono RGD identity, persona, Life/Relation o autorizzazione all'azione.
- Tutti i test automatici sono offline (nessuna spesa API) e non certificano qualità/pitch, latenza reale o connessione Bluetooth sul mini-PC. Collaudare lì.
