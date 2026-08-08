"""黄金价格后台调度器（守护线程）。

调度策略：
  - sge / shfe / smm：6h/次（中国市场日间更新频率低）
  - yahoo：6h/次（受 Yahoo 限流约束保守取整）

每次循环：依次调用 4 个 fetcher + 写 DB + 记日志。
失败不会终止守护线程；失败的 channel 下一轮继续尝试。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from server.gold_format import CHANNELS, FetchResult
from server.gold_fetcher.shfe import ShfeFuturesFetcher
from server.gold_fetcher.sge import SgeCurrentFetcher
from server.gold_fetcher.smm import SmmBrandFetcher
from server.gold_fetcher.yahoo import YahooGoldFetcher
from server.gold_store import GoldStore

logger = logging.getLogger("home-monitor.gold")

# 默认调度间隔（秒）：6 小时
DEFAULT_INTERVAL_SEC = 6 * 3600


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_fetcher(channel: str):
    if channel == "sge":
        return SgeCurrentFetcher()
    if channel == "shfe":
        return ShfeFuturesFetcher()
    if channel == "yahoo":
        return YahooGoldFetcher()
    if channel == "smm":
        return SmmBrandFetcher()
    raise ValueError(f"unknown gold channel: {channel}")


class GoldScheduler(threading.Thread):
    daemon = True

    def __init__(self, store: GoldStore, interval_sec: int = DEFAULT_INTERVAL_SEC):
        super().__init__(name="gold-scheduler")
        self.store = store
        self.interval_sec = max(60, interval_sec)
        self.stop_event = threading.Event()

    def stop(self):
        self.stop_event.set()

    def run(self):
        logger.info("gold scheduler started (interval=%ds)", self.interval_sec)
        # 启动先跑一轮（失败也不影响后续调度）
        self._run_once()
        while not self.stop_event.is_set():
            self.stop_event.wait(self.interval_sec)
            if self.stop_event.is_set():
                break
            self._run_once()

    # 单轮调度：依次执行 4 个 fetcher，失败回退到 seed
    def _run_once(self):
        for ch in CHANNELS:
            if self.stop_event.is_set():
                return
            try:
                self._run_channel(ch)
            except Exception as exc:  # 防止单 channel 异常影响下一轮
                logger.exception("gold channel %s raised: %s", ch, exc)

    def _run_channel(self, channel: str):
        fetcher = build_fetcher(channel)
        started = _now_iso()
        try:
            result: FetchResult = fetcher.fetch()
        except Exception as exc:
            result = FetchResult(source=fetcher.name, ok=False, error=f"exception: {exc}")
        finished = _now_iso()
        rows = 0
        if result.ok:
            rows = self._ingest(fetcher.name, result)
        self.store.write_fetch_log(
            source=fetcher.name,
            started_at=started,
            finished_at=finished,
            status="ok" if result.ok else "failed",
            message=result.error,
            rows_written=rows,
        )
        if result.ok:
            logger.info("gold %s ok rows=%d", channel, rows)
        else:
            logger.warning("gold %s failed: %s", channel, result.error)

    # ingest：根据 fetcher.name 走对应纯函数拿到 rows，再写 DB
    def _ingest(self, name: str, result: FetchResult) -> int:
        # Fetcher 已经完成抓取和解析，直接写入同一批结果，避免重复请求。
        rows = result.data
        n = 0
        for r in rows:
            # current upsert
            self.store.upsert_current(
                channel=name,
                brand=r.get("brand", ""),
                price=r["price"],
                effective_at=r["effective_at"],
                source=name,
                confidence="official",
            )
            # history 追加
            self.store.insert_history(
                channel=name,
                brand=r.get("brand", ""),
                price=r["price"],
                effective_at=r["effective_at"],
                source=name,
            )
            n += 1
        return n


# 上面为了避免在 _ingest 内部耦合 fetcher 内部 helper，重新写一组 thin wrappers
# 复用 gold_format 的 parse_* 纯函数。

def parse_sge_rows(html: str):
    from server.gold_format import parse_sge_table
    return parse_sge_table(html)


def parse_shfe_rows(html: str):
    from server.gold_format import parse_shfe_kx
    return parse_shfe_kx(html)


def parse_yahoo_rows(csv_text: str):
    from server.gold_format import parse_yahoo_csv
    return parse_yahoo_csv(csv_text)


def parse_smm_rows(html: str):
    from server.gold_format import parse_smm_html
    return parse_smm_html(html)


__all__ = ["GoldScheduler", "DEFAULT_INTERVAL_SEC", "build_fetcher"]
