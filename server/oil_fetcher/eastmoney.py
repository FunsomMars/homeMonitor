"""东方财富历史油价抓取器。

API 形态（实测常见版本）：
    https://datacenter-web.eastmoney.com/api/data/v1/get?
        reportName=RPT_FUEL_OIL_HISTORY
        &columns=ALL
        &pageSize=200
        &filter=(PROVINCE=%22%E6%B1%9F%E8%8B%8F%22)(FUEL=%2292%22)

返回 JSON：``result.data`` 是省份历史价列表，每条形如
``{ "REPORT_DATE": "2026-07-04", "PRICE": 7.15, "PROVINCE": "江苏", "FUEL": "92" }``。

抓取失败时返回带 ``ok=False`` 的 ``FetchResult``，调用方保留上次缓存作兜底。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from server.oil_format import FetchResult, parse_eastmoney_row
from server.oil_fetcher import Fetcher

logger = logging.getLogger(__name__)

REPORT_NAME = "RPT_FUEL_OIL_HISTORY"
BASE_URL = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get"
    "?reportName={report}&columns=ALL&pageNumber=1&pageSize=200"
    "&filter={filter_}"
)
DEFAULT_PROVINCE = "江苏"
FUEL_TYPES: tuple[str, ...] = ("92", "95", "98", "0")
HTTP_TIMEOUT = 8


def build_url(province: str, fuel: str) -> str:
    cond = f'(PROVINCE="{province}")(FUEL="{fuel}")'
    return BASE_URL.format(
        report=REPORT_NAME,
        filter_=urllib.parse.quote(cond, safe="()="),
    )


def _http_get_json(url: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (homeMonitor-oil/1.0)",
            "Referer": "https://data.eastmoney.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _adapt_eastmoney_payload(payload: Any, province: str) -> list[dict]:
    """把东方财富原始字段映射到 ``parse_eastmoney_row`` 期望的 schema。"""
    data = (payload or {}).get("result", {}).get("data") or []
    out: list[dict] = []
    for raw in data:
        out.append({
            "province": raw.get("PROVINCE") or province,
            "fuel_type": str(raw.get("FUEL", "")).strip(),
            "price": raw.get("PRICE"),
            "effective_at": raw.get("REPORT_DATE"),
        })
    return out


class EastmoneyHistoryFetcher:
    name = "eastmoney"
    kind = "history"

    def __init__(self, province: str = DEFAULT_PROVINCE):
        self.province = province

    def fetch(self) -> FetchResult:
        ok_rows = 0
        last_err: str | None = None
        for fuel in FUEL_TYPES:
            url = build_url(self.province, fuel)
            try:
                payload = _http_get_json(url)
                adapted = _adapt_eastmoney_payload(payload, self.province)
                for it in adapted:
                    if parse_eastmoney_row(it) is not None:
                        ok_rows += 1
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_err = f"{fuel}: {exc}"
                logger.warning("eastmoney fetch failed: %s", last_err)
                continue
        if ok_rows == 0:
            return FetchResult(source=self.name, ok=False, rows=0,
                               error=last_err or "no rows")
        return FetchResult(source=self.name, ok=True, rows=ok_rows,
                           error=None if not last_err else f"partial: {last_err}")


__all__ = ["EastmoneyHistoryFetcher", "build_url", "DEFAULT_PROVINCE", "FUEL_TYPES"]