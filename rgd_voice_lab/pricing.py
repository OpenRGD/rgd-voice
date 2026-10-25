"""Conservative local usage estimates for OpenRGD Voice Lab.

Rates are attributed references or operator overrides, never declared provider invoices.
A successful /v1/models sync supplies model metadata/factors, NOT contractual USD prices.
No transcripts, utterance text, WAV data, API keys or account IDs are persisted here.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import closing
import math
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

PUBLIC_PRICING_URL = 'https://elevenlabs.io/pricing/api'
MODELS_API_URL = 'https://api.elevenlabs.io/v1/models'
SUBSCRIPTION_API_URL = 'https://api.elevenlabs.io/v1/user/subscription'
PUBLIC_AS_OF = '2026-10-25'
# Published general ElevenAPI product family prices; v4 and v4 Turbo have no
# unambiguous standalone public USD API rate in the cited product table.
REFERENCE = {
    'el:eleven_flash_v2_5': {'usd_per_1000_chars': 0.05, 'rate_status': 'public_reference', 'source': PUBLIC_PRICING_URL},
    'el:eleven_multilingual_v2': {'usd_per_1000_chars': 0.10, 'rate_status': 'public_reference', 'source': PUBLIC_PRICING_URL},
    'el:eleven_v4': {'usd_per_1000_chars': None, 'rate_status': 'unconfirmed', 'source': PUBLIC_PRICING_URL},
    'el:eleven_v4_turbo': {'usd_per_1000_chars': None, 'rate_status': 'unconfirmed', 'source': PUBLIC_PRICING_URL},
    'el:scribe_v2': {'usd_per_audio_hour': 0.22, 'rate_status': 'public_reference', 'source': PUBLIC_PRICING_URL},
}
LOCK = threading.Lock()


def root(data_root):
    d = Path(data_root) / 'pricing'
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(d, 0o700)
    return d


def json_read(path, fallback=None):
    if not path.is_file():
        return fallback if fallback is not None else {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return fallback if fallback is not None else {}


def json_write(path, data):
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def now():
    return datetime.now(timezone.utc).isoformat()


def db_conn(data_root):
    dbpath = root(data_root) / 'usage.sqlite3'
    first = not dbpath.exists()
    conn = sqlite3.connect(str(dbpath), timeout=10)
    try:
        if first:
            os.chmod(dbpath, 0o600)
        conn.execute('''CREATE TABLE IF NOT EXISTS usage (
            id INTEGER PRIMARY KEY,
            timestamp_utc TEXT NOT NULL,
            provider TEXT NOT NULL,
            model_id TEXT NOT NULL,
            operation TEXT NOT NULL CHECK (operation IN ('tts','stt')),
            input_chars INTEGER NOT NULL DEFAULT 0,
            audio_seconds REAL NOT NULL DEFAULT 0,
            request_seconds REAL NOT NULL DEFAULT 0,
            estimated_usd REAL,
            rate_status TEXT NOT NULL
        )''')
        conn.execute('CREATE INDEX IF NOT EXISTS usage_by_timestamp ON usage(timestamp_utc)')
        conn.commit()
        return conn
    except Exception:
        conn.close()
        raise


def _money(value):
    if value is None:
        return None
    try:
        d = Decimal(str(value))
        if not d.is_finite() or d < Decimal('0.00001') or d > Decimal('1000'):
            raise ValueError('Tariffa USD per unità fuori limite (0.00001–1000)')
        return float(d)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError('Tariffa non numerica') from exc


def overrides(data_root):
    return json_read(root(data_root) / 'overrides.json', {})


def set_override(data_root, model_id, unit_price_usd, unit):
    """Explicit operator price; never synchronized as verified commercial price."""
    if model_id not in REFERENCE:
        raise ValueError('Modello non incluso nel listino Voice Lab')
    expected = 'usd_per_audio_hour' if model_id == 'el:scribe_v2' else 'usd_per_1000_chars'
    if unit != expected:
        raise ValueError('Unità di tariffazione incompatibile con il modello')
    amount = _money(unit_price_usd)
    with LOCK:
        items = overrides(data_root)
        items[model_id] = {unit: amount, 'rate_status': 'operator_override', 'updated_at_utc': now()}
        json_write(root(data_root) / 'overrides.json', items)
    return tariff(data_root, model_id)


def delete_override(data_root, model_id):
    if model_id not in REFERENCE:
        raise ValueError('Modello non incluso nel listino Voice Lab')
    with LOCK:
        items = overrides(data_root)
        items.pop(model_id, None)
        json_write(root(data_root) / 'overrides.json', items)
    return tariff(data_root, model_id)


def tariff(data_root, model_id):
    ref = REFERENCE.get(model_id)
    if ref is None:
        return {'model_id': model_id, 'usd_per_1000_chars': None, 'usd_per_audio_hour': None,
                'rate_status': 'unpriced', 'source': None}
    result = {'model_id': model_id, **ref, 'reference_as_of': PUBLIC_AS_OF}
    override = overrides(data_root).get(model_id)
    if override:
        result.update(override)
        result['source'] = 'operator_local'
        result['operator_supplied'] = True
    else:
        result['operator_supplied'] = False
    return result


def model_meta(data_root, model_id):
    snapshot = json_read(root(data_root) / 'models-snapshot.json', {})
    model = model_id.partition(':')[2]
    return next((x for x in snapshot.get('models', []) if x.get('model_id') == model), None)


def estimate(data_root, model_id, operation, chars=0, audio_seconds=0):
    rate = tariff(data_root, model_id)
    if operation == 'tts' and rate.get('usd_per_1000_chars') is not None:
        # Price is an indicative product-family tariff. Provider multipliers are
        # exposed as metadata, not implicitly applied to an unclear base price.
        return round(float(rate['usd_per_1000_chars']) * chars / 1000.0, 7), rate['rate_status']
    if operation == 'stt' and rate.get('usd_per_audio_hour') is not None:
        return round(float(rate['usd_per_audio_hour']) * audio_seconds / 3600.0, 7), rate['rate_status']
    return None, rate['rate_status']


def record(data_root, provider, model_id, operation, chars=0, audio_seconds=0, request_seconds=0):
    """Log only successful provider calls. Missing tariffs remain NULL, not $0."""
    if operation not in ('tts', 'stt'):
        raise ValueError('Operazione non supportata')
    if not (isinstance(chars, int) and 0 <= chars <= 100000):
        raise ValueError('Conteggio caratteri fuori intervallo')
    if not (0 <= audio_seconds <= 7200 and 0 <= request_seconds <= 7200):
        raise ValueError('Durata fuori intervallo')
    money, status = (0.0, 'local_no_api') if provider in ('Piper', 'faster-whisper') else estimate(data_root, model_id, operation, chars=chars, audio_seconds=audio_seconds)
    with LOCK:
        with closing(db_conn(data_root)) as conn, conn:
            conn.execute('''INSERT INTO usage(timestamp_utc,provider,model_id,operation,
                   input_chars,audio_seconds,request_seconds,estimated_usd,rate_status)
                   VALUES(?,?,?,?,?,?,?,?,?)''', (now(), provider, model_id, operation,
                                                chars, audio_seconds, request_seconds, money, status))
    return {'estimated_usd': money, 'rate_status': status}


def summary(data_root):
    # Current UTC month; clearly displayed as such in UI.
    month = datetime.now(timezone.utc).strftime('%Y-%m')
    with closing(db_conn(data_root)) as conn:
        rows = conn.execute('''SELECT model_id, operation, COUNT(*), SUM(input_chars), SUM(audio_seconds),
                     SUM(estimated_usd), SUM(CASE WHEN estimated_usd IS NULL THEN 1 ELSE 0 END)
                     FROM usage WHERE timestamp_utc >= ? AND timestamp_utc < ? GROUP BY model_id,operation''',
                     (month + '-01', _next_month(month) + '-01')).fetchall()
    result=[]
    for model, kind, count, chars, seconds, usd, unknown in rows:
        # Unknown priced requests are not treated as free. Sum may be partial.
        result.append({'model_id':model,'operation':kind,'requests':count,'chars':chars or 0,
                       'audio_seconds':round(seconds or 0,2),'estimated_usd_known_only': round(usd or 0,5),
                       'requests_unpriced':unknown or 0,
                       'usd_per_voiced_minute_estimate': round(usd * 60 / seconds,5) if usd is not None and seconds and not unknown else None,
                       'usd_per_voiced_hour_estimate': round(usd * 3600 / seconds,3) if usd is not None and seconds and not unknown else None})
    return {'month_utc':month,'rows':result,'total_requests':sum(x['requests'] for x in result),
            'total_estimated_usd_known_only':round(sum(x['estimated_usd_known_only'] for x in result),5),
            'has_unpriced_requests':any(x['requests_unpriced'] for x in result),
            'disclaimer':'Stime locali, non fatture ElevenLabs; utilizzo precedente a questa versione non incluso.'}


def _next_month(month):
    year, mon = map(int, month.split('-'))
    if mon == 12: return f'{year+1}-01'
    return f'{year}-{mon+1:02d}'


def forecast(data_root, model_id, minutes_per_day=60, chars_per_spoken_minute=900, days=30):
    if model_id not in REFERENCE:
        raise ValueError('Modello non incluso nel confronto')
    minutes=float(minutes_per_day)
    chars=float(chars_per_spoken_minute)
    if not (math.isfinite(minutes) and math.isfinite(chars) and 0 <= minutes <= 1440 and 100 <= chars <= 3000 and 1 <= days <= 366):
        raise ValueError('Parametri proiezione non validi')
    r=tariff(data_root, model_id)
    cost_per_min = (r['usd_per_1000_chars'] * chars / 1000.0) if r.get('usd_per_1000_chars') is not None else None
    return {'model_id':model_id,'minutes_per_day':minutes,'days':days,
            'chars_per_spoken_minute_assumed':chars,'usd_per_voiced_minute':round(cost_per_min,5) if cost_per_min is not None else None,
            'usd_per_voiced_hour':round(cost_per_min*60,4) if cost_per_min is not None else None,
            'usd_period':round(cost_per_min*minutes*days,2) if cost_per_min is not None else None,
            'rate_status':r['rate_status'],
            'note':'Proiezione da caratteri/minuto ipotizzati; il tempo di ascolto non viene fatturato come TTS.'}


def synced(data_root):
    return json_read(root(data_root) / 'models-snapshot.json', {})


def sync_elevenlabs(data_root, *, key, request_json):
    """Explicit user-triggered metadata sync; no pricing-page scraping.

    GET /v1/models gives provider model availability and credit multipliers,
    GET /v1/user/subscription (optional permission) provides workspace usage.
    It does NOT return canonical USD per model, so USD rates aren't 'updated'.
    """
    if not key:
        raise ValueError('Chiave ElevenLabs assente')
    models_obj = request_json(MODELS_API_URL, headers={'xi-api-key': key})
    if not isinstance(models_obj, list):
        raise ValueError('Risposta /v1/models non valida')
    safe_models = []
    for model in models_obj[:300]:
        if not isinstance(model, dict) or not isinstance(model.get('model_id'), str):
            continue
        rates = model.get('model_rates')
        if not isinstance(rates, dict):
            rates = {}
        safe_models.append({'model_id':model['model_id'][:90], 'name':str(model.get('name') or '')[:120],
                'can_do_text_to_speech': bool(model.get('can_do_text_to_speech')),
                'requires_alpha_access':bool(model.get('requires_alpha_access')),
                'token_cost_factor':model.get('token_cost_factor') if isinstance(model.get('token_cost_factor'), (int,float)) else None,
                'model_rates':{k:v for k,v in rates.items()
                        if k in ('character_cost_multiplier','cost_discount_multiplier') and isinstance(v,(int,float))}})
    out={'provider':'ElevenLabs','synced_at_utc':now(),
         'models':safe_models,'model_count':len(safe_models),
         'models_source':MODELS_API_URL,
         'public_rate_reference':{'source':PUBLIC_PRICING_URL,'as_of':PUBLIC_AS_OF,'auto_usd_prices':False},
         'subscription':None,'subscription_state':'not_checked'}
    try:
        sub=request_json(SUBSCRIPTION_API_URL, headers={'xi-api-key':key})
        if not isinstance(sub, dict):
            raise ValueError('Subscription malformata')
        out['subscription']={k:sub.get(k) for k in ['tier','character_count','character_limit','next_character_count_reset_unix','currency']}
        out['subscription_state']='available'
    except Exception:
        # Restricted read permissions are ordinary. Never invalidate TTS key.
        out['subscription_state']='restricted_or_unavailable'
    with LOCK:
        json_write(root(data_root) / 'models-snapshot.json', out)
    return out


def dashboard(data_root):
    remote=synced(data_root)
    prices=[]
    for mid in REFERENCE:
        p=tariff(data_root, mid)
        p['provider_model_metadata']=model_meta(data_root, mid)
        prices.append(p)
    return {'rates':prices,'sync':{'synced_at_utc':remote.get('synced_at_utc'),
                    'model_count':remote.get('model_count',0),
                    'subscription':remote.get('subscription'),
                    'subscription_state':remote.get('subscription_state','not_synced'),
                    'models_source':MODELS_API_URL,
                    'usd_prices_autosync':False,
                    'source_note':'La API modelli espone moltiplicatori; il listino USD è un riferimento manuale verificabile sul sito pubblico.'},
            'usage':summary(data_root),
            'pricing_source':PUBLIC_PRICING_URL,'reference_as_of':PUBLIC_AS_OF}
