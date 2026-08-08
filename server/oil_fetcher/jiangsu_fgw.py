"""江苏省发改委当前油价抓取器。

公告页：https://fzggw.jiangsu.gov.cn/（成品油调价公告栏目）

价格表 HTML 形态（举例）：
    <table>
      <tr><th>油品</th><th>价格(元/升)</th></tr>
      <tr><td>92号汽油</td><td>7.39</td></tr>
      <tr><td>95号汽油</td><td>7.86</td></tr>
      <tr><td>0号柴油</td><td>7.04</td></tr>
    </table>

98 号全国不统一，省发改委一般不发，留空待 ``Sinopec98Fetcher`` 兜底（v1 未实现）。

为不依赖外网，本模块把页面抓取 + 解析解耦：
  - ``parse_price_table(html)`` 纯函数，方便单测
  - ``JiangsuFGWCurrentFetcher`` 仅负责 HTTP + 调用纯函数
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import urljoin

from server.oil_format import FetchResult
from server.oil_fetcher import Fetcher

logger = logging.getLogger(__name__)

LIST_URL = "http://fg.suzhou.gov.cn/"
HTTP_TIMEOUT = 8
DETAIL_LINK_RE = re.compile(
    r'<a[^>]+href="(?P<href>[^"]+)"[^>]*>\s*江苏省成品油价格调整公告[^<]*</a>',
    re.IGNORECASE,
)
PROVINCE = "江苏"

FUEL_NAME_PATTERNS: dict[str, re.Pattern[str]] = {
    "92": re.compile(r"92\s*(?:号|#)?[^0-9]{0,12}汽油"),
    "95": re.compile(r"95\s*(?:号|#)?[^0-9]{0,12}汽油"),
    "98": re.compile(r"98\s*(?:号|#)?[^0-9]{0,12}汽油"),
    "0":  re.compile(r"0\s*(?:号|#)?[^0-9]{0,12}柴油"),
}


def _http_get(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (homeMonitor-oil/1.0)",
            "Referer": "https://fzggw.jiangsu.gov.cn/",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
        return resp.read().decode("utf-8", errors="replace")


def parse_price_table(html: str) -> dict:
    """从公告页 HTML 解析油价表，返回 ``{"province":..., "items":[...]}``。"""
    m = re.search(r"<table.*?</table>", html, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        raise ValueError("no price table found")
    table_html = m.group(0)
    items: list[dict] = []
    for tr in re.findall(r"<tr.*?</tr>", table_html, flags=re.IGNORECASE | re.DOTALL):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.IGNORECASE | re.DOTALL)
        if len(cells) < 2:
            continue
        name = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", cells[0]))
        price_candidates = []
        for cell in cells[1:]:
            price_txt = re.sub(r"<[^>]+>", "", cell).strip().replace(",", "")
            try:
                price_candidates.append(float(price_txt))
            except ValueError:
                continue
        price = next((p for p in price_candidates if 3.0 <= p <= 20.0), None)
        if price is None:
            continue
        for fuel, pat in FUEL_NAME_PATTERNS.items():
            if pat.search(name):
                items.append({"fuel_type": fuel, "price": round(price, 2)})
                break
    if not items:
        raise ValueError("no recognized fuel rows in table")
    return {"province": PROVINCE, "items": items, "effective_at": parse_effective_at(html)}


def parse_effective_at(html: str) -> str | None:
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"\s+", "", text)
    m = re.search(r"自(\d{4})年(\d{1,2})月(\d{1,2})日24时起", text)
    if not m:
        return None
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}T00:00:00+08:00"


class JiangsuFGWCurrentFetcher:
    name = "jiangsu_fgw"
    kind = "current"

    def __init__(self, url: str = LIST_URL):
        self.url = url

    def fetch(self) -> FetchResult:
        try:
            listing = _http_get(self.url)
            match = DETAIL_LINK_RE.search(listing)
            if not match:
                return FetchResult(source=self.name, ok=False, error="no latest announcement")
            detail_url = urljoin(self.url, match.group("href"))
            html = _http_get(detail_url)
        except (urllib.error.URLError, TimeoutError) as exc:
            return FetchResult(source=self.name, ok=False, error=f"http: {exc}")
        try:
            parsed = parse_price_table(html)
        except ValueError as exc:
            return FetchResult(source=self.name, ok=False, error=str(exc))
        effective_at = parsed.get("effective_at") or datetime.now().astimezone().isoformat(timespec="seconds")
        rows = [
            {**item, "province": PROVINCE, "effective_at": effective_at, "source": self.name}
            for item in parsed["items"]
        ]
        return FetchResult(source=self.name, ok=bool(rows), rows=len(rows), data=rows,
                           error=None if rows else "no recognized fuel rows")


__all__ = ["JiangsuFGWCurrentFetcher", "parse_price_table", "parse_effective_at", "PROVINCE"]
