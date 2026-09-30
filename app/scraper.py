"""
Логика сбора организаций с yandex.ru/maps.

Как и у 2GIS, у Яндекс.Карт обфусцированные CSS-классы, которые меняются
при обновлениях фронтенда. Поэтому парсер не завязан на конкретные классы:

  * ссылки на карточки организаций содержат "/maps/org/" в href — это
    устойчивый якорь;
  * телефон, рейтинг и т.п. вытаскиваются регулярками из текста карточки;
  * список результатов — отдельная скроллящаяся панель слева (карта справа
    не скроллится), скроллим именно её, а не document.body;
  * скролл идёт небольшими шагами с ожиданием, а не одним прыжком в конец —
    это больше похоже на поведение живого человека и реже провоцирует
    антибот-защиту сайта.

Если после нескольких успешных карточек страница внезапно "белеет"
(антибот детектит автоматизацию и рвёт рендер SPA) — это ловится отдельно
как is_blank_page() и обрабатывается так же, как капча: пользователю дают
знать через handle_block и ждут его решения, вместо того чтобы тихо
оборвать сбор или зависнуть.
"""
from __future__ import annotations

import re
import time
import random
import urllib.parse
from dataclasses import dataclass
from typing import Callable

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

from .models import Organization
from . import config

BASE_URL = config.BASE_URL
CARD_LINK_CSS = 'a[href*="/maps/org/"]'

PHONE_RE = re.compile(r"(?:\+7|8)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}")
RATING_RE = re.compile(r"\b([1-5][.,]\d)\b")
REVIEWS_RE = re.compile(r"\b(\d{1,5})\s*(?:отзыв|оцен)")
ORG_ID_RE = re.compile(r"/maps/org/(?:[^/]+/)?(\d+)")

CAPTCHA_MARKERS = (
    "подтвердите, что вы не робот",
    "smartcaptcha",
    "showcaptcha",
    "доступ ограничен",
    "unusual traffic",
    "recaptcha",
)

SOCIAL_HINTS = {
    "t.me": "Telegram",
    "telegram": "Telegram",
    "wa.me": "WhatsApp",
    "whatsapp": "WhatsApp",
    "instagram": "Instagram",
    "vk.com": "VK",
    "vkontakte": "VK",
    "facebook": "Facebook",
    "viber": "Viber",
}

# Через сколько последовательных скроллов без новых карточек считаем,
# что список закончился (а не просто подгружается).
MAX_STAGNANT_SCROLLS = 6


class CaptchaRequired(Exception):
    """Сайт запросил капчу — нужно вмешательство пользователя в окне браузера."""


class SlowdownDetected(Exception):
    """Яндекс.Карты отвечают медленно/бросают таймауты — похоже на мягкий рейт-лимит."""


@dataclass
class ScrapeParams:
    query: str
    city: str
    limit: int = 100
    delay_seconds: float = 4.0
    random_delay: bool = True
    hide_with_site: bool = False
    hide_with_social: bool = False
    only_with_phone: bool = False
    only_with_rating: bool = False
    hide_duplicates: bool = True

    def search_text(self) -> str:
        return f"{self.query.strip()} {self.city.strip()}".strip()


def build_search_url(search_text: str) -> str:
    encoded = urllib.parse.quote(search_text)
    return f"{BASE_URL}/?text={encoded}"


def _sleep_between_requests(params: ScrapeParams):
    base = max(0.0, params.delay_seconds)
    if params.random_delay:
        base = base * random.uniform(0.7, 1.4)
    time.sleep(base)


def _check_for_captcha(driver) -> bool:
    text = driver.page_source.lower()
    return any(marker in text for marker in CAPTCHA_MARKERS)


def _is_blank_page(driver) -> bool:
    """
    Ловит ситуацию, когда антибот-защита не показывает капчу, а просто
    рвёт рендер SPA — карточки исчезают, а на странице остаётся почти
    пустой body. Проверяем длину видимого текста: у живой страницы с
    результатами поиска она всегда заметно больше пары символов.
    """
    try:
        length = driver.execute_script("return document.body.innerText.length;")
        return length is not None and length < 200
    except Exception:
        return False


def _extract_socials(card_el) -> tuple[bool, list[str]]:
    has_site = False
    socials: list[str] = []
    try:
        links = card_el.find_elements(By.CSS_SELECTOR, "a[href]")
    except Exception:
        return has_site, socials
    for link in links:
        href = (link.get_attribute("href") or "").lower()
        aria = (link.get_attribute("aria-label") or "").lower()
        title = (link.get_attribute("title") or "").lower()
        blob = f"{href} {aria} {title}"
        if "/maps/org/" in href or "yandex." in href:
            continue
        matched = False
        for hint, label in SOCIAL_HINTS.items():
            if hint in blob and label not in socials:
                socials.append(label)
                matched = True
        if not matched and href.startswith("http"):
            has_site = True
    return has_site, socials


