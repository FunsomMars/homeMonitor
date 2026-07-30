"""黄金数据采集模块。

按数据源分文件，每个抓取器实现统一的 ``Fetcher`` 协议：
    name:  数据源标识（与 channel 同名）
    kind:  history / current / mixed
    fetch() -> FetchResult

``FetchResult`` 与 ``parse_*`` 函数均来自 :mod:`server.gold_format`，
本包只负责：HTTP 抓取 + 把原始 payload 喂给 ``gold_format`` 的纯函数。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from server.gold_format import FetchResult


class Fetcher(Protocol):
    name: str
    kind: str  # "history" | "current" | "mixed"

    def fetch(self) -> FetchResult: ...


__all__ = ["Fetcher", "FetchResult"]