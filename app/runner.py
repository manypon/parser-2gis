from __future__ import annotations

import threading
import time
import datetime as dt

from . import browser_manager, storage, exporter
from .logger import SessionLogger
from .models import Organization
from .scraper import ScrapeParams, scrape, CaptchaRequired, SlowdownDetected


class RunController:
    """
    Держит состояние одного запуска парсинга и крутит его в отдельном потоке,
    чтобы не блокировать окно приложения. Все данные для UI отдаются через
    get_status() — простой поллинг раз в ~1 секунду с фронта, этого достаточно
    для живого журнала и прогресс-бара.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._reset()

    def _reset(self):
        self.state = "idle"          # idle | running | paused | captcha | slowdown | done | error | stopped
        self.driver = None
        self.logger: SessionLogger | None = None
        self.thread: threading.Thread | None = None
        self.orgs: list[Organization] = []
        self.params: ScrapeParams | None = None
        self.browser_key = None
        self.started_at = None
        self.finished_at = None
        self.error_message = ""
        self._stop_flag = False
        self._pause_flag = False
        self._speed_samples: list[int] = []  # найдено-в-минуту, скользящее окно
        self._last_minute_count = 0
        self._last_minute_ts = time.time()
        self._log_offset_seen = 0

    # ---------- команды из UI ----------

    def start(self, browser_key: str, run_mode: str, params_dict: dict):
        with self._lock:
            if self.state == "running":
                raise RuntimeError("Сбор уже идёт")
            self._reset()
            self.params = ScrapeParams(
                query=params_dict["query"],
                city=params_dict["city"],
                limit=int(params_dict.get("limit", 100)),
                delay_seconds=float(params_dict.get("delay_seconds", 2.0)),
                random_delay=bool(params_dict.get("random_delay", True)),
                hide_with_site=bool(params_dict.get("hide_with_site", False)),
                hide_with_social=bool(params_dict.get("hide_with_social", False)),
                only_with_phone=bool(params_dict.get("only_with_phone", False)),
                only_with_rating=bool(params_dict.get("only_with_rating", False)),
                hide_duplicates=bool(params_dict.get("hide_duplicates", True)),
            )
            self.browser_key = browser_key
            self.logger = SessionLogger(self.params.search_text())
            self.state = "running"
            self.started_at = dt.datetime.now()

            headless = run_mode == "headless"
            try:
                self.driver = browser_manager.launch(browser_key, headless=headless)
            except browser_manager.BrowserLaunchError as exc:
                self.state = "error"
                self.error_message = str(exc)
                self.logger.error(str(exc))
                return

            self.thread = threading.Thread(target=self._run_loop, daemon=True)
            self.thread.start()

    def pause(self):
        with self._lock:
            if self.state == "running":
                self._pause_flag = True
                self.state = "paused"
                if self.logger:
                    self.logger.info("Пауза по команде пользователя")

    def resume(self):
        with self._lock:
            if self.state in ("paused", "captcha", "slowdown"):
                self._pause_flag = False
                self.state = "running"
                if self.logger:
                    self.logger.info("Продолжаю сбор")

    def stop(self):
        with self._lock:
            self._stop_flag = True
            self._pause_flag = False
            if self.logger:
                self.logger.info("Остановлено по команде пользователя")

    # ---------- рабочий поток ----------

    def _run_loop(self):
        assert self.params is not None
        try:
            orgs = scrape(
                self.driver,
                self.params,
                on_found=self._on_found,
                should_stop=lambda: self._stop_flag,
                should_pause=lambda: self._pause_flag,
            )
            with self._lock:
                self.orgs = orgs
                if self._stop_flag:
                    self.state = "stopped"
                else:
                    self.state = "done"
                self.finished_at = dt.datetime.now()
            storage.add_history_entry(
                self.params.query, self.params.city, len(orgs),
                params_dict={
                    "limit": self.params.limit,
                    "browser": self.browser_key,
                },
            )
            if self.logger:
                self.logger.info(f"Готово. Собрано организаций: {len(orgs)}")
        except CaptchaRequired as exc:
            with self._lock:
                self.state = "captcha"
            if self.logger:
                self.logger.error(str(exc))
        except SlowdownDetected as exc:
            with self._lock:
                self.state = "slowdown"
            if self.logger:
                self.logger.warn(str(exc))
        except Exception as exc:  # непредвиденная ошибка — не роняем приложение
            with self._lock:
                self.state = "error"
                self.error_message = str(exc)
            if self.logger:
                self.logger.error(f"Непредвиденная ошибка: {exc}")
        finally:
            self._safe_quit_driver()

    def _safe_quit_driver(self):
        if self.driver and self.state in ("done", "stopped", "error"):
            try:
                self.driver.quit()
            except Exception:
                pass

    def _on_found(self, org: Organization):
        with self._lock:
            self.orgs.append(org)
            now = time.time()
            if now - self._last_minute_ts >= 60:
                self._speed_samples.append(self._last_minute_count)
                self._speed_samples = self._speed_samples[-8:]
                self._last_minute_count = 0
                self._last_minute_ts = now
            self._last_minute_count += 1
        if self.logger:
            self.logger.found(f"{org.name} — {org.address or 'адрес не найден'}")

    # ---------- для UI ----------

    def status(self) -> dict:
        with self._lock:
            new_events, offset = ([], self._log_offset_seen)
            if self.logger:
                new_events, offset = self.logger.events_since(self._log_offset_seen)
                self._log_offset_seen = offset

            elapsed = None
            if self.started_at:
                end = self.finished_at or dt.datetime.now()
                elapsed = int((end - self.started_at).total_seconds())

            current_speed = self._speed_samples[-1] if self._speed_samples else self._last_minute_count

            return {
                "state": self.state,
                "found": len(self.orgs),
                "limit": self.params.limit if self.params else 0,
                "orgs": [o.to_dict() for o in self.orgs[-40:]],  # последние — для живого журнала
                "speed_history": self._speed_samples,
                "current_speed": current_speed,
                "elapsed_seconds": elapsed,
                "error_message": self.error_message,
                "new_log_events": new_events,
                "query": self.params.query if self.params else "",
                "city": self.params.city if self.params else "",
            }

    def all_orgs(self) -> list[dict]:
        with self._lock:
            return [o.to_dict() for o in self.orgs]

    def export(self, fmt: str, output_dir: str | None = None) -> str:
        with self._lock:
            orgs = list(self.orgs)
            query = self.params.query if self.params else "export"
            city = self.params.city if self.params else ""
        return exporter.export(orgs, fmt, query, city, output_dir)
