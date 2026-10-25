"""Use the operator-selected Voice Lab output profile from other Paky-01 audio apps.

Does not load an RGD, call cognitive providers or touch actuators.
"""
from __future__ import annotations
import argparse
import sys
import tempfile
from pathlib import Path
from . import core, voice_profile


def main(argv=None):
    p = argparse.ArgumentParser(description='Paky-01 — speak using saved Voice Studio default')
    p.add_argument('--text', required=True, help='Testo da pronunciare (1–650 caratteri)')
    p.add_argument('--play', action='store_true', help='Riproduci attraverso lo speaker selezionato')
    p.add_argument('--allow-cloud', action='store_true', help='Autorizza invio esplicito testo a ElevenLabs/OpenAI')
    p.add_argument('--dry-run', action='store_true', help='Mostra il profilo senza generare audio')
    p.add_argument('--output', help='Salva WAV in questo percorso invece che in file temporaneo')
    args = p.parse_args(argv)
    profile = voice_profile.read()
    if not profile:
        p.error('Nessuna voce predefinita: apri Voice Studio e premi Salva per Paky-01')
    model, _ = voice_profile.validate(profile['model_id'], profile['voice_id'], profile['controls'])
    print(f'[VOICE PROFILE] robot=Paky-01 model={model["id"]} voice={profile["voice_id"]}')
    if args.dry_run:
        return 0
    if model['mode'] == 'cloud' and not args.allow_cloud:
        p.error('Provider cloud: aggiungi --allow-cloud per autorizzare il testo verso il servizio esterno')
    with tempfile.TemporaryDirectory(prefix='paky01_voice_studio_') as td:
        output = Path(args.output).expanduser() if args.output else Path(td) / 'speech.wav'
        result = core.synthesize(model['id'], args.text, profile['voice_id'], output, controls=profile['controls'])
        print(f'[TTS] {result["seconds"]:.2f}s · {result["duration"]:.2f}s audio')
        if args.play:
            from .server import get_settings
            sink = core.select_endpoint('sinks', get_settings()['sink'])
            core.playback(output, sink)
            print('[OUTPUT] Riproduzione completata')
        if args.output:
            print(f'[WAV] {output}')
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print(f'[VOICE ERROR] {e}', file=sys.stderr)
        sys.exit(1)