def _parse_card(card_el, link_href: str) -> Organization | None:
    text = card_el.text.strip()
    if not text:
        return None
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return None

    org = Organization()
    org.name = lines[0]
    org.card_url = link_href
    m = ORG_ID_RE.search(link_href)
    if m:
        org.org_id = m.group(1)

    phone_match = PHONE_RE.search(text)
    if phone_match:
        org.phone = phone_match.group(0)

    rating_match = RATING_RE.search(text)
    if rating_match:
        try:
            org.rating = float(rating_match.group(1).replace(",", "."))
        except ValueError:
            org.rating = None
    reviews_match = REVIEWS_RE.search(text)
    if reviews_match:
        org.reviews_count = int(reviews_match.group(1))

    for line in lines[1:3]:
        if not PHONE_RE.search(line) and not re.search(r"\d", line):
            org.category = line
            break

    for line in lines[1:]:
        if (
            re.search(r"\d", line)
            and not PHONE_RE.fullmatch(line.strip())
            and "," in line
            and "—" not in line
        ):
            org.address = line
            break

    org.has_site, org.socials = _extract_socials(card_el)
    return org


def _find_scrollable_panel(driver, first_link):
    try:
        el = first_link
        for _ in range(8):
            parent = driver.execute_script("return arguments[0].parentElement;", el)
            if parent is None:
                break
            is_scrollable = driver.execute_script(
                "return arguments[0].scrollHeight > arguments[0].clientHeight + 40;", parent
            )
            if is_scrollable:
                return parent
            el = parent
    except Exception:
        pass
    return None


def _scroll_step(driver, scroll_panel):
    """Скроллим небольшими шагами (~80% высоты панели), а не одним прыжком в конец."""
    if scroll_panel is not None:
        driver.execute_script(
            "arguments[0].scrollTop = arguments[0].scrollTop + arguments[0].clientHeight * 0.8;",
            scroll_panel,
        )
    else:
        driver.execute_script("window.scrollBy(0, window.innerHeight * 0.8);")


def _try_soft_recover(driver, scroll_panel, attempts: int = 2) -> bool:
    """
    Пробуем "починить" белый экран без навигации: скроллим список наверх
    и обратно, даём странице время перерисоваться. Если SPA просто
    подвисло (а не рухнуло насмерть) — это часто помогает и не требует
    полной перезагрузки страницы (а значит, не гоняет заново уже
    просмотренные карточки и не создаёт новую нагрузку на сервер).
    Возвращает True, если после попытки карточки снова видны на странице.
    """
    for _ in range(attempts):
        try:
            if scroll_panel is not None:
                driver.execute_script("arguments[0].scrollTop = 0;", scroll_panel)
            else:
                driver.execute_script("window.scrollTo(0, 0);")
        except Exception:
            pass
        time.sleep(1.5)
        try:
            if scroll_panel is not None:
                driver.execute_script(
                    "arguments[0].scrollTop = arguments[0].scrollHeight * 0.3;", scroll_panel
                )
            else:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.3);")
        except Exception:
            pass
        time.sleep(1.5)
        if not _is_blank_page(driver):
            try:
                if driver.find_elements(By.CSS_SELECTOR, CARD_LINK_CSS):
                    return True
            except Exception:
                pass
    return False


def _capture_diagnostics(driver, logger, tag: str):
    """
    Разовый снимок состояния браузера в момент сбоя (не на каждую
    организацию!) — скриншот, консоль браузера и HTML страницы. Пишется
    только если явно включено в настройках (debug_capture_on_block),
    в ту же папку логов, что и текстовый лог сессии.
    """
    if logger is None:
        return
    base = logger.file_path.with_suffix("")
    try:
        driver.save_screenshot(f"{base}_{tag}.png")
    except Exception:
        pass
    try:
        html = driver.execute_script("return document.documentElement.outerHTML;")
        (base.parent / f"{base.name}_{tag}.html").write_text(html or "", encoding="utf-8")
    except Exception:
        pass
    try:
        logs = driver.get_log("browser")  # доступно только у Chromium-браузеров
        lines = "\n".join(f"[{e.get('level')}] {e.get('message')}" for e in logs)
        (base.parent / f"{base.name}_{tag}_console.log").write_text(lines, encoding="utf-8")
    except Exception:
        pass  # Firefox/geckodriver не поддерживает get_log('browser') — тихо пропускаем


def _wait_for_results(driver, timeout=20) -> bool:
    """True — карточки появились. False — не появились (но и не капча/блок)."""
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, CARD_LINK_CSS))
        )
        return True
    except TimeoutException:
        return False


