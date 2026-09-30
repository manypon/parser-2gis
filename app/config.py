"""
Общие пути и константы приложения.
Все данные (профили браузера, логи, экспорт, настройки) хранятся рядом
с исполняемым файлом, в папке data/ — так проще переносить приложение
и не трогать системные папки пользователя без необходимости.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Корень приложения: если собрано в exe (PyInstaller) — рядом с exe,
# иначе — рядом с исходниками.
if getattr(sys, "frozen", False):
    APP_ROOT = Path(sys.executable).resolve().parent
else:
    APP_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = APP_ROOT / "data"
PROFILES_DIR = DATA_DIR / "profiles"      # отдельные профили браузеров
LOGS_DIR = DATA_DIR / "logs"              # текстовые логи сессий
EXPORTS_DIR = DATA_DIR / "exports"        # готовые CSV/XLSX/JSON/HTML
SETTINGS_FILE = DATA_DIR / "settings.json"
HISTORY_FILE = DATA_DIR / "history.json"

for d in (DATA_DIR, PROFILES_DIR, LOGS_DIR, EXPORTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

BASE_URL = "https://yandex.ru/maps"

# Поддерживаемые браузеры. driver — какой драйвер selenium использовать,
# binary_names — как искать исполняемый файл браузера в системе.
BROWSERS = {
    "chrome": {
        "label": "Google Chrome",
        "engine": "chromium",
        "binary_names": ["chrome", "google-chrome", "chrome.exe", "Google Chrome"],
    },
    "yandex": {
        "label": "Яндекс Браузер",
        "engine": "chromium",
        "binary_names": ["browser", "yandex-browser", "browser.exe"],
    },
    "edge": {
        "label": "Microsoft Edge",
        "engine": "chromium",
        "binary_names": ["msedge", "msedge.exe"],
    },
    "firefox": {
        "label": "Firefox",
        "engine": "gecko",
        "binary_names": ["firefox", "firefox.exe"],
    },
}

DEFAULT_SETTINGS = {
    "user_agent_mode": "default",   # default | custom
    "user_agent_custom": "",
    "autostart": False,
    "minimize_to_tray": True,
    "close_to_tray": True,
    "notifications": True,
    "output_dir": str(EXPORTS_DIR),
    "delay_seconds": 2.0,
    "random_delay": True,
    "remember_filters": True,
    "remember_browser": True,
    "last_browser": "chrome",
    "run_mode": "window",           # window | headless
}
