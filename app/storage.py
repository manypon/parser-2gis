from __future__ import annotations

import json
import datetime as dt
from pathlib import Path

from . import config


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


# ---------- Настройки ----------

def load_settings() -> dict:
    saved = _read_json(config.SETTINGS_FILE, {})
    merged = {**config.DEFAULT_SETTINGS, **saved}
    return merged


def save_settings(settings: dict) -> dict:
    merged = {**config.DEFAULT_SETTINGS, **settings}
    _write_json(config.SETTINGS_FILE, merged)
    return merged


# ---------- История запусков ----------

def load_history() -> list[dict]:
    return _read_json(config.HISTORY_FILE, [])


def add_history_entry(query: str, city: str, found_count: int, params: dict) -> dict:
    history = load_history()
    entry = {
        "id": dt.datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "query": query,
        "city": city,
        "found_count": found_count,
        "date": dt.datetime.now().strftime("%d %b, %H:%M"),
        "timestamp": dt.datetime.now().isoformat(),
        "params": params,
    }
    history.insert(0, entry)
    history = history[:200]  # не даём файлу расти бесконечно
    _write_json(config.HISTORY_FILE, history)
    return entry
