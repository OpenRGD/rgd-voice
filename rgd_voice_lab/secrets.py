"""Local-only credential persistence for Voice Lab.

Storage: ~/.config/openrgd/voice-lab/.env (0700 directory, 0600 file).
Values are never returned to HTTP clients, logged, expanded or committed.
The service is intentionally localhost-only and does not grant any robot authority.
"""
from __future__ import annotations

import os
import re
import stat
import tempfile
import threading
from pathlib import Path

CONFIG_DIR = Path(os.environ.get('RGD_VOICE_LAB_CONFIG', str(Path.home() / '.config/openrgd/voice-lab'))).expanduser()
ENV_PATH = CONFIG_DIR / '.env'
PROVIDERS = {'elevenlabs': 'ELEVENLABS_API_KEY', 'openai': 'OPENAI_API_KEY'}
LOCK = threading.RLock()
KEY_PATTERN = re.compile(r'^[A-Za-z0-9_./+=:@~-]{12,1024}$', re.ASCII)


def _ensure_private_directory():
    path = ENV_PATH.parent
    if path.is_symlink():
        raise RuntimeError('Directory credenziali non sicura (link simbolico)')
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise RuntimeError('Directory credenziali non sicura')
    st = path.stat()
    if hasattr(os, 'getuid') and st.st_uid != os.getuid():
        raise RuntimeError('Directory credenziali non appartiene all’utente')
    if stat.S_IMODE(st.st_mode) != 0o700:
        path.chmod(0o700)


def _read_env_file():
    if not ENV_PATH.exists() and not ENV_PATH.is_symlink():
        return {}
    if ENV_PATH.is_symlink():
        raise RuntimeError('File .env non sicuro: link simbolico')
    # O_NOFOLLOW prevents a symlink substitution between lstat and open.
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(ENV_PATH, flags)
    with os.fdopen(fd, 'r', encoding='utf-8') as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise RuntimeError('File .env non è un file regolare')
        if stat.S_IMODE(st.st_mode) & 0o077:
            raise RuntimeError('Permessi .env troppo permissivi: usare chmod 600')
        if hasattr(os, 'getuid') and st.st_uid != os.getuid():
            raise RuntimeError('File .env non appartiene all’utente')
        content = f.read(8192)
    values = {}
    for line in content.splitlines():
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        if k in PROVIDERS.values() and KEY_PATTERN.fullmatch(v):
            values[k] = v
    return values


def _write_env_file(values):
    _ensure_private_directory()
    filename = None
    try:
        fd, filename = tempfile.mkstemp(prefix='.env-', suffix='.tmp', dir=str(ENV_PATH.parent))
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write('# OpenRGD Voice Lab · local API credentials · DO NOT COMMIT\n')
            for k in sorted(PROVIDERS.values()):
                if k in values:
                    f.write(f'{k}={values[k]}\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(filename, ENV_PATH)
        ENV_PATH.chmod(0o600)
    finally:
        if filename is not None and os.path.exists(filename):
            os.unlink(filename)


def get_secret(provider):
    name = PROVIDERS.get(provider)
    if name is None:
        raise ValueError('Provider non supportato')
    with LOCK:
        saved = _read_env_file()
        return saved.get(name) or os.environ.get(name, '')


def status():
    with LOCK:
        saved = _read_env_file()
        return {provider: {
            'configured': bool(saved.get(name) or os.environ.get(name)),
            'source': 'local_env' if saved.get(name) else ('process_env' if os.environ.get(name) else None),
        } for provider, name in PROVIDERS.items()}


def set_secret(provider, value):
    if provider not in PROVIDERS:
        raise ValueError('Provider non supportato')
    if not isinstance(value, str) or not KEY_PATTERN.fullmatch(value):
        raise ValueError('Chiave non valida: servono 12–1024 caratteri ASCII senza spazi o simboli speciali')
    with LOCK:
        values = _read_env_file()
        values[PROVIDERS[provider]] = value
        _write_env_file(values)
        return {'saved': True, 'provider': provider, 'configured': True, 'source': 'local_env'}


def delete_secret(provider):
    if provider not in PROVIDERS:
        raise ValueError('Provider non supportato')
    with LOCK:
        values = _read_env_file()
        values.pop(PROVIDERS[provider], None)
        _write_env_file(values)
        return {'removed_from_local_env': True, 'provider': provider,
                'configured': bool(os.environ.get(PROVIDERS[provider])),
                'source': 'process_env' if os.environ.get(PROVIDERS[provider]) else None}
