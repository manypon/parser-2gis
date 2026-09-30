"""
Отвечает за поиск установленных у пользователя браузеров и их запуск
через Selenium с отдельным профилем приложения (папка data/profiles/<browser>),
чтобы не трогать личные вкладки/куки/историю пользователя.

Chrome, Яндекс.Браузер и Edge — все на движке Chromium, поэтому запускаются
через selenium.webdriver.Chrome с явным указанием пути к бинарнику браузера.
Firefox запускается через selenium.webdriver.Firefox.

Начиная с Selenium 4.6 не нужно руками скачивать chromedriver/geckodriver —
Selenium Manager делает это сам, подбирая версию под установленный браузер.
Для Яндекс.Браузера (он периодически расходится в версии протокола с "чистым"
Chromium) это иногда даёт сбой — тогда в лог пишется понятная причина и
пользователю предлагается выбрать Chrome/Edge.
"""
from __future__ import annotations

import platform
import shutil
from pathlib import Path

from . import config

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

# Типичные пути установки на Windows/macOS/Linux, на случай если
# бинарник не попал в PATH (обычная ситуация на Windows).
COMMON_PATHS = {
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ],
    "yandex": [
        r"C:\Users\%USERNAME%\AppData\Local\Yandex\YandexBrowser\Application\browser.exe",
        "/Applications/Yandex.app/Contents/MacOS/Yandex",
        "/usr/bin/yandex-browser",
    ],
    "edge": [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/microsoft-edge",
    ],
    "firefox": [
        r"C:\Program Files\Mozilla Firefox\firefox.exe",
        r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        "/Applications/Firefox.app/Contents/MacOS/firefox",
        "/usr/bin/firefox",
    ],
}


def find_browser_binary(key: str) -> str | None:
    import os

    for name in config.BROWSERS[key]["binary_names"]:
        found = shutil.which(name)
        if found:
            return found

    # %USERNAME%-путь не всегда раскрывается корректно (например, если
    # реальный логин отличается от переменной окружения) — для Яндекса
    # дополнительно пробуем собрать путь напрямую из %LOCALAPPDATA%.
    if key == "yandex" and IS_WINDOWS:
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            candidate = Path(local_appdata) / "Yandex" / "YandexBrowser" / "Application" / "browser.exe"
            if candidate.exists():
                return str(candidate)
        # На некоторых машинах Яндекс.Браузер ставится на всех пользователей.
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        candidate = Path(program_files) / "Yandex" / "YandexBrowser" / "Application" / "browser.exe"
        if candidate.exists():
            return str(candidate)

    for raw in COMMON_PATHS.get(key, []):
        expanded = os.path.expandvars(raw)
        if Path(expanded).exists():
            return expanded
    return None


def list_available() -> dict:
    """Для раздела "Браузеры" в UI: что нашли, что нет."""
    result = {}
    for key, meta in config.BROWSERS.items():
        binary = find_browser_binary(key)
        result[key] = {
            "label": meta["label"],
            "engine": meta["engine"],
            "found": binary is not None,
            "path": binary,
        }
    return result


def profile_dir(key: str) -> Path:
    d = config.PROFILES_DIR / key
    d.mkdir(parents=True, exist_ok=True)
    return d


class BrowserLaunchError(RuntimeError):
    pass


def launch(key: str, headless: bool = False, user_agent: str | None = None):
    """Возвращает запущенный selenium webdriver для выбранного браузера."""
    meta = config.BROWSERS.get(key)
    if meta is None:
        raise BrowserLaunchError(f"Неизвестный браузер: {key}")

    binary = find_browser_binary(key)
    if not binary:
        raise BrowserLaunchError(
            f"{meta['label']} не найден на компьютере. Выберите другой браузер."
        )

    profile = profile_dir(key)

    if meta["engine"] == "chromium":
        return _launch_chromium(binary, profile, headless, user_agent)
    if meta["engine"] == "gecko":
        return _launch_firefox(binary, profile, headless, user_agent)
    raise BrowserLaunchError(f"Неподдерживаемый движок: {meta['engine']}")


def _launch_chromium(binary: str, profile: Path, headless: bool, user_agent: str | None):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    options = Options()
    options.binary_location = binary
    options.add_argument(f"--user-data-dir={profile}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--lang=ru-RU")
    # У автоматизированного Chrome (виртуалки, ноды без GPU) часто падает
    # аппаратный WebGL-контекст, на котором держится канвас карты у Яндекс.Карт.
    # Если контекст падает, у SPA вылетает необработанное исключение и весь
    # интерфейс белеет — это выглядит как блокировка, но на деле краш рендера.
    # Форсируем программный (software) WebGL через SwiftShader — медленнее,
    # но не зависит от нестабильного GPU-драйвера под автоматизацией.
    options.add_argument("--use-gl=angle")
    options.add_argument("--use-angle=swiftshader-webgl")
    options.add_argument("--enable-unsafe-swiftshader")
    options.add_argument("--ignore-gpu-blocklist")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1400,1000")
    if user_agent:
        options.add_argument(f"--user-agent={user_agent}")

    try:
        driver = webdriver.Chrome(options=options)
    except Exception as exc:
        raise BrowserLaunchError(
            "Не удалось запустить браузер через Selenium. Если это Яндекс.Браузер, "
            "попробуйте Chrome или Edge — у них более стабильная поддержка драйвера. "
            f"Техническая причина: {exc}"
        ) from exc

    _apply_stealth(driver)
    return driver


def _apply_stealth(driver):
    """
    Прячет самые очевидные следы автоматизации, которые антибот-системы
    (в т.ч. у Яндекс.Карт) проверяют в первую очередь: флаг navigator.webdriver,
    пустой список плагинов/языков, нестандартный объект window.chrome.
    Полностью неотличимым от живого браузера это не делает — но снижает
    шанс блокировки на "белый экран" после нескольких карточек.
    """
    try:
        from selenium_stealth import stealth

        stealth(
            driver,
            languages=["ru-RU", "ru"],
            vendor="Google Inc.",
            platform="Win32",
            webgl_vendor="Intel Inc.",
            renderer="Intel Iris OpenGL Engine",
            fix_hairline=True,
        )
        return
    except ImportError:
        pass  # selenium-stealth не установлен — используем ручной, более скромный патч

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {
                "source": """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru']});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                window.chrome = window.chrome || { runtime: {} };
                """
            },
        )
    except Exception:
        pass


def _launch_firefox(binary: str, profile: Path, headless: bool, user_agent: str | None):
    from selenium import webdriver
    from selenium.webdriver.firefox.options import Options

    options = Options()
    options.binary_location = binary
    options.add_argument("-profile")
    options.add_argument(str(profile))
    if headless:
        options.add_argument("-headless")
    if user_agent:
        options.set_preference("general.useragent.override", user_agent)
    options.set_preference("dom.webdriver.enabled", False)
    # Та же причина, что и для Chromium: под автоматизацией GPU-ускоренный
    # WebGL/композитинг у Firefox тоже может падать и ронять рендер SPA
    # в белый экран. Переводим композитинг и WebGL на программный рендер.
    options.set_preference("layers.acceleration.disabled", True)
    options.set_preference("gfx.webrender.software", True)
    options.set_preference("webgl.force-enabled", True)
    options.set_preference("webgl.disable-fail-if-major-performance-caveat", True)

    try:
        return webdriver.Firefox(options=options)
    except Exception as exc:
        raise BrowserLaunchError(f"Не удалось запустить Firefox: {exc}") from exc
