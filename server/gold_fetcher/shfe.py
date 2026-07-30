"""上海期货交易所 沪金主力合约结算价抓取器。

行情页：https://www.shfe.cn/reports/tradedata/dailyandweeklydata/

SHFE 主力合约日结算价表 HTML（举例）：
    <table>
      <tr><th>合约</th><th>开盘</th><th>最高</th><th>最低</th><th>收盘</th><th>结算</th><th>涨跌</th><th>成交量</th><th>持仓量</th></tr>
      <tr><td>au2608</td><td>760.0</td><td>768.5</td><td>758.2</td><td>765.4</td><td>763.6</td><td>+3.2</td><td>12345</td><td>56789</td></tr>
    </table>

取所有以 ``au`` 开头的合约行的结算价；优先主力合约（即持仓量最大者）。
"""

from __future__ import annotations

import http.client
import logging
import urllib.error
import urllib.request

from server.gold_format import FetchResult, parse_shfe_kx

logger = logging.getLogger(__name__)

LIST_URL = "https://www.shfe.cn/reports/tradedata/dailyandweeklydata/"
HTTP_TIMEOUT = 8


def _http_get(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (homeMonitor-gold/1.0)",
            "Referer": "https://www.shfe.cn/",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
            return resp.read().decode("utf-8", errors="replace")
    except http.client.IncompleteRead as exc:
        # SHFE 服务器偶发 chunked 传输未正常终止, 把已读字节当 body 返回
        return exc.partial.decode("utf-8", errors="replace")


class ShfeFuturesFetcher:
    name = "shfe"
    kind = "current"

    def __init__(self, url: str = LIST_URL):
        self.url = url

    def fetch(self) -> FetchResult:
        try:
            html = _http_get(self.url)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return FetchResult(source=self.name, ok=False, error=f"http: {exc}")
        try:
            rows = parse_shfe_kx(html)
        except ValueError as exc:
            return FetchResult(source=self.name, ok=False, error=str(exc))
        return FetchResult(source=self.name, ok=True, rows=len(rows))


__all__ = ["ShfeFuturesFetcher", "LIST_URL"]