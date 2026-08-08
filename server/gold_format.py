"""黄金价格模块：seed 加载、格式校验、涨跌幅统计、解析协议。"""
from __future__ import annotations

import json
import re
from html import unescape
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = ROOT / "data" / "seed"

# 四张图对应的 channel。顺序即为页面自上而下顺序。
CHANNELS: tuple[str, ...] = ("sge", "shfe", "yahoo", "smm")

# 每 channel 的默认显示名与单位
CHANNEL_META: dict[str, dict] = {
    "sge":   {"name": "上海黄金交易所 Au99.99", "unit": "元/克",     "kind": "single"},
    "shfe":  {"name": "上海期货交易所 沪金主力", "unit": "元/克",     "kind": "single"},
    "yahoo": {"name": "COMEX 黄金主力 GC=F",     "unit": "美元/盎司", "kind": "single"},
    "smm":   {"name": "金店挂牌价（SMM 聚合）",   "unit": "元/克",     "kind": "multi"},
}

# 价格合理范围（用于 seed 校验 + 防止爬虫抓到异常值）
PRICE_RANGE = {
    "sge":   (200.0, 900.0),    # 元/克
    "shfe":  (200.0, 900.0),
    "yahoo": (1000.0, 10000.0),  # 美元/盎司
    "smm":   (400.0, 1500.0),   # 元/克（金店零售挂牌含工费）
}


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _valid_price(channel: str, p) -> bool:
    try:
        v = float(p)
        lo, hi = PRICE_RANGE[channel]
        return lo <= v <= hi
    except (KeyError, TypeError, ValueError):
        return False


# ---------- seed ----------

def load_seed_history() -> dict:
    """加载 4 个 channel 的历史快照。

    返回 ``{"channels": {"sge": [...rows...], ...}, "as_of": "..."}``。
    每个 row 形如 ``{"brand": "Au99.99", "price": 765.4, "effective_at": "..."}``。
    """
    path = SEED_DIR / "gold_history.json"
    if not path.exists():
        return {"channels": {ch: [] for ch in CHANNELS}, "as_of": None}
    raw = json.loads(path.read_text(encoding="utf-8"))
    channels: dict[str, list[dict]] = {}
    for ch in CHANNELS:
        rows: list[dict] = []
        for it in raw.get("channels", {}).get(ch, []):
            brand = str(it.get("brand", "")).strip()
            eff = _parse_iso(it.get("effective_at", ""))
            if not eff:
                continue
            if not _valid_price(ch, it.get("price")):
                continue
            rows.append({
                "brand": brand,
                "price": round(float(it["price"]), 2),
                "effective_at": eff.isoformat(timespec="seconds"),
                "source": raw.get("source", "seed"),
            })
        rows.sort(key=lambda r: (r["brand"], r["effective_at"]))
        channels[ch] = rows
    return {"channels": channels, "as_of": raw.get("as_of")}


def load_seed_brands() -> dict:
    """加载 SMM 多品牌当前挂牌价快照。

    返回 ``{"as_of": "...", "brands": [{"brand":"周大福","price":962.0,"unit":"元/克"}, ...]}``。
    """
    path = SEED_DIR / "gold_brands.json"
    if not path.exists():
        return {"as_of": None, "brands": []}
    raw = json.loads(path.read_text(encoding="utf-8"))
    brands: list[dict] = []
    for it in raw.get("brands", []):
        if not _valid_price("smm", it.get("price")):
            continue
        brands.append({
            "brand": str(it.get("brand", "")).strip(),
            "price": round(float(it["price"]), 2),
            "unit": "元/克",
            "source": raw.get("source", "seed"),
        })
    return {"as_of": raw.get("as_of"), "brands": brands}


# ---------- series 构造 ----------

def _filter_by_brand(rows: list[dict], brand: str) -> list[dict]:
    if not brand:
        return [r for r in rows if not r.get("brand")]
    return [r for r in rows if r.get("brand") == brand]


