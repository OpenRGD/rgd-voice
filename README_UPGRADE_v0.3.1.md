# OpenRGD Voice Lab v0.3.1 — aggiornamento Ubuntu 22.04

## Installazione

Non cambia il kernel o l'ambiente BHL. Stop al server Voice Lab precedente (`Ctrl+C`), poi:

```bash
cd ~/openrgd
unzip -o ~/Downloads/rgd-voice-lab-v0.3.1.zip
cd ~/openrgd/rgd-voice-lab-v0.3.1
PY=~/openrgd/rgd-voice-paky01-smoke-v0.1/.venv/bin/python
$PY -m pip install 'websockets>=13,<17'
$PY -m unittest discover -s tests -v
$PY server.py
```

Apri Firefox su `http://127.0.0.1:8765`. Le preferenze salvate e le chiavi locali della v0.3.0 restano nello stesso data root.

### Primo test v4 Turbo

1. Apri **Laboratorio → TTS** e scegli **ElevenLabs · Eleven v4 Turbo · WebSocket**.
2. Scegli una voce ElevenLabs accessibile all'account, inserisci una frase, quindi *Genera* e conferma l'invio del testo al cloud.
3. Verifica la latenza `primo chunk` (non è ancora il momento dell'inizio della riproduzione): la dashboard raccoglie l'audio, converte MP3→WAV e solo allora consente di riprodurlo sulla Bose.
4. Se il modello non appare **PRONTO**, controlla presenza di `websockets` e `ffmpeg` con `$PY -m pip show websockets` e `command -v ffmpeg`. Non va installato come peso locale.
5. Se ElevenLabs risponde 403 o `model_unavailable`, verifica dalla dashboard provider ElevenLabs i permessi TTS e l'accesso al modello: il catalogo locale non garantisce l'abilitazione dell'account.

### Costi & utilizzo

1. Apri **Costi & utilizzo**: confronta tariffe di riferimento e tariffe non confermate.
2. Premi **Sincronizza API ElevenLabs**. Il backend usa la chiave già presente in `.env` per leggere modelli/fattori; prova a leggere il piano solo se il permesso lo consente. Non genera audio né scopre automaticamente un prezzo USD specifico per v4 Turbo.
3. Per v4 Turbo, premi **Inserisci tariffa** solo se conosci il prezzo esatto dall'offerta/contratto ElevenLabs. Verrà contrassegnata come tariffa inserita dall'operatore e potrai rimuoverla.
4. Nel simulatore scegli modello, minuti effettivi di parlato al giorno e caratteri per minuto (900 ipotesi iniziale). Leggi costo minuto, ora e 30 giorni; non confondere con un'ora di ascolto.
5. I test TTS/STT eseguiti dopo questo aggiornamento si registrano nel ledger economico locale. Richieste cloud con tariffa mancante sono etichettate **non quotate**, mai gratuite.

Percorsi persistenti:

```text
~/.config/openrgd/voice-lab/.env                       # Credenziali, chmod 600
~/.local/share/openrgd/voice-lab/profiles/             # Voce preferita Paky-01
~/.local/share/openrgd/voice-lab/pricing/              # Tariffe e contabilità
```

Se vuoi cancellare solo i dati economici di test, ferma il server prima di modificare il database. Non eliminare la cartella principale del laboratorio, che contiene anche i modelli e le preferenze vocali.

### Limiti intenzionali

- Disponibilità, catalogo e moltiplicatori si sincronizzano tramite API; **il prezzo USD del modello non è automaticamente sincronizzabile con affidabilità** dall'endpoint modelli.
- Prezzi dell'account, eccedenze e voci premium potrebbero differire dalle tariffe pubbliche e dalle stime locali.
- L'ora di conversazione reale dipende anche dalla porzione di tempo in cui il robot ascolta, dal modello STT, dall'inferenza cognitiva e dalla durata della risposta. Il simulator calcola soltanto la componente TTS.
- L'integrazione non modifica file RGD, identità del robot, Chronon o attuazione.
