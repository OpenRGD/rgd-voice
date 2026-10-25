#!/usr/bin/env python3
"""Refuse obvious secrets and runtime artifacts from the Git staging area."""
from __future__ import annotations
import re, subprocess, sys
from pathlib import PurePosixPath

files = subprocess.check_output(['git','diff','--cached','--name-only','-z']).decode().split('\0')
files = [x for x in files if x]
violations=[]
for name in files:
    path=PurePosixPath(name)
    base=path.name.lower()
    if base in ('.env','id_rsa','id_ed25519') or base.startswith('.env.') or path.suffix.lower() in ('.key','.pem','.p12','.pfx','.sqlite','.sqlite3','.db','.wav','.mp3','.flac','.ogg','.onnx','.pt','.bin'):
        violations.append(f'Path sensibile incluso: {name}')
    try:
        buf=subprocess.check_output(['git','show',f':{name}'])
    except subprocess.CalledProcessError:
        violations.append(f'File stage non leggibile: {name}')
        continue
    if (b'-----BEGIN ' + b'PRIVATE KEY-----') in buf or (b'-----BEGIN OPENSSH ' + b'PRIVATE KEY-----') in buf:
        violations.append(f'Chiave privata trovata in: {name}')
    # Conservative literal-key detection. False positives should be reviewed manually.
    if re.search(rb'(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{24,}',buf):
        violations.append(f'Possibile API key in: {name}')
if violations:
    print('BLOCCATO: file pericolosi nello staging:')
    for m in violations: print(' - '+m)
    sys.exit(5)
print(f'OK: staged {len(files)} file, nessun segreto evidente rilevato (non è una scansione esaustiva).')
