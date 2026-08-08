"""国家发改委成品油调价通知监测器。

页面：https://www.ndrc.gov.cn/xwdt/xwfb/tztg/

抓取策略：拿到公告列表的标题 + 链接，匹配"成品油价格调整"标题，
然后访问正文提取调价数字。

为不依赖外网（测试环境无法访问 ndrc.gov.cn），HTTP 抓取失败时
返回 ``ok=False``，调用方继续用上次缓存。
"""

from __future__ import annotations

import logging
import re
import urllib.error
import urllib.request
from datetime import datetime
from urllib.parse import urljoin

from server.oil_format import FetchResult, parse_adjust_notice
from server.oil_fetcher import Fetcher

logger = logging.getLogger(__name__)

LIST_URL = "https://www.ndrc.gov.cn/xwdt/xwfb/"
HTTP_TIMEOUT = 8

# 公告列表里通常是 <a href="...">标题</a>
LIST_LINK_RE = re.compile(
    r'<a[^>]+href="(?P<href>[^"]+)"[^>]*>\s*(?P<title>[^<]*成品油[^<]*价格[^<]*调整[^<]*)\s*</a>',
    re.IGNORECASE,
)


def _http_get(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (homeMonitor-oil/1.0)",
            "Referer": "https://www.ndrc.gov.cn/",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
        return resp.read().decode("utf-8", errors="replace")


class NationalFGWEventFetcher:
    name = "national_fgw"
    kind = "event"

    def __init__(self, base_url: str = LIST_URL):
        self.base_url = base_url

    def fetch(self) -> FetchResult:
        try:
            html = _http_get(self.base_url)
        except (urllib.error.URLError, TimeoutError) as exc:
            return FetchResult(source=self.name, ok=False, error=f"list: {exc}")

        m = LIST_LINK_RE.search(html)
        if not m:
            return FetchResult(source=self.name, ok=True, rows=0)

        href = m.group("href")
        href = urljoin(self.base_url, href)
        try:
            detail_html = _http_get(href)
        except (urllib.error.URLError, TimeoutError) as exc:
            return FetchResult(source=self.name, ok=False, error=f"detail: {exc}")

        text = re.sub(r"<[^>]+>", "", detail_html)
        text = re.sub(r"\s+", " ", text)
        parsed = parse_adjust_notice(text)
        if not parsed:
            return FetchResult(source=self.name, ok=False, error="parse failed")
        effective = re.search(r"自(\d{4})年(\d{1,2})月(\d{1,2})日24时起", text)
        if effective is None:
            effective = re.search(r"自(\d{1,2})月(\d{1,2})日24时起", text)
            title_date = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", m.group("title"))
            if effective and title_date:
                year, month, day = title_date.groups()[0], effective.group(1), effective.group(2)
            else:
                year = month = day = None
        else:
            year, month, day = effective.groups()
        effective_at = (
            f"{year}-{int(month):02d}-{int(day):02d}T00:00:00+08:00"
            if year and month and day else datetime.now().astimezone().isoformat(timespec="seconds")
        )
        parsed.update({"effective_at": effective_at, "source": self.name, "notice_url": href})
        return FetchResult(source=self.name, ok=True, rows=1, data=[parsed], error=None)


__all__ = ["NationalFGWEventFetcher", "LIST_URL", "LIST_LINK_RE"]
