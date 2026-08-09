"""油价模块的单元测试。"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import oil_format as of  # noqa: E402


class TestSeedLoading(unittest.TestCase):
    def test_seed_history_loads(self):
        data = of.load_seed_history()
        self.assertEqual(data["province"], "江苏")
        self.assertGreater(len(data["rows"]), 20)
        fts = {r["fuel_type"] for r in data["rows"]}
        self.assertIn("92", fts); self.assertIn("95", fts); self.assertIn("0", fts)

    def test_seed_history_validity(self):
        for r in of.load_seed_history()["rows"]:
            self.assertIn(r["fuel_type"], of.FUEL_TYPES)
            self.assertGreater(r["price"], 3.0)
            self.assertLess(r["price"], 20.0)
            datetime.fromisoformat(r["effective_at"])

    def test_seed_adjustments(self):
        evs = of.load_seed_adjustments()
        self.assertGreater(len(evs), 5)
        for a, b in zip(evs, evs[1:]):
            self.assertGreater(a["effective_at"], b["effective_at"])

    def test_seed_map_31_provinces(self):
        m = of.load_seed_map()
        self.assertEqual(len(m["data"]), 31)
        self.assertIsNotNone(m["min"]); self.assertIsNotNone(m["max"])
        self.assertLess(m["min"], m["max"])


class TestBuildCurrent(unittest.TestCase):
    def test_default_province(self):
        c = of.build_current()
        self.assertEqual(c["province"], "江苏")
        self.assertGreaterEqual(len(c["items"]), 3)
        types = {i["type"] for i in c["items"]}
        self.assertIn("92", types); self.assertIn("95", types)

    def test_unknown_province_falls_back(self):
        c = of.build_current(province="不存在省")
        self.assertEqual(c["province"], "江苏")

    def test_last_adjustment(self):
        c = of.build_current()
        self.assertIsNotNone(c["last_adjustment"])
        self.assertIn("gasoline_change", c["last_adjustment"])


class TestBuildHistorySeries(unittest.TestCase):
    def test_series_sorted(self):
        h = of.build_history_series("江苏", "92", 365)
        dates = [p["date"] for p in h["series"]]
        self.assertEqual(dates, sorted(dates))
        self.assertGreater(len(h["series"]), 5)

    def test_invalid_fuel_falls_back(self):
        h = of.build_history_series("江苏", "999", 365)
        self.assertEqual(h["type"], "92")

    def test_stats_delta(self):
        h = of.build_history_series("江苏", "92", 365)
        s = h["stats"]
        self.assertIsNotNone(s)
        self.assertAlmostEqual(s["delta"], s["last_value"] - s["first_value"], places=2)

    def test_days_window(self):
        h_full = of.build_history_series("江苏", "92", 3650)
        h_30 = of.build_history_series("江苏", "92", 30)
        self.assertGreaterEqual(len(h_full["series"]), len(h_30["series"]))

    def test_adjustment_marks_in_range(self):
        h = of.build_history_series("江苏", "92", 3650)
        adj_dates = {a["date"] for a in h["adjustments"]}
        series_dates = {p["date"] for p in h["series"]}
        self.assertTrue(adj_dates)
        self.assertGreaterEqual(min(adj_dates), min(series_dates))
        self.assertLessEqual(max(adj_dates), max(series_dates))

    def test_adjustment_mark_kept_without_same_day_price_point(self):
        rows = [
            {"fuel_type": "92", "price": 7.15, "effective_at": "2026-07-04T00:00:00+08:00"},
            {"fuel_type": "92", "price": 7.39, "effective_at": "2026-07-18T00:00:00+08:00"},
        ]
        adjustments = [{
            "effective_at": "2026-07-17T16:00:00+00:00",
            "gasoline_change": 300,
            "diesel_change": 290,
        }]
        h = of.build_history_series("江苏", "92", 365, rows, adjustments)
        self.assertEqual(h["adjustments"], [{
            "date": "2026-07-17", "gasoline_change": 300, "diesel_change": 290,
        }])


class TestFetchProtocol(unittest.TestCase):
    def test_parse_eastmoney_row(self):
        row = of.parse_eastmoney_row({
            "province": "江苏", "fuel_type": "92",
            "price": 7.39, "effective_at": "2026-07-18T00:00:00+08:00",
        })
        self.assertIsNotNone(row)
        self.assertEqual(row["source"], "eastmoney")
        self.assertEqual(row["price"], 7.39)

    def test_parse_eastmoney_row_invalid(self):
        self.assertIsNone(of.parse_eastmoney_row({"province": "x"}))                       # 缺字段
        self.assertIsNone(of.parse_eastmoney_row({"province": "x", "fuel_type": "92",
                                                  "price": "abc", "effective_at": "x"}))  # 价非数字
        self.assertIsNone(of.parse_eastmoney_row({"province": "x", "fuel_type": "92",
                                                  "price": -1, "effective_at": "x"}))      # 价越界
        self.assertIsNone(of.parse_eastmoney_row({"province": "x", "fuel_type": "abc",
                                                  "price": 7, "effective_at": "x"}))      # 油品非法
        self.assertIsNone(of.parse_eastmoney_row({"province": "x", "fuel_type": "92",
                                                  "price": 7, "effective_at": "not-iso"}))  # 时间非法

    def test_parse_adjust_notice_up(self):
        r = of.parse_adjust_notice("汽油价格每吨提高300元，柴油价格每吨提高290元")
        self.assertIsNotNone(r)
        self.assertEqual(r["gasoline_change"], 300)
        self.assertEqual(r["diesel_change"], 290)

    def test_parse_adjust_notice_down(self):
        r = of.parse_adjust_notice("汽油价格每吨降低145元，柴油价格每吨降低140元")
        self.assertIsNotNone(r)
        self.assertEqual(r["gasoline_change"], -145)
        self.assertEqual(r["diesel_change"], -140)

    def test_parse_adjust_notice_partial(self):
        r = of.parse_adjust_notice("汽油、柴油价格每吨分别提高300元和290元")
        self.assertIsNotNone(r)
        self.assertEqual(r["gasoline_change"], 300)
        self.assertEqual(r["diesel_change"], 290)

    def test_parse_adjust_notice_none(self):
        self.assertIsNone(of.parse_adjust_notice("无关文本"))
        self.assertIsNone(of.parse_adjust_notice(""))


class TestIntegrationAPIResponse(unittest.TestCase):
    def test_current_response_shape(self):
        c = of.build_current("江苏")
        self.assertIn("province", c); self.assertIn("as_of", c)
        self.assertIn("items", c); self.assertIn("last_adjustment", c)
        for it in c["items"]:
            for k in ("type", "price", "source", "confidence", "effective_at", "updated_at"):
                self.assertIn(k, it)

    def test_history_response_shape(self):
        h = of.build_history_series("江苏", "92", 365)
        for k in ("province", "type", "days", "series", "adjustments", "stats"):
            self.assertIn(k, h)
        for p in h["series"]:
            self.assertIn("date", p); self.assertIn("value", p)

    def test_map_response_shape(self):
        m = of.load_seed_map()
        for k in ("fuel_type", "min", "max", "data"):
            self.assertIn(k, m)
        for d in m["data"]:
            self.assertIn("name", d); self.assertIn("value", d)


if __name__ == "__main__":
    unittest.main()
