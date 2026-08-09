"""回填金店挂牌价的公开历史数据。

SMM 金店页面只提供当日列表；本工具从金价网的品牌黄金饰品历史页读取可用
日线，用当天 SMM 抓取结果作为最新价。可重复执行。
"""
from __future__ import annotations

import argparse
import http.client
import logging
from pathlib import Path
import subprocess
import urllib.request

from server.gold_format import parse_jinjia_history_html
from server.gold_store import DEFAULT_DB, GoldStore

LOG = logging.getLogger("home-monitor.store-price-backfill")

HISTORY_URLS = {
    "周大福": "https://www.jinjia.com.cn/chowtaifook/history.html",
    "老凤祥": "https://www.jinjia.com.cn/laofengxiang/history.html",
    "周生生": "https://www.jinjia.com.cn/chowsangsang/history.html",
    "六福珠宝": "https://www.jinjia.com.cn/lukfook/history.html",
    "老庙黄金": "https://www.jinjia.com.cn/laomiao/history.html",
    "菜百首饰": "https://www.jinjia.com.cn/caibai/history.html",
    "周大生": "https://www.jinjia.com.cn/chowtaiseng/history.html",
}
HTTP_TIMEOUT = 12


def _http_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as response:  # noqa: S310
            return response.read().decode("utf-8", errors="replace")
    except (http.client.IncompleteRead, OSError):
        # 该站点偶发提前断开分块响应；curl 可取得完整页面，避免历史只回填一半。
        proc = subprocess.run(
            ["curl", "--fail", "--silent", "--show-error", "--location",
             "--connect-timeout", "5", "--max-time", str(HTTP_TIMEOUT),
             "--header", "User-Agent: Mozilla/5.0", url],
            capture_output=True,
            timeout=HTTP_TIMEOUT + 5,
            check=False,
        )
        if proc.returncode:
            raise
        return proc.stdout.decode("utf-8", errors="replace")


def backfill_store_history(store: GoldStore) -> dict[str, int]:
    """将各品牌可公开取得的历史日线写入 ``gold_history``。"""
    counts: dict[str, int] = {}
    for brand, url in HISTORY_URLS.items():
        try:
            rows = parse_jinjia_history_html(_http_get(url), brand)
        except Exception as exc:  # 一个品牌失败不影响其余品牌
            LOG.warning("store history %s failed: %s", brand, exc)
            counts[brand] = 0
            continue
        for row in rows:
            store.insert_history("smm", brand, row["price"], row["effective_at"], "jinjia")
        counts[brand] = len(rows)
    LOG.info("store history backfill counts=%s", counts)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="回填金店挂牌价公开历史")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="gold.db 路径")
    args = parser.parse_args()
    print(backfill_store_history(GoldStore(Path(args.db))))


if __name__ == "__main__":
    main()