def build_current(seed_history=None, seed_brands=None) -> dict:
    """构造 ``/api/gold/current`` 响应：4 个 channel 的最新价 + SMM 多品牌。"""
    if seed_history is None:
        seed_history = load_seed_history()
    if seed_brands is None:
        seed_brands = load_seed_brands()

    channels_out: dict[str, dict] = {}
    for ch in CHANNELS:
        meta = CHANNEL_META[ch]
        rows = seed_history["channels"].get(ch, [])
        # 取每 brand 各自的最新点
        latest_per_brand: dict[str, dict] = {}
        for r in rows:
            b = r.get("brand", "")
            cur = latest_per_brand.get(b)
            if not cur or r["effective_at"] > cur["effective_at"]:
                latest_per_brand[b] = r

        if ch == "smm":
            # 合并 seed 品牌列表
            live_brands = [
                {
                    "brand": b,
                    "price": r["price"],
                    "effective_at": r["effective_at"],
                    "source": r["source"],
                    "confidence": "fallback" if r["source"] == "seed" else "official",
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                }
                for b, r in latest_per_brand.items()
            ]
            # 没有 history 行时也允许从 brand 快照补
            if not live_brands and seed_brands["brands"]:
                as_of = seed_brands.get("as_of")
                live_brands = [
                    {
                        "brand": b["brand"],
                        "price": b["price"],
                        "effective_at": as_of or datetime.now().isoformat(timespec="seconds"),
                        "source": b["source"],
                        "confidence": "fallback",
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    }
                    for b in seed_brands["brands"]
                ]
            as_of_dt = max(
                (datetime.fromisoformat(b["effective_at"]) for b in live_brands),
                default=None,
            )
            channels_out[ch] = {
                "channel": ch,
                "name": meta["name"],
                "unit": meta["unit"],
                "kind": meta["kind"],
                "as_of": as_of_dt.isoformat(timespec="seconds") if as_of_dt else None,
                "items": live_brands,
            }
        else:
            # single 类型：取 brand=="" 的最新一行
            single = latest_per_brand.get("") or next(
                (latest_per_brand[b] for b in latest_per_brand),
                None,
            )
            if single is None:
                channels_out[ch] = {
                    "channel": ch, "name": meta["name"], "unit": meta["unit"],
                    "kind": meta["kind"], "as_of": None, "items": [],
                }
            else:
                channels_out[ch] = {
                    "channel": ch,
                    "name": meta["name"],
                    "unit": meta["unit"],
                    "kind": meta["kind"],
                    "as_of": single["effective_at"],
                    "items": [{
                        "brand": "",
                        "price": single["price"],
                        "effective_at": single["effective_at"],
                        "source": single["source"],
                        "confidence": "fallback" if single["source"] == "seed" else "official",
                        "updated_at": datetime.now().isoformat(timespec="seconds"),
                    }],
                }

    return {
        "channels": channels_out,
        "as_of": max(
            (c.get("as_of") for c in channels_out.values() if c.get("as_of")),
            default=None,
        ),
    }


