"""黄金价格模块的解析器、seed 加载、series 构造测试。

注意：所有断言都基于 server.gold_format 的真实 schema
（解析器返回 ``[{brand, price, effective_at}]``，不是 ``{contract, ...}``）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.gold_format import (  # noqa: E402
    CHANNELS, CHANNEL_META,
    load_seed_history, load_seed_brands,
    build_current, build_history_series, build_channels_meta,
    parse_sge_table, parse_shfe_kx, parse_shfe_json, parse_yahoo_csv, parse_yahoo_chart, parse_smm_html,
)
from server.app import merge_gold_history  # noqa: E402


# 真实 HTML/CSV 样本（取自数据源公开页面简化版）
SGE_HTML = """
<html><body>
<table>
  <tr><th>品种</th><th>最新价</th><th>涨跌</th><th>更新时间</th></tr>
  <tr><td>Au99.99</td><td>765.40</td><td>+1.20</td><td>2026-07-30 15:30:00</td></tr>
  <tr><td>Au99.95</td><td>765.00</td><td>+1.00</td><td>2026-07-30 15:30:00</td></tr>
  <tr><td>Au100g</td><td>766.50</td><td>+1.30</td><td>2026-07-30 15:30:00</td></tr>
  <tr><td>Ag99.99</td><td>9999.00</td><td>+0.00</td><td>2026-07-30 15:30:00</td></tr>
</table>
</body></html>
"""

SHFE_HTML = """
<html><body>
<table>
  <tr><th>合约</th><th>开盘</th><th>最高</th><th>最低</th><th>收盘</th><th>结算</th><th>涨跌</th></tr>
  <tr><td>au2606</td><td>763.20</td><td>765.80</td><td>761.50</td><td>764.30</td><td>763.60</td><td>+0.80</td></tr>
  <tr><td>au2512</td><td>760.10</td><td>762.50</td><td>759.80</td><td>761.20</td><td>761.00</td><td>+0.50</td></tr>
  <tr><td>cu2606</td><td>80000</td><td>80100</td><td>79900</td><td>80050</td><td>80060</td><td>+10</td></tr>
</table>
</body></html>
"""

YAHOO_CSV = """Date,Open,High,Low,Close,Adj Close,Volume
2024-01-02,2068.0,2075.0,2060.0,2063.0,2063.0,150000
2024-01-03,2063.0,2072.0,2058.0,2065.0,2065.0,180000
2024-01-04,2065.0,2070.0,2055.0,2061.0,2061.0,160000
2024-01-05,2061.0,2068.0,2050.0,2056.0,2056.0,170000
2024-01-08,2056.0,2063.0,2048.0,2059.0,2059.0,165000
2024-01-09,2059.0,2066.0,2052.0,2064.0,2064.0,175000
2024-01-10,2064.0,2071.0,2057.0,2068.0,2068.0,170000
"""

YAHOO_CSV_BAD = """Date,Open,High,Low,Close
2024-01-02,2068,2063
bad_row
2024-01-03
"""

SMM_HTML_TR = """
<table>
  <tr><td>周大福</td><td>962.00</td></tr>
  <tr><td>老凤祥</td><td>958.50</td></tr>
  <tr><td>周生生</td><td>956.00</td></tr>
  <tr><td>中国黄金</td><td>938.00</td></tr>
  <tr><td>潮宏基</td><td>954.00</td></tr>
  <tr><td>六福珠宝</td><td>960.00</td></tr>
  <tr><td>老庙黄金</td><td>956.00</td></tr>
  <tr><td>菜百首饰</td><td>942.00</td></tr>
  <tr><td>周大生</td><td>954.00</td></tr>
  <tr><td>中国珠宝</td><td>936.00</td></tr>
