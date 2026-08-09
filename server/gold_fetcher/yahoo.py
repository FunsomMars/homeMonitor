"""Yahoo Finance COMEX 黄金主力 GC=F 历史价抓取器。

API：https://query1.finance.yahoo.com/v7/finance/download/GC=F?period1=...&period2=...&interval=1d&events=history

返回 CSV：
    Date,Open,High,Low,Close,Adj Close,Volume
    2025-07-30,2350.50,2365.20,2342.10,2358.40,2358.40,123456
    ...

历史频率 6h/次；只取 Close 列。
"""

from __future__ import annotations

import http.client
import json
import logging
import time
import urllib.error
import urllib.request

from server.gold_format import FetchResult, parse_yahoo_chart

logger = logging.getLogger(__name__)

SYMBOL = "GC=F"
HISTORY_URL = "https://query1.finance.yahoo.com/v8/finance/chart/" + SYMBOL
HTTP_TIMEOUT = 8

# 取近 1 年（Yahoo Finance 免费 API 最大返回窗口限制为 ~1y/单次）
DEFAULT_PERIOD_DAYS = 365


def build_url(days: int = DEFAULT_PERIOD_DAYS) -> str:
    return HISTORY_URL + f"?range={max(1, days)}d&interval=1d&events=history"


def _http_get(url: str) -> dict:
    req = urllib.request.Request(
        url,
        headers={
            # Yahoo 会拒绝带有自定义产品标识的 UA；使用普通浏览器 UA 可稳定
            # 返回公开 chart 接口，避免自动更新落入 403 后的旧数据。
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/131.0 Safari/537.36",
            "Accept": "application/json,text/plain,*/*",
            "Referer": "https://finance.yahoo.com/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except http.client.IncompleteRead as exc:
        return json.loads(exc.partial.decode("utf-8", errors="replace"))


class YahooGoldFetcher:
    name = "yahoo"
    kind = "history"

    def __init__(self, days: int = DEFAULT_PERIOD_DAYS):
        self.days = days

    def fetch(self) -> FetchResult:
        try:
            payload = _http_get(build_url(self.days))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            return FetchResult(source=self.name, ok=False, error=f"http: {exc}")
        try:
            rows = parse_yahoo_chart(payload)
        except ValueError as exc:
            return FetchResult(source=self.name, ok=False, error=str(exc))
        return FetchResult(source=self.name, ok=bool(rows), rows=len(rows), data=rows,
                           error=None if rows else "no quotation data")


__all__ = ["YahooGoldFetcher", "build_url", "SYMBOL", "DEFAULT_PERIOD_DAYS"]
