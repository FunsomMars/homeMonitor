"""油价与调价公告后台调度器。"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from server.oil_fetcher.jiangsu_fgw import JiangsuFGWCurrentFetcher
from server.oil_fetcher.national_fgw import NationalFGWEventFetcher
from server.oil_store import OilStore

logger = logging.getLogger("home-monitor.oil")
DEFAULT_INTERVAL_SEC = 6 * 3600


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class OilScheduler(threading.Thread):
    daemon = True

    def __init__(self, store: OilStore, interval_sec: int = DEFAULT_INTERVAL_SEC):
        super().__init__(name="oil-scheduler")
        self.store = store
        self.interval_sec = max(300, interval_sec)
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        logger.info("oil scheduler started (interval=%ds)", self.interval_sec)
        self._run_once()
        while not self.stop_event.wait(self.interval_sec):
            self._run_once()

    def _run_once(self):
        self._run_current()
        if not self.stop_event.is_set():
            self._run_events()

    def _run_current(self):
        fetcher = JiangsuFGWCurrentFetcher()
        started = _now_iso()
        try:
            result = fetcher.fetch()
        except Exception as exc:  # noqa: BLE001
            logger.exception("oil current fetch raised: %s", exc)
            result = type("Result", (), {"ok": False, "data": [], "error": str(exc)})()
        written = 0
        if result.ok:
            for row in result.data:
                written += self.store.upsert_current(row)
        finished = _now_iso()
        self.store.write_fetch_log(fetcher.name, started, finished,
                                   "ok" if result.ok else "failed",
                                   getattr(result, "error", None), written)
        if result.ok:
            logger.info("oil current ok rows=%d written=%d", len(result.data), written)
        else:
            logger.warning("oil current failed: %s", result.error)

    def _run_events(self):
        fetcher = NationalFGWEventFetcher()
        started = _now_iso()
        try:
            result = fetcher.fetch()
        except Exception as exc:  # noqa: BLE001
            logger.exception("oil event fetch raised: %s", exc)
            result = type("Result", (), {"ok": False, "data": [], "error": str(exc)})()
        written = 0
        if result.ok:
            for event in result.data:
                written += self.store.upsert_adjustment(event)
        finished = _now_iso()
        self.store.write_fetch_log(fetcher.name, started, finished,
                                   "ok" if result.ok else "failed",
                                   getattr(result, "error", None), written)
        if result.ok:
            logger.info("oil events ok rows=%d", len(result.data))
        else:
            logger.warning("oil events failed: %s", result.error)


__all__ = ["OilScheduler", "DEFAULT_INTERVAL_SEC"]
