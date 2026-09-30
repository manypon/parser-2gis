from __future__ import annotations

import os
import subprocess
import sys

from . import browser_manager, storage
from .runner import RunController
from .logger import SessionLogger


class Api:
    """
    Единственная точка входа, которую видит JS во фронтенде как `pywebview.api.*`.
    Все методы — обычные синхронные функции: pywebview сам заворачивает их
    в promise на стороне JS (await pywebview.api.method(...)).
    """

    def __init__(self):
        self.runner = RunController()
        self.window = None  # проставляется из main.py после создания окна

    def set_window(self, window):
        self.window = window

    # ---------- управление окном (своя титлбар-панель без рамки ОС) ----------

    def minimize_window(self) -> dict:
        if self.window:
            self.window.minimize()
        return {"ok": True}

    def toggle_maximize_window(self) -> dict:
        if self.window:
            self.window.toggle_fullscreen()
        return {"ok": True}

    def close_window(self) -> dict:
        if self.window:
            self.window.destroy()
        return {"ok": True}

    # ---------- настройки ----------

    def get_settings(self) -> dict:
        return storage.load_settings()

    def save_settings(self, settings: dict) -> dict:
        return storage.save_settings(settings)

    # ---------- браузеры ----------

    def list_browsers(self) -> dict:
        return browser_manager.list_available()

    # ---------- история ----------

    def get_history(self) -> list[dict]:
        return storage.load_history()

    # ---------- запуск/управление ----------

    def start_run(self, browser_key: str, run_mode: str, params: dict) -> dict:
        try:
            self.runner.start(browser_key, run_mode, params)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def pause_run(self) -> dict:
        self.runner.pause()
        return {"ok": True}

    def resume_run(self) -> dict:
        self.runner.resume()
        return {"ok": True}

    def stop_run(self) -> dict:
        self.runner.stop()
        return {"ok": True}

    def get_status(self) -> dict:
        return self.runner.status()

    def get_results(self) -> list[dict]:
        return self.runner.all_orgs()

    # ---------- экспорт ----------

    def export_results(self, fmt: str) -> dict:
        settings = storage.load_settings()
        try:
            path = self.runner.export(fmt, settings.get("output_dir"))
            return {"ok": True, "path": path}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    # ---------- системные штуки для раздела "Настройки" ----------

    def open_logs_folder(self) -> dict:
        return self._open_folder(str(SessionLogger.logs_folder()))

    def open_output_folder(self) -> dict:
        settings = storage.load_settings()
        return self._open_folder(settings.get("output_dir", ""))

    @staticmethod
    def _open_folder(path: str) -> dict:
        try:
            if not path or not os.path.isdir(path):
                return {"ok": False, "error": "Папка не найдена"}
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
