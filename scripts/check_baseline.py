#!/usr/bin/env python3
"""Read-only comparison of the installed Paky-01 Voice source with this kit."""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
source = Path(os.environ.get('RGD_VOICE_SOURCE', str(Path.home() / 'openrgd/rgd-voice-lab-v0.3.1'))).expanduser()
manifest = repo / 'baseline/original-files.sha256'

if not source.is_dir():
    print(f'ERROR: baseline installata non trovata: {source}', file=sys.stderr)
    sys.exit(2)

failures=[]
for line in manifest.read_text(encoding='utf-8').splitlines():
    sha, rel = line.split('  ', 1)
    # Bootstrap changes the .gitignore intentionally; compare original
    # kit manifest and installed .gitignore from the upstream ZIP only
    # where it is still identical (never block on the hardened ignore).
    if rel == '.gitignore':
        continue
    a, b = source / rel, repo / rel
    if not a.is_file():
        failures.append(f'ASSENTE SOURCE: {rel}')
    elif hashlib.sha256(a.read_bytes()).hexdigest() != sha:
        failures.append(f'DIVERSO SOURCE: {rel}')
    if not b.is_file() or hashlib.sha256(b.read_bytes()).hexdigest() != sha:
        failures.append(f'DIVERSO KIT: {rel}')
if failures:
    print('STOP: baseline installata differisce dalla distribuzione candidata:')
    for row in failures: print('  '+row)
    print('Nessun file viene modificato. Recuperare e revisionare le differenze prima del push.')
    sys.exit(3)
print('OK: 23 file originali coincidono con la versione sul mini-PC; .gitignore pubblicato rafforzato.')
