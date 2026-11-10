"""Фоновый обход каналов раз в FETCH_INTERVAL_SECONDS секунд."""
import logging
import threading

from app import config
from app.fetcher import fetch_all

log = logging.getLogger(__name__)


class FetchScheduler:
    def __init__(self, interval_seconds: int = config.FETCH_INTERVAL_SECONDS):
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="fetch-scheduler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        # первый обход сразу после старта: после перезапуска лента обновляется без ожидания
        while not self._stop.is_set():
            try:
                new_count = fetch_all()
                if new_count:
                    log.info("сборщик: новых статей %d", new_count)
            except Exception:
                log.exception("ошибка обхода каналов")
            self._stop.wait(self.interval_seconds)