def scrape(
    driver,
    params: ScrapeParams,
    on_found: Callable[[Organization], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
    handle_block: Callable[[str, str], None] | None = None,
    logger=None,
    debug_capture: bool = False,
) -> list[Organization]:
    """
    Основной цикл сбора. Возвращает список найденных организаций.

    on_found — вызывается для каждой новой организации (живой журнал в UI).
    should_stop / should_pause — обычные Пауза/Стоп из интерфейса.
    handle_block(kind, message) — вызывается при капче или похожей на
    блокировку "белой странице". Это БЛОКИРУЮЩИЙ вызов: он должен сам
    дождаться, пока пользователь нажмёт "Продолжить" (или отменит сбор),
    и вернуть управление сюда только после этого — сам поток сбора при
    этом не завершается, просто стоит и ждёт, ничего не теряя.
    """
    search_text = params.search_text()
    url = build_search_url(search_text)
    driver.get(url)

    def _ensure_page_ready() -> bool:
        """Возвращает True, если карточки на странице есть. False — сбор пора закончить (остановлен)."""
        while True:
            if _wait_for_results(driver, timeout=20):
                return True
            if _check_for_captcha(driver):
                if handle_block:
                    handle_block("captcha", "Яндекс запросил проверку — пройдите капчу в открытом окне браузера, затем нажмите «Продолжить».")
                else:
                    raise CaptchaRequired("Нужна капча")
            else:
                if handle_block:
                    handle_block("slowdown", "Яндекс.Карты не отдали результаты за 20 секунд. Похоже на лимит запросов.")
                else:
                    raise SlowdownDetected("Нет ответа от страницы")
            if should_stop and should_stop():
                return False
            driver.get(url)  # пробуем ещё раз после того, как пользователь подтвердил

    if not _ensure_page_ready():
        return []

    first_link = driver.find_element(By.CSS_SELECTOR, CARD_LINK_CSS)
    scroll_panel = _find_scrollable_panel(driver, first_link)

    results: list[Organization] = []
    seen_keys: set[str] = set()
    stagnant_scrolls = 0

    while len(results) < params.limit and stagnant_scrolls < MAX_STAGNANT_SCROLLS:
        if should_stop and should_stop():
            break
        while should_pause and should_pause():
            time.sleep(0.5)
            if should_stop and should_stop():
                return results

        if _check_for_captcha(driver):
            if handle_block:
                handle_block("captcha", "Яндекс запросил проверку — пройдите капчу в открытом окне браузера, затем нажмите «Продолжить».")
            else:
                raise CaptchaRequired("Нужна капча")
            if should_stop and should_stop():
                break
            continue

        if _is_blank_page(driver):
            if _try_soft_recover(driver, scroll_panel):
                if logger:
                    logger.info("Белый экран — восстановилось без перезагрузки страницы")
                continue

            if debug_capture:
                _capture_diagnostics(driver, logger, tag=f"blank_{len(results)}")

            if handle_block:
                handle_block(
                    "slowdown",
                    "Страница неожиданно опустела и не восстановилась сама — похоже, сайт заметил автоматизацию. "
                    "Можно подождать и нажать «Продолжить» (страница перезагрузится), либо остановить сбор.",
                )
                if should_stop and should_stop():
                    break
                driver.get(url)
                if not _ensure_page_ready():
                    break
                first_link = driver.find_element(By.CSS_SELECTOR, CARD_LINK_CSS)
                scroll_panel = _find_scrollable_panel(driver, first_link)
                stagnant_scrolls = 0
                continue
            else:
                raise SlowdownDetected("Страница опустела — похоже на блокировку")

        try:
            links = driver.find_elements(By.CSS_SELECTOR, CARD_LINK_CSS)
        except StaleElementReferenceException:
            links = []

        new_before = len(results)
        for link in links:
            if len(results) >= params.limit:
                break
            try:
                href = link.get_attribute("href") or ""
                if "/maps/org/" not in href:
                    continue
                card = link
                for _ in range(4):
                    parent = card.find_element(By.XPATH, "..")
                    if parent is None:
                        break
                    card = parent
                    if len(card.text.strip()) > len(link.text.strip()):
                        continue

                org = _parse_card(card, href)
                if org is None:
                    continue

                key = org.dedupe_key()
                if params.hide_duplicates and key in seen_keys:
                    continue
                if params.hide_with_site and org.has_site:
                    continue
                if params.hide_with_social and org.socials:
                    continue
                if params.only_with_phone and not org.phone:
                    continue
                if params.only_with_rating and org.rating is None:
                    continue

                seen_keys.add(key)
                results.append(org)
                if on_found:
                    on_found(org)
            except StaleElementReferenceException:
                continue

        if len(results) == new_before:
            stagnant_scrolls += 1
        else:
            stagnant_scrolls = 0

        _scroll_step(driver, scroll_panel)
        _sleep_between_requests(params)

    return results