def build_history_series(channel: str, brand: str, days: int,
                         seed_history=None) -> dict:
    """构造 ``/api/gold/history`` 响应：单 channel 单 brand 的曲线 + 涨跌幅统计。

    SMM 是多品牌 channel：当 ``brand`` 为空时，返回按 brand 分组的 series
    （元素为 ``{"brand", "points": [[date, value], ...]}``），与前端多线图对齐。
    其他 channel 仍然返回扁平 ``series: [{"date", "value"}]``。
    """
    if channel not in CHANNELS:
        channel = "sge"
    if seed_history is None:
        seed_history = load_seed_history()
    meta = CHANNEL_META[channel]

    rows = seed_history["channels"].get(channel, [])
    rows.sort(key=lambda r: r["effective_at"])

    # --- SMM 多品牌：无 brand 时返回按品牌分组的 series ---
    if channel == "smm" and not brand:
        by_brand: dict[str, list[dict]] = {}
        for r in rows:
            by_brand.setdefault(r.get("brand", ""), []).append(r)
        # 按 brand 各自截断天数
        series: list[dict] = []
        for bname, brows in by_brand.items():
            cropped = brows
            if days > 0 and brows:
                last_dt = datetime.fromisoformat(brows[-1]["effective_at"])
                cutoff = last_dt - timedelta(days=days)
                cropped = [r for r in brows if datetime.fromisoformat(r["effective_at"]) >= cutoff]
            points = [[r["effective_at"][:10], r["price"]] for r in cropped]
            if points:
                series.append({"brand": bname, "points": points})
        # 整体 stats：按 brand 在最后日的均价聚合
        stats = None
        if series:
            last_dates = {s["points"][-1][0] for s in series if s["points"]}
            last_date = max(last_dates) if last_dates else None
            first_date = min((s["points"][0][0] for s in series if s["points"]), default=None)
            if last_date and first_date:
                vals_last = [s["points"][-1][1] for s in series if s["points"] and s["points"][-1][0] == last_date]
                vals_first = [s["points"][0][1] for s in series if s["points"] and s["points"][0][0] == first_date]
                if vals_last and vals_first:
                    avg_last = round(sum(vals_last) / len(vals_last), 2)
                    avg_first = round(sum(vals_first) / len(vals_first), 2)
                    delta = round(avg_last - avg_first, 2)
                    delta_pct = round(delta / avg_first * 100, 2) if avg_first else 0
                    stats = {
                        "first_date": first_date,
                        "last_date": last_date,
                        "first_value": avg_first,
                        "last_value": avg_last,
                        "delta": delta,
                        "delta_pct": delta_pct,
                    }
        return {
            "channel": channel,
            "brand": "",
            "name": meta["name"],
            "unit": meta["unit"],
            "kind": meta["kind"],
            "days": days,
            "series": series,
            "stats": stats,
        }

    # --- 单品牌 / 单 channel 默认路径 ---
    rows = _filter_by_brand(rows, brand)

    if days > 0 and rows:
        last_dt = datetime.fromisoformat(rows[-1]["effective_at"])
        cutoff = last_dt - timedelta(days=days)
        rows = [r for r in rows if datetime.fromisoformat(r["effective_at"]) >= cutoff]

    series = [{"date": r["effective_at"][:10], "value": r["price"]} for r in rows]

    stats = None
    if series:
        first, last = series[0], series[-1]
        delta = round(last["value"] - first["value"], 2)
        delta_pct = round(delta / first["value"] * 100, 2) if first["value"] else 0
        stats = {
            "first_date": first["date"],
            "last_date": last["date"],
            "first_value": first["value"],
            "last_value": last["value"],
            "delta": delta,
            "delta_pct": delta_pct,
        }

    return {
        "channel": channel,
        "brand": brand,
        "name": meta["name"],
        "unit": meta["unit"],
        "kind": meta["kind"],
        "days": days,
        "series": series,
        "stats": stats,
    }


def build_channels_meta(seed_history=None, source_status: dict | None = None) -> dict:
    """构造 ``/api/gold/channels`` 响应：4 个 channel 的元信息。

    ``source_status`` 形如 ``{channel: {"last_run_at": ..., "last_status": ...}}``，
    来自 gold_source 表；缺失时退路为 None。
    """
    if seed_history is None:
        seed_history = load_seed_history()
    if source_status is None:
        source_status = {}
    out = []
    for ch in CHANNELS:
        meta = CHANNEL_META[ch]
        rows = seed_history["channels"].get(ch, [])
        as_of = max((r["effective_at"] for r in rows), default=None)
        src = source_status.get(ch, {})
        out.append({
            "channel": ch,
            "name": meta["name"],
            "unit": meta["unit"],
            "kind": meta["kind"],
            "row_count": len(rows),
            "seed_as_of": as_of,
            "last_run_at": src.get("last_run_at"),
            "last_status": src.get("last_status"),
        })
    return {"channels": out}


# ---------- 抓取协议 ----------

class FetchResult:
    """与 server.oil_format.FetchResult 同结构，便于上层统一处理。"""

    def __init__(self, source: str, ok: bool, rows: int = 0,
                 error: str | None = None, data: list[dict] | None = None):
        self.source = source
        self.ok = ok
        self.rows = rows
        self.error = error
        self.data = data or []


# ---------- 4 个 channel 的纯函数解析器 ----------