</table>
"""

# div 风格页面（退路匹配）
SMM_HTML_DIV = """
<div class="brand-row"><span class="brand">周大福</span><span class="price">962.00</span></div>
<div class="brand-row"><span class="brand">老凤祥</span><span class="price">958.50</span></div>
<div class="brand-row"><span class="brand">菜百首饰</span><span class="price">942.00</span></div>
"""

SMM_HTML_NEW = """
<table>
  <tr><th>品牌</th><th>产品</th><th>价格</th><th>更新时间</th></tr>
  <tr><td>中国黄金</td><td>999黄金</td><td>1255</td><td>2026-08-08</td></tr>
  <tr><td>六福珠宝</td><td>黄金</td><td>1283</td><td>2026-08-08</td></tr>
  <tr><td>周大福</td><td>PT950铂金</td><td>673</td><td>2026-08-08</td></tr>
</table>
"""


class SgeParserTest(unittest.TestCase):
    def test_returns_primary_contract(self):
        rows = parse_sge_table(SGE_HTML)
        brands = [r["brand"] for r in rows]
        self.assertIn("Au99.99", brands)
        self.assertIn("Au99.95", brands)

    def test_filters_non_au(self):
        rows = parse_sge_table(SGE_HTML)
        brands = [r["brand"] for r in rows]
        # Ag99.99 不应出现（非 Au 被过滤）
        self.assertNotIn("Ag99.99", brands)

    def test_latest_price_parsed(self):
        rows = parse_sge_table(SGE_HTML)
        au9999 = next(r for r in rows if r["brand"] == "Au99.99")
        self.assertAlmostEqual(au9999["price"], 765.40, places=2)
        self.assertIn("effective_at", au9999)

    def test_empty_html(self):
        self.assertEqual(parse_sge_table(""), [])

    def test_no_table(self):
        self.assertEqual(parse_sge_table("<html><body>no table</body></html>"), [])


class ShfeParserTest(unittest.TestCase):
    def test_json_main_contract_identified(self):
        rows = parse_shfe_json({
            "o_curinstrument": [
                {"PRODUCTGROUPID": "au", "DELIVERYMONTH": "2608",
                 "SETTLEMENTPRICE": 925.42, "OPENINTEREST": 3240},
                {"PRODUCTGROUPID": "au", "DELIVERYMONTH": "2610",
                 "SETTLEMENTPRICE": 927.54, "OPENINTEREST": 207293},
                {"PRODUCTGROUPID": "au", "DELIVERYMONTH": "小计",
                 "SETTLEMENTPRICE": "", "OPENINTEREST": 336567},
                {"PRODUCTGROUPID": "cu", "DELIVERYMONTH": "2608",
                 "SETTLEMENTPRICE": 1057.4, "OPENINTEREST": 999999},
            ]
        }, "2026-08-07")
        self.assertEqual(rows[0]["brand"], "au2610")
        self.assertEqual(rows[0]["price"], 927.54)
        self.assertEqual(rows[0]["effective_at"], "2026-08-07T00:00:00+08:00")

    def test_main_contract_identified(self):
        rows = parse_shfe_kx(SHFE_HTML)
        codes = [r["brand"] for r in rows]
        self.assertIn("au2606", codes)
        self.assertIn("au2512", codes)
        # 铜不应进入
        self.assertNotIn("cu2606", codes)

    def test_settlement_price(self):
        rows = parse_shfe_kx(SHFE_HTML)
        au = next(r for r in rows if r["brand"] == "au2606")
        # 第 6 列（索引 5）= 结算价 = 763.60
        self.assertAlmostEqual(au["price"], 763.60, places=2)

    def test_empty_html(self):
        self.assertEqual(parse_shfe_kx(""), [])

    def test_no_table(self):
        self.assertEqual(parse_shfe_kx("<html>empty</html>"), [])


class YahooParserTest(unittest.TestCase):
    def test_parses_chart_json(self):
        points = parse_yahoo_chart({
            "chart": {"result": [{
                "timestamp": [1783483200, 1783569600],
                "indicators": {"quote": [{"close": [4155.1, 4145.3]}]},
            }]}
        })
        self.assertEqual(len(points), 2)
        self.assertAlmostEqual(points[-1]["price"], 4145.3)

    def test_parses_csv_to_points(self):
        points = parse_yahoo_csv(YAHOO_CSV)
        self.assertGreaterEqual(len(points), 5)
        first = points[0]
        # 真实 schema: dict 含 brand/price/effective_at
        self.assertIn("price", first)
        self.assertIn("effective_at", first)
        self.assertAlmostEqual(first["price"], 2063.0, places=2)
        self.assertTrue(first["effective_at"].startswith("2024-01-02"))

    def test_skips_invalid_rows(self):
        # 坏行不影响好行；总有效行 >= 5 → 返回非空
        points = parse_yahoo_csv(YAHOO_CSV_BAD + "\n" + YAHOO_CSV.split("\n", 1)[1])
        self.assertGreater(len(points), 0)

    def test_too_few_rows(self):
        # 真实代码：总有效行 < 5 → 返回 []
        self.assertEqual(parse_yahoo_csv("Date,Close\n2024-01-02,2063\n2024-01-03,2065"), [])

    def test_empty(self):
        self.assertEqual(parse_yahoo_csv(""), [])
        self.assertEqual(parse_yahoo_csv("Date,Close"), [])


class SmmParserTest(unittest.TestCase):
    def test_new_product_table_style(self):
        rows = parse_smm_html(SMM_HTML_NEW)
        prices = {r["brand"]: r["price"] for r in rows}
        self.assertEqual(prices["中国黄金"], 1255)
        self.assertEqual(prices["六福珠宝"], 1283)
        self.assertNotIn("周大福", prices)

    def test_table_style(self):
        rows = parse_smm_html(SMM_HTML_TR)
        brands = {r["brand"] for r in rows}
        # 至少识别 6 个品牌
        self.assertGreaterEqual(len(brands), 6)
        self.assertIn("周大福", brands)
        self.assertIn("老凤祥", brands)

    def test_prices_in_range(self):
        rows = parse_smm_html(SMM_HTML_TR)
        for r in rows:
            self.assertGreater(r["price"], 400)
            self.assertLess(r["price"], 1500)

    def test_div_style(self):
        rows = parse_smm_html(SMM_HTML_DIV)
        brands = {r["brand"] for r in rows}
        # div 退路匹配
        self.assertGreater(len(brands), 0)

    def test_empty(self):
        self.assertEqual(parse_smm_html(""), [])

    def test_dedup(self):
        rows = parse_smm_html(SMM_HTML_TR + SMM_HTML_TR)
        brands = [r["brand"] for r in rows]
        # 每品牌应只出现一次
        self.assertEqual(len(brands), len(set(brands)))


class SeedLoadTest(unittest.TestCase):
    def test_seed_history_loads(self):
        data = load_seed_history()
        self.assertIn("channels", data)
        for ch in ("sge", "shfe", "yahoo", "smm"):
            self.assertIn(ch, data["channels"])
            # 至少 30+ 条
            self.assertGreater(len(data["channels"][ch]), 30)

    def test_seed_brands_loads(self):
        brands = load_seed_brands()
        self.assertIn("brands", brands)
        self.assertGreaterEqual(len(brands["brands"]), 5)

    def test_seed_history_prices_valid(self):
        data = load_seed_history()
        # 抽样校验价格落在合理区间
        sge = data["channels"]["sge"]
        for r in sge[:5]:
            self.assertGreater(r["price"], 200)
            self.assertLess(r["price"], 900)


class SeriesBuildTest(unittest.TestCase):
    def test_merge_history_keeps_seed_curve_and_overlays_live_day(self):
        seed = {"channels": {ch: [] for ch in CHANNELS}, "as_of": None}
        seed["channels"]["sge"] = [{
            "brand": "", "price": 783.52,
            "effective_at": "2026-07-31T00:00:00+08:00", "source": "seed",
        }]
        seed["channels"]["smm"] = [{
            "brand": "周大福", "price": 962.0,
            "effective_at": "2026-07-31T00:00:00+08:00", "source": "seed",
        }]
        merged = merge_gold_history(seed, [
            {"channel": "sge", "brand": "", "price": 928.47,
             "effective_at": "2026-08-08T12:00:00+08:00", "source": "sge"},
            {"channel": "smm", "brand": "周大福", "price": 1286.0,
             "effective_at": "2026-08-08T00:00:00+08:00", "source": "smm"},
        ])
        sge = build_history_series("sge", "", 180, seed_history=merged)
        smm = build_history_series("smm", "", 180, seed_history=merged)
        self.assertEqual(sge["series"][-1]["date"], "2026-08-08")
        self.assertEqual(len(sge["series"]), 2)
        self.assertEqual(smm["series"][0]["points"][-1], ["2026-08-08", 1286.0])

    def test_build_channels_meta(self):
        meta = build_channels_meta()
        self.assertEqual(len(meta["channels"]), len(CHANNELS))
        names = {c["channel"] for c in meta["channels"]}
        self.assertEqual(names, set(CHANNELS))

    def test_build_current(self):
        result = build_current()
        self.assertIn("channels", result)
        self.assertIn("as_of", result)
        for ch in CHANNELS:
            self.assertIn(ch, result["channels"])
            entry = result["channels"][ch]
            self.assertIn("items", entry)
            self.assertIn("name", entry)
            self.assertIn("unit", entry)

    def test_build_current_single_uses_newer_named_live_row(self):
        seed = {
            "channels": {ch: [] for ch in CHANNELS},
            "as_of": None,
        }
        seed["channels"]["sge"] = [{
            "brand": "",
            "price": 783.52,
            "effective_at": "2026-07-31T00:00:00+08:00",
            "source": "seed",
        }]
        seed["channels"]["sge"].append({
            "brand": "Au99.99",
            "price": 928.47,
            "effective_at": "2026-08-08T12:00:00+08:00",
            "source": "sge",
        })
        result = build_current(seed_history=seed, seed_brands={"brands": [], "as_of": None})
        current = result["channels"]["sge"]["items"][0]
        self.assertEqual(current["price"], 928.47)
        self.assertEqual(current["source"], "sge")

    def test_build_current_smm_multi(self):
        result = build_current()
        smm = result["channels"]["smm"]
        # SMM 是 multi 模式，items 数量 >= 5
        self.assertGreaterEqual(len(smm["items"]), 5)
        brands = {it["brand"] for it in smm["items"]}
        self.assertIn("周大福", brands)

    def test_build_history_series_sge(self):
        result = build_history_series("sge", "", days=400)
        self.assertEqual(result["channel"], "sge")
        self.assertEqual(result["unit"], "元/克")
        self.assertGreater(len(result["series"]), 0)
        first_point = result["series"][0]
        self.assertIn("date", first_point)
        self.assertIn("value", first_point)
        self.assertIsNotNone(result["stats"])
        self.assertIn("delta", result["stats"])
        self.assertIn("delta_pct", result["stats"])

    def test_build_history_series_yahoo(self):
        result = build_history_series("yahoo", "", days=30)
        self.assertEqual(result["channel"], "yahoo")
        self.assertEqual(result["unit"], "美元/盎司")
        self.assertGreater(len(result["series"]), 0)

    def test_build_history_series_smm_brand(self):
        result = build_history_series("smm", "周大福", days=400)
        self.assertEqual(result["channel"], "smm")
        self.assertEqual(result["brand"], "周大福")
        self.assertGreater(len(result["series"]), 0)

    def test_build_history_series_truncates_by_days(self):
        # days=10 应少于 days=400
        short = build_history_series("sge", "", days=10)
        long = build_history_series("sge", "", days=400)
        self.assertLess(len(short["series"]), len(long["series"]))

    def test_build_history_series_unknown_channel_falls_back(self):
        result = build_history_series("unknown", "", days=30)
        # 未知 channel 退路到 sge
        self.assertEqual(result["channel"], "sge")

    def test_build_history_series_no_data(self):
        result = build_history_series("sge", "NoSuchBrand", days=30)
        # 没有该品牌 → 空 series, stats=None
        self.assertEqual(result["series"], [])
        self.assertIsNone(result["stats"])


if __name__ == "__main__":
    unittest.main()
