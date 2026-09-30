"""
Логика сбора организаций с yandex.ru/maps.

Как и у 2GIS, у Яндекс.Карт обфусцированные CSS-классы, которые меняются
при обновлениях фронтенда. Поэтому парсер не завязан на конкретные классы:

  * ссылки на карточки организаций содержат "/maps/org/" в href — это
    устойчивый якорь, который используется и в собственных публичных
    ссылках Яндекса на организацию (см. yandex.ru/maps/org/{id});
  * телефон, рейтинг и т.п. вытаскиваются регулярками из текста карточки;
  * список результатов у Яндекс.Карт — это отдельная скроллящаяся панель
    слева (карта справа не скроллится), поэтому скроллить нужно именно её,
    а не document.body — это ключевое отличие от 2GIS с его скроллом всей
    страницы. Панель ищется автоматически: берём первую ссылку на карточку
    и поднимаемся по родителям, пока не найдём элемент, у которого
    scrollHeight заметно больше clientHeight (то есть он реально скроллится).

Если Яндекс сильно поменяет вёрстку и эвристики перестанут работать —
править нужно константы ниже (CARD_LINK_CSS, регулярки), а не весь модуль.
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


class CaptchaRequired(Exception):
    """Сайт запросил капчу — нужно вмешательство пользователя в окне браузера."""


class SlowdownDetected(Exception):
    """Яндекс.Карты отвечают медленно/бросают таймауты — похоже на мягкий рейт-лимит."""


@dataclass
class ScrapeParams:
    query: str
    city: str
    limit: int = 100
    delay_seconds: float = 2.0
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


def _check_for_captcha(driver):
    text = driver.page_source.lower()
    if any(marker in text for marker in CAPTCHA_MARKERS):
        raise CaptchaRequired("Яндекс запросил проверку — пройдите капчу в открытом окне браузера.")


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

    # Категория/рубрика — обычно вторая короткая строка карточки, без цифр.
    for line in lines[1:3]:
        if not PHONE_RE.search(line) and not re.search(r"\d", line):
            org.category = line
            break

    # Адрес — строка с цифрой дома/улицей, но не телефон и не время работы.
    for line in lines[1:]:
        if (
            re.search(r"\d", line)
            and not PHONE_RE.fullmatch(line.strip())
            and "," in line
            and "—" not in line  # часто так пишут расписание "пн-вс 09:00—21:00"
        ):
            org.address = line
            break

    org.has_site, org.socials = _extract_socials(card_el)
    return org


def _find_scrollable_panel(driver, first_link):
    """
    У Яндекс.Карт список результатов — отдельная скроллящаяся панель слева,
    в отличие от 2GIS, где скроллится вся страница. Поднимаемся от первой
    найденной ссылки вверх по DOM и ищем ближайшего предка, у которого
    реально есть внутренний скролл (scrollHeight > clientHeight).
    Если не находим — откатываемся на скролл всей страницы.
    """
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


def scrape(
    driver,
    params: ScrapeParams,
    on_found: Callable[[Organization], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    should_pause: Callable[[], bool] | None = None,
) -> list[Organization]:
    """
    Основной цикл сбора. Возвращает список найденных организаций.
    on_found вызывается для каждой новой (не дублирующей) организации сразу
    после извлечения — на этом держится "живой" журнал в UI.
    should_stop / should_pause — коллбэки, которые дергает GUI-поток
    (кнопки "Пауза"/"Стоп" из интерфейса).
    """
    search_text = params.search_text()
    url = build_search_url(search_text)
    driver.get(url)

    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, CARD_LINK_CSS))
        )
    except TimeoutException:
        _check_for_captcha(driver)
        raise SlowdownDetected("Яндекс.Карты не отдали результаты за 20 секунд. Возможно, лимит запросов.")

    _check_for_captcha(driver)

    first_link = driver.find_element(By.CSS_SELECTOR, CARD_LINK_CSS)
    scroll_panel = _find_scrollable_panel(driver, first_link)

    results: list[Organization] = []
    seen_keys: set[str] = set()
    stagnant_scrolls = 0
    max_stagnant_scrolls = 6  # если N скроллов подряд не дали новых карточек — конец списка

    while len(results) < params.limit and stagnant_scrolls < max_stagnant_scrolls:
        if should_stop and should_stop():
            break
        while should_pause and should_pause():
            time.sleep(0.5)
            if should_stop and should_stop():
                return results

        _check_for_captcha(driver)

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
                # Карточка — обычно один из родителей ссылки; поднимаемся,
                # пока не найдём контейнер с более полным текстом карточки.
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

        if scroll_panel is not None:
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", scroll_panel)
        else:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        _sleep_between_requests(params)

    return results
