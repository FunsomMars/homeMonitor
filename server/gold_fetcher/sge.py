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
import logging
import urllib.error
import urllib.request

from server.gold_format import FetchResult, parse_sge_table

logger = logging.getLogger(__name__)

LIST_URL = "https://www.sge.com.cn/sjzx/quotation_daily_new"
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


class SgeCurrentFetcher:
    name = "sge"
    kind = "current"

    def __init__(self, url: str = LIST_URL):
        self.url = url

    def fetch(self) -> FetchResult:
        try:
            html = _http_get(self.url)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return FetchResult(source=self.name, ok=False, error=f"http: {exc}")
        try:
            rows = parse_sge_table(html)
        except ValueError as exc:
            return FetchResult(source=self.name, ok=False, error=str(exc))
        return FetchResult(source=self.name, ok=True, rows=len(rows))


__all__ = ["SgeCurrentFetcher", "LIST_URL"]