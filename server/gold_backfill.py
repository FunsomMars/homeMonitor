"""用上金所、上期所官方日行情补齐黄金历史。

用法：``python -m server.gold_backfill --days 180``。
"""
from __future__ import annotations

import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

from server.gold_fetcher.sge import fetch_daily as fetch_sge_daily
from server.gold_fetcher.shfe import fetch_daily as fetch_shfe_daily
from server.gold_store import DEFAULT_DB, GoldStore

LOG = logging.getLogger("home-monitor.gold-backfill")


def _fetch_with_retry(fetcher, day, attempts: int = 3) -> list[dict]:
    last_error = None
    for attempt in range(attempts):
        try:
            return fetcher(day)
        except Exception as exc:  # 网络截断等短暂错误重试；非交易日最终仍会跳过
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.2 * (attempt + 1))
    raise last_error  # type: ignore[misc]


def _fetch_one(day, delay: float = 0.15):
    rows: dict[str, list[dict]] = {"sge": [], "shfe": []}
    errors: dict[str, str] = {}
    for channel, fetcher in (("sge", fetch_sge_daily), ("shfe", fetch_shfe_daily)):
        try:
            rows[channel] = _fetch_with_retry(fetcher, day)
        except Exception as exc:  # 非交易日的 404 等待下一日即可
            errors[channel] = str(exc)
        if delay > 0:
            time.sleep(delay)
    return day, rows, errors


def backfill_official_history(store: GoldStore, days: int = 180,
                              workers: int = 1, delay: float = 0.15) -> dict[str, int]:
    """补齐最近 ``days`` 天的官方日线；可重复执行，SQLite 唯一约束会去重。"""
    today = datetime.now().astimezone().date()
    dates = [today - timedelta(days=offset) for offset in range(days, -1, -1)
             if (today - timedelta(days=offset)).weekday() < 5]
    fetched = {"sge": 0, "shfe": 0}
    written = {"sge": 0, "shfe": 0}
    failures = {"sge": 0, "shfe": 0}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_fetch_one, day, delay) for day in dates]
        for future in as_completed(futures):
            _, rows_by_channel, errors = future.result()
            for channel, rows in rows_by_channel.items():
                fetched[channel] += len(rows)
                for row in rows:
                    # 单值图表统一使用空 brand，与前端历史查询参数保持一致。
                    store.insert_history(channel, "", row["price"], row["effective_at"], channel)
                    written[channel] += 1
            for channel in errors:
                failures[channel] += 1
    LOG.info("gold history backfill fetched=%s written=%s failures=%s",
             fetched, written, failures)
    return {
        "sge_fetched": fetched["sge"], "shfe_fetched": fetched["shfe"],
        "sge_written": written["sge"], "shfe_written": written["shfe"],
        "sge_failures": failures["sge"], "shfe_failures": failures["shfe"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="回填官方黄金日线")
    parser.add_argument("--days", type=int, default=180, help="回填自然日数，默认 180")
    parser.add_argument("--workers", type=int, default=1, help="并发请求数，默认 1")
    parser.add_argument("--delay", type=float, default=0.15, help="每次请求后的间隔秒数，默认 0.15")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="gold.db 路径")
    args = parser.parse_args()
    result = backfill_official_history(GoldStore(Path(args.db)), max(1, args.days), args.workers, args.delay)
    print(result)


if __name__ == "__main__":
    main()
