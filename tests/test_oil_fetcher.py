"""油价抓取器单元测试。

只覆盖**离线纯函数**与 fetcher 实例化属性。
在线 HTTP 抓取受网络/反爬影响，CI 不跑，靠运行时 fallback 兜底。
"""

from __future__ import annotations

import unittest

from server.oil_format import parse_adjust_notice
from server.oil_fetcher.eastmoney import (
    DEFAULT_PROVINCE,
    FUEL_TYPES,
    EastmoneyHistoryFetcher,
    build_url,
)
from server.oil_fetcher.jiangsu_fgw import (
    PROVINCE,
    JiangsuFGWCurrentFetcher,
    parse_price_table,
)
from server.oil_fetcher.national_fgw import (
    LIST_LINK_RE,
    NationalFGWEventFetcher,
)


class TestEastmoneyFetcher(unittest.TestCase):
    def test_attributes(self) -> None:
        f = EastmoneyHistoryFetcher()
        self.assertEqual(f.name, "eastmoney")
        self.assertEqual(f.kind, "history")
        self.assertEqual(f.province, DEFAULT_PROVINCE)
        self.assertEqual(len(FUEL_TYPES), 4)

    def test_url_contains_province_and_fuel(self) -> None:
        url = build_url("江苏", "92")
        self.assertIn("reportName=RPT_FUEL_OIL_HISTORY", url)
        self.assertIn("%E6%B1%9F%E8%8B%8F", url)  # 江苏 URL 编码
        self.assertIn("%2292%22", url)             # "92"


class TestJiangsuFGWParser(unittest.TestCase):
    SAMPLE_HTML = """
    <html><body>
      <h2>成品油价格调整公告</h2>
      <table>
        <tr><th>油品</th><th>价格(元/升)</th></tr>
        <tr><td>92号汽油</td><td>7.39</td></tr>
        <tr><td>95号汽油</td><td>7.86</td></tr>
        <tr><td>98号汽油</td><td>9.12</td></tr>
        <tr><td>0号柴油</td><td>7.04</td></tr>
      </table>
    </body></html>
    """

    def test_parses_four_fuels(self) -> None:
        parsed = parse_price_table(self.SAMPLE_HTML)
        self.assertEqual(parsed["province"], PROVINCE)
        items = {it["fuel_type"]: it["price"] for it in parsed["items"]}
        self.assertEqual(len(items), 4)
        self.assertAlmostEqual(items["92"], 7.39)
        self.assertAlmostEqual(items["95"], 7.86)
        self.assertAlmostEqual(items["98"], 9.12)
        self.assertAlmostEqual(items["0"], 7.04)

    def test_missing_table_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_price_table("<html><body>无表格</body></html>")

    def test_no_recognized_rows_raises(self) -> None:
        bad = "<table><tr><td>未知</td><td>1.0</td></tr></table>"
        with self.assertRaises(ValueError):
            parse_price_table(bad)

    def test_fetcher_attributes(self) -> None:
        f = JiangsuFGWCurrentFetcher()
        self.assertEqual(f.name, "jiangsu_fgw")
        self.assertEqual(f.kind, "current")
        self.assertEqual(f.url, "https://fzggw.jiangsu.gov.cn/")


class TestNationalFGW(unittest.TestCase):
    def test_attributes(self) -> None:
        f = NationalFGWEventFetcher()
        self.assertEqual(f.name, "national_fgw")
        self.assertEqual(f.kind, "event")

    def test_list_link_regex(self) -> None:
        html = (
            '<ul>'
            '<li><a href="/xwdt/xwfb/tztg/202607/t20260717_abc.html">'
            '国家发展改革委关于成品油价格调整的通知'
            '</a></li>'
            '</ul>'
        )
        m = LIST_LINK_RE.search(html)
        self.assertIsNotNone(m)
        assert m is not None
        self.assertIn("成品油", m.group("title"))
        self.assertIn("调整", m.group("title"))


class TestAdjustNoticeParse(unittest.TestCase):
    def test_pair_phrase(self) -> None:
        text = (
            "自2026年7月17日24时起，国内汽、柴油价格（标准品）"
            "每吨分别提高300元和290元。"
        )
        out = parse_adjust_notice(text)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out["gasoline_change"], 300)
        self.assertEqual(out["diesel_change"], 290)

    def test_negative_change(self) -> None:
        text = "自2026年4月17日24时起，汽柴油价格每吨分别降低235元和225元。"
        out = parse_adjust_notice(text)
        self.assertIsNotNone(out)
        assert out is not None
        self.assertEqual(out["gasoline_change"], -235)
        self.assertEqual(out["diesel_change"], -225)


if __name__ == "__main__":
    unittest.main()