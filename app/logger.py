from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import config


class SessionLogger:
    """
    Пишет обычный текстовый лог на диск (для разбора ошибок вручную,
    кнопка "Открыть папку с логами" в настройках) и параллельно копит
    события в памяти, чтобы фронтенд мог их запросить и показать
    как живой список — без скриншотов, просто короткие строки.
    """

    LEVELS = {"info", "warn", "error", "found"}

    def __init__(self, session_name: str):
        ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c for c in session_name if c.isalnum() or c in " _-").strip() or "session"
        self.file_path = config.LOGS_DIR / f"{ts}_{safe_name}.log"
        self._events: list[dict] = []
        self._fh = self.file_path.open("a", encoding="utf-8")
        self.info(f"Сессия запущена: {session_name}")

    def _write(self, level: str, message: str) -> dict:
        now = dt.datetime.now()
        event = {"time": now.strftime("%H:%M:%S"), "level": level, "message": message}
        self._events.append(event)
        line = f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] [{level.upper():5}] {message}\n"
        self._fh.write(line)
        self._fh.flush()
        return event

    def info(self, message: str) -> dict:
        return self._write("info", message)

    def warn(self, message: str) -> dict:
        return self._write("warn", message)

    def error(self, message: str) -> dict:
        return self._write("error", message)

    def found(self, message: str) -> dict:
        return self._write("found", message)

    def events_since(self, offset: int) -> tuple[list[dict], int]:
        """Для поллинга с фронта: отдать всё новое после индекса offset."""
        new = self._events[offset:]
        return new, len(self._events)

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass

    @staticmethod
    def logs_folder() -> Path:
        return config.LOGS_DIR
