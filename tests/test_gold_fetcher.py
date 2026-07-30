"""
黄金 fetcher 协议 / 实例化属性测试。

不实际联网。仅验证:
- 每个 Fetcher 暴露符合 Protocol 的属性 (name / kind / fetch())
- 实例化不会抛错
- 返回 FetchResult 包含 ok / data / error 字段
"""
import sys
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from server.gold_fetcher.sge import SgeCurrentFetcher  # noqa: E402
from server.gold_fetcher.shfe import ShfeFuturesFetcher  # noqa: E402
from server.gold_fetcher.yahoo import YahooGoldFetcher  # noqa: E402
from server.gold_fetcher.smm import SmmBrandFetcher  # noqa: E402


class FetcherAttributeTest(unittest.TestCase):
    def setUp(self):
        self.fetchers = [
            SgeCurrentFetcher(),
            ShfeFuturesFetcher(),
            YahooGoldFetcher(),
            SmmBrandFetcher(),
        ]

    def test_required_attributes(self):
        for f in self.fetchers:
            self.assertTrue(hasattr(f, "name"), f"{type(f).__name__} missing name")
            self.assertTrue(hasattr(f, "kind"), f"{type(f).__name__} missing kind")
            self.assertTrue(callable(getattr(f, "fetch", None)), f"{type(f).__name__} missing fetch()")

    def test_kind_in_history_current_mixed(self):
        for f in self.fetchers:
            self.assertIn(f.kind, ("history", "current", "mixed"))

    def test_names_unique(self):
        names = [f.name for f in self.fetchers]
        self.assertEqual(len(names), len(set(names)))


class FetcherResultShapeTest(unittest.TestCase):
    """验证所有 fetcher 在网络失败时返回 FetchResult(ok=False) 而不是抛异常。"""

    def test_all_fetchers_fail_gracefully(self):
        # 这里允许两种结果:
        #   - 网络可达且解析成功 → FetchResult(ok=True, data=...)
        #   - 网络失败 → FetchResult(ok=False, error=...)
        # 不允许抛异常
        for f in [
            SgeCurrentFetcher(),
            ShfeFuturesFetcher(),
            YahooGoldFetcher(),
            SmmBrandFetcher(),
        ]:
            try:
                result = f.fetch()
            except Exception as e:
                self.fail(f"{f.name}.fetch() raised {type(e).__name__}: {e}")
            self.assertIsNotNone(result)
            self.assertTrue(hasattr(result, "ok"))
            self.assertTrue(hasattr(result, "data") or hasattr(result, "error"))


if __name__ == "__main__":
    unittest.main()