import sqlite3
import json
from datetime import datetime
from typing import Dict, Any, List
import numpy as np
import pandas as pd

DB_PATH = 'analyses.db'


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS analyses (
        id INTEGER PRIMARY KEY,
        symbol TEXT,
        ts TEXT,
        decision TEXT,
        reason TEXT,
        indicators TEXT,
        fundamentals TEXT
    )
    ''')
    conn.commit()
    conn.close()


def _json_default(o):
    """Fallback serializer for numpy / pandas types to make json.dumps robust."""
    # numpy scalar types
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    # numpy arrays
    if isinstance(o, (np.ndarray,)):
        return o.tolist()
    # pandas types
    if isinstance(o, (pd.Timestamp,)):
        return o.isoformat()
    if isinstance(o, (pd.Timedelta,)):
        return str(o)
    if isinstance(o, (pd.Series,)):
        return o.tolist()
    # fallback to str
    return str(o)


def save_analysis(symbol: str, decision: str, reason: str, indicators: Dict[str, Any], fundamentals: Dict[str, Any]):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # Use a safe JSON serializer that converts numpy/pandas types
    indicators_json = json.dumps(indicators, default=_json_default, ensure_ascii=False)
    fundamentals_json = json.dumps(fundamentals, default=_json_default, ensure_ascii=False)
    cur.execute('INSERT INTO analyses(symbol, ts, decision, reason, indicators, fundamentals) VALUES (?,?,?,?,?,?)',
                (symbol, datetime.utcnow().isoformat(), decision, reason, indicators_json, fundamentals_json))
    conn.commit()
    conn.close()


def get_history(limit: int = 100) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('SELECT id, symbol, ts, decision, reason, indicators, fundamentals FROM analyses ORDER BY id DESC LIMIT ?', (limit,))
    rows = cur.fetchall()
    out = []
    for r in rows:
        out.append({
            'id': r[0], 'symbol': r[1], 'ts': r[2], 'decision': r[3], 'reason': r[4],
            'indicators': json.loads(r[5]) if r[5] else {},
            'fundamentals': json.loads(r[6]) if r[6] else {}
        })
    conn.close()
    return out
