"""上海有色网（SMM）金店挂牌价聚合页抓取器。

聚合页：https://precious.smm.cn/gold-price

页面 HTML 形态（每品牌一行的挂牌价表）：
    <tr><td>周大福</td><td>962.00</td><td>...</td></tr>
    <tr><td>老凤祥</td><td>958.50</td><td>...</td></tr>
    ...

解析由 :func:`server.gold_format.parse_smm_html` 纯函数负责。
"""

from __future__ import annotations

import http.client
import gzip
import logging
import urllib.error
import urllib.request

from server.gold_format import FetchResult, parse_smm_html

logger = logging.getLogger(__name__)

LIST_URL = "https://precious.smm.cn/gold-price"
HTTP_TIMEOUT = 8


def _http_get(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (homeMonitor-gold/1.0)",
            "Referer": "https://precious.smm.cn/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
            body = resp.read()
            if (resp.headers.get("Content-Encoding") or "").lower() == "gzip" or body[:2] == b"\x1f\x8b":
                body = gzip.decompress(body)
            return body.decode("utf-8", errors="replace")
    except http.client.IncompleteRead as exc:
        return exc.partial.decode("utf-8", errors="replace")


class SmmBrandFetcher:
    name = "smm"
    kind = "current"

    def __init__(self, url: str = LIST_URL):
        self.url = url

    def fetch(self) -> FetchResult:
        try:
            html = _http_get(self.url)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return FetchResult(source=self.name, ok=False, error=f"http: {exc}")
        try:
            rows = parse_smm_html(html)
        except ValueError as exc:
            return FetchResult(source=self.name, ok=False, error=str(exc))
        return FetchResult(source=self.name, ok=bool(rows), rows=len(rows), data=rows,
                           error=None if rows else "no quotation data")


__all__ = ["SmmBrandFetcher", "LIST_URL"]
