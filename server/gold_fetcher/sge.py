"""上海黄金交易所 Au99.99 当前价抓取器。

行情页：https://www.sge.com.cn/sjzx/quotation_daily_new

页面 HTML 形态（实测）：
    <table>
      <tr><th>品种</th><th>最新价(元/克)</th><th>涨跌</th><th>更新时间</th></tr>
      <tr><td>Au99.99</td><td>765.40</td><td>+1.20</td><td>2026-07-30 15:30:00</td></tr>
      <tr><td>Au99.95</td><td>764.80</td><td>+1.10</td><td>2026-07-30 15:30:00</td></tr>
    </table>

解析由 :func:`server.gold_format.parse_sge_table` 纯函数负责，便于单测。
"""

from __future__ import annotations

import http.client
import json
import logging
import re
import urllib.error
import urllib.request

from server.gold_format import FetchResult, parse_sge_table

logger = logging.getLogger(__name__)

QUOTATIONS_URL = "https://www.sge.com.cn/graph/quotations"
LIST_URL = QUOTATIONS_URL
HTTP_TIMEOUT = 8


def _http_get(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (homeMonitor-gold/1.0)",
            "Referer": "https://www.sge.com.cn/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
            return resp.read().decode("utf-8", errors="replace")
    except http.client.IncompleteRead as exc:
        return exc.partial.decode("utf-8", errors="replace")


def _http_get_json(url: str) -> dict:
    req = urllib.request.Request(
        url,
        data=b"",
        method="POST",
        headers={
            "User-Agent": "Mozilla/5.0 (homeMonitor-gold/1.0)",
            "Referer": "https://www.sge.com.cn/sjzx/quotation_daily_new",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def parse_sge_quote(payload: dict) -> list[dict]:
    """解析 SGE 当前 Au99.99 分钟行情 JSON。"""
    try:
        prices = payload.get("data") or []
        price = next(float(v) for v in reversed(prices) if v not in (None, "", "-"))
    except (AttributeError, StopIteration, TypeError, ValueError):
        return []
    if not 200.0 <= price <= 10000.0:
        return []
    stamp = str(payload.get("delaystr", ""))
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s+(\d{2}:\d{2}:\d{2})", stamp)
    if m:
        effective_at = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}T{m.group(4)}+08:00"
    else:
        from datetime import datetime
        effective_at = datetime.now().astimezone().isoformat(timespec="seconds")
    return [{"brand": payload.get("heyue") or "Au99.99", "price": round(price, 2), "effective_at": effective_at}]


class SgeCurrentFetcher:
    name = "sge"
    kind = "current"

    def __init__(self, url: str = LIST_URL):
        self.url = url

    def fetch(self) -> FetchResult:
        try:
            payload = _http_get_json(self.url)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            return FetchResult(source=self.name, ok=False, error=f"http: {exc}")
        try:
            rows = parse_sge_quote(payload)
        except ValueError as exc:
            return FetchResult(source=self.name, ok=False, error=str(exc))
        return FetchResult(source=self.name, ok=bool(rows), rows=len(rows), data=rows,
                           error=None if rows else "no quotation data")


__all__ = ["SgeCurrentFetcher", "LIST_URL", "QUOTATIONS_URL", "parse_sge_quote"]