def parse_sge_table(html: str) -> list[dict]:
    """从上海黄金交易所行情页 HTML 解析 Au99.99 等品种的当前价。

    SGE 表格典型结构（实测简化版）：
        <table>
          <tr><th>品种</th><th>最新价</th><th>涨跌</th><th>更新时间</th></tr>
          <tr><td>Au99.99</td><td>765.40</td><td>+1.20</td><td>2026-07-30 15:30:00</td></tr>
        </table>

    返回列表，每条 ``{"brand": "Au99.99", "price": 765.4, "effective_at": "..."}``。
    空输入或未识别到 Au 行 → 返回 ``[]``。
    """
    if not html or "<table" not in html.lower():
        return []
    m = re.search(r"<table.*?</table>", html, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    table = m.group(0)
    out: list[dict] = []
    for tr in re.findall(r"<tr.*?</tr>", table, flags=re.IGNORECASE | re.DOTALL):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.IGNORECASE | re.DOTALL)
        if len(cells) < 2:
            continue
        brand = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", cells[0]))
        price_txt = re.sub(r"<[^>]+>", "", cells[1]).strip()
        price_txt = price_txt.replace(",", "")
        try:
            price = float(price_txt)
        except ValueError:
            continue
        if not brand.lower().startswith("au"):
            continue
        if not _valid_price("sge", price):
            continue
        eff = None
        if len(cells) >= 4:
            t = re.sub(r"<[^>]+>", "", cells[3]).strip()
            dt = _parse_iso(t.replace(" ", "T"))
            if dt:
                eff = dt.isoformat(timespec="seconds")
        if not eff:
            eff = datetime.now().isoformat(timespec="seconds")
        out.append({"brand": brand, "price": round(price, 2), "effective_at": eff})
    return out


# SHFE 主力合约日结算价 HTML：列依次为 合约代码, 开盘, 最高, 最低, 收盘, 结算, 涨跌, 成交量, 持仓量
def parse_shfe_kx(html: str) -> list[dict]:
    """从 SHFE 日结行情 HTML 解析黄金主力合约。

    返回列表，每条 ``{"brand": "au2606", "price": 765.4, "effective_at": "..."}``。
    空输入或未识别到 au 行 → 返回 ``[]``。
    """
    if not html or "<table" not in html.lower():
        return []
    m = re.search(r"<table.*?</table>", html, flags=re.IGNORECASE | re.DOTALL)
    if not m:
        return []
    table = m.group(0)
    out: list[dict] = []
    for tr in re.findall(r"<tr.*?</tr>", table, flags=re.IGNORECASE | re.DOTALL):
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.IGNORECASE | re.DOTALL)
        if len(cells) < 6:
            continue
        code = re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", cells[0])).lower()
        if not code.startswith("au"):
            continue
        # 取第 5 列（结算价）作当前价
        price_txt = re.sub(r"<[^>]+>", "", cells[5]).strip().replace(",", "")
        try:
            price = float(price_txt)
        except ValueError:
            continue
        if not _valid_price("shfe", price):
            continue
        eff = datetime.now().isoformat(timespec="seconds")
        out.append({"brand": code, "price": round(price, 2), "effective_at": eff})
    return out


