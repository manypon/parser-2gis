"""
Точка входа. Открывает десктопное окно (pywebview) с UI из ui/index.html
и пробрасывает в него Python-бэкенд как pywebview.api.

Запуск:  python main.py
"""
from pathlib import Path

import webview

from app.api import Api

UI_PATH = Path(__file__).resolve().parent / "ui" / "index.html"


def main():
    api = Api()
    window = webview.create_window(
        "Parser Яндекс.Карты",
        url=str(UI_PATH),
        js_api=api,
        width=1440,
        height=900,
        min_size=(1000, 640),
        frameless=True,   # своя титлбар-панель уже нарисована в UI
        easy_drag=False,  # перетаскивание окна за титлбар обрабатывает -webkit-app-region: drag
    )
    api.set_window(window)
    webview.start()


if __name__ == "__main__":
    main()
