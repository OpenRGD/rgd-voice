#!/usr/bin/env python3
"""Read-only Voice→Visual Feedback commissioning monitor.

Queries only the existing same-UID Unix socket 'status' command. No audio,
LED commands, credentials, motor/CAN, recordings or outbound network traffic.
A reported BLE 'applied' means a write was acknowledged, not optical proof.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import stat
import time


def socket_path() -> Path:
    runtime = os.environ.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'
    return Path(runtime) / 'paky01-visual-feedback.sock'


def read_status(path: Path) -> dict:
    meta = path.lstat()
    if not stat.S_ISSOCK(meta.st_mode) or meta.st_uid != os.getuid():
        raise RuntimeError('Visual socket missing or not owned by this user')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(.5)
        conn.connect(str(path))
        conn.sendall(b'{"version":1,"command":"status"}\n')
        data = b''
        while b'\n' not in data and len(data) < 4096:
            chunk = conn.recv(1024)
            if not chunk:
                break
            data += chunk
    if not data.endswith(b'\n') or len(data) > 4096:
        raise RuntimeError('Visual status response truncated or too large')
    result = json.loads(data)
    if not isinstance(result, dict) or result.get('ok') is not True:
        raise RuntimeError('Visual status unavailable')
    return result


def snapshot(status: dict) -> tuple:
    event = status.get('voice_event')
    if not isinstance(event, dict):
        raise RuntimeError('Visual service has no v0.3 voice_event support')
    return (event.get('session_id'), event.get('last_sequence'), event.get('last_event'),
            status.get('desired'), status.get('applied'), status.get('connected'))


def monitor(seconds: float, interval: float, path: Path) -> int:
    end = time.monotonic() + seconds
    previous = None
    seen = 0
    print('[VOICE→VISUAL MONITOR] READ ONLY, no audio, no LED commands; verify color by eye', flush=True)
    try:
        while time.monotonic() < end:
            state = snapshot(read_status(path))
            if state != previous:
                previous = state
                seen += 1
                session, seq, event, desired, applied, connected = state
                print(f'[EVENT {seen}] seq={seq!s} event={event or "none"} '
                      f'desired={desired or "none"} applied={applied or "none"} '
                      f'BLE={"connected" if connected else "disconnected"}', flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print('[MONITOR] stopped by operator', flush=True)
    except (OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f'[MONITOR ERROR] {exc}', flush=True)
        return 2
    print(f'[MONITOR] {seen} distinct status snapshots; physical LED is not attested', flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description='Read-only Voice→Visual lifecycle monitor')
    p.add_argument('--seconds', type=float, default=90)
    p.add_argument('--interval', type=float, default=.10)
    args = p.parse_args()
    if not 1 <= args.seconds <= 300 or not .05 <= args.interval <= 2:
        p.error('seconds 1..300, interval 0.05..2')
    return monitor(args.seconds, args.interval, socket_path())


if __name__ == '__main__':
    raise SystemExit(main())