def parse_yahoo_csv(csv_text: str) -> list[dict]:
    """从 Yahoo Finance ``download/GC=F`` CSV 解析历史 + 最新价。

    CSV 列：Date,Open,High,Low,Close,Adj Close,Volume。
    返回列表，按日期升序。任何缺列或缺值行会被静默跳过；总行数 < 5 时返回 ``[]``。
    """
    out: list[dict] = []
    lines = [ln for ln in (csv_text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return []
    header = [c.strip().lower() for c in lines[0].split(",")]
    if "date" not in header or "close" not in header:
        return []
    idx_date = header.index("date")
    idx_close = header.index("close")
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) <= max(idx_date, idx_close):
            continue
        date_txt = parts[idx_date].strip()
        try:
            price = float(parts[idx_close])
        except ValueError:
            continue
        if not _valid_price("yahoo", price):
            continue
        dt = _parse_iso(date_txt + "T00:00:00+00:00")
        if not dt:
            continue
        out.append({
            "brand": "",
            "price": round(price, 2),
            "effective_at": dt.isoformat(timespec="seconds"),
        })
    if len(out) < 5:
        return []
    out.sort(key=lambda r: r["effective_at"])
    return out


def parse_yahoo_chart(payload: dict) -> list[dict]:
    """解析 Yahoo Finance chart JSON（下载 CSV 接口需要 cookie/token）。"""
    try:
        result = (payload.get("chart", {}).get("result") or [])[0]
        timestamps = result.get("timestamp") or []
        closes = (result.get("indicators", {}).get("quote") or [])[0].get("close") or []
    except (AttributeError, IndexError, KeyError, TypeError):
        return []
    out: list[dict] = []
    for ts, close in zip(timestamps, closes):
        if close is None or not _valid_price("yahoo", close):
            continue
        try:
            dt = datetime.fromtimestamp(float(ts)).astimezone()
        except (TypeError, ValueError, OverflowError, OSError):
            continue
        out.append({
            "brand": "",
            "price": round(float(close), 2),
            "effective_at": dt.isoformat(timespec="seconds"),
        })
    out.sort(key=lambda r: r["effective_at"])
    return out


# SMM 聚合页 HTML：每品牌独立块，含品牌名 + 当日挂牌价（元/克）
_BRAND_NAMES = (
    "周大福", "老凤祥", "周生生", "中国黄金", "潮宏基",
    "六福珠宝", "老庙黄金", "菜百首饰", "谢瑞麟",
    "周大生", "中国珠宝",
)
def _cell_text(cell: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", cell))).strip()


def parse_smm_html(html: str) -> list[dict]:
    """从上海有色网聚合页解析多品牌当日挂牌价。

    返回列表，每条 ``{"brand":"周大福","price":962.0,"effective_at":"..."}``。
    空输入或未识别到品牌 → 返回 ``[]``。
    """
    out: list[dict] = []
    if not html:
        return []
    eff_default = datetime.now().astimezone().isoformat(timespec="seconds")
    seen: set[str] = set()
    for tr in re.findall(r"<tr[^>]*>.*?</tr>", html, flags=re.IGNORECASE | re.DOTALL):
        cells = [_cell_text(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, flags=re.IGNORECASE | re.DOTALL)]
        if len(cells) < 2:
            continue
        brand = next((b for b in _BRAND_NAMES if cells[0] == b), None)
        if not brand or brand in seen:
            continue
        product = cells[1]
        is_gold_product = bool(re.search(r"黄金|足金|金条|工艺金", product)) and not re.search(r"铂金|白金", product)
        if not is_gold_product and not re.fullmatch(r"[\d,.]+", product):
            continue
        price = None
        for cell in cells[1:]:
            try:
                candidate = float(cell.replace(",", ""))
            except ValueError:
                continue
            if _valid_price("smm", candidate):
                price = candidate
                break
        if price is None:
            continue
        date_match = re.search(r"(\d{4}-\d{2}-\d{2})", " ".join(cells))
        effective_at = date_match.group(1) + "T00:00:00+08:00" if date_match else eff_default
        out.append({"brand": brand, "price": round(price, 2), "effective_at": effective_at})
        seen.add(brand)
    if not out:
        # 退路：在整 HTML 里按品牌名 + 数字抓
        for brand in _BRAND_NAMES:
            for m in re.finditer(re.escape(brand) + r"[^\d]{0,80}?(\d{3,5}(?:\.\d+)?)", html):
                try:
                    price = float(m.group(1))
                except ValueError:
                    continue
                if not _valid_price("smm", price):
                    continue
                out.append({"brand": brand, "price": round(price, 2), "effective_at": eff_default})
                break  # 每个品牌只取第一个
    # 去重：每品牌保留首个
    seen: set[str] = set()
    dedup: list[dict] = []
    for it in out:
        if it["brand"] in seen:
            continue
        seen.add(it["brand"])
        dedup.append(it)
    return dedup


__all__ = [
    "CHANNELS",
    "CHANNEL_META",
    "PRICE_RANGE",
    "FetchResult",
    "load_seed_history",
    "load_seed_brands",
    "build_current",
    "build_history_series",
    "build_channels_meta",
    "parse_sge_table",
    "parse_shfe_kx",
    "parse_yahoo_csv",
    "parse_yahoo_chart",
    "parse_smm_html",
]
