"""油价模块：seed 加载、格式校验、涨跌幅统计。"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED_DIR = ROOT / "data" / "seed"

PROVINCES = [
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
    "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
    "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
]
FUEL_TYPES = ("92", "95", "98", "0")


# 内部 source id → 中文显示名；空字符串表示不显示
_SOURCE_DISPLAY = {
    "national_fgw": "国家发改委",
    "jiangsu_fgw": "江苏省发改委",
    "eastmoney": "东方财富",
    "seed": "",          # 本地兜底数据不展示来源
    "demo": "演示数据",
}

def _display_source(src: str) -> str:
    return _SOURCE_DISPLAY.get(src or "", src or "")


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _valid_price(p) -> bool:
    try:
        v = float(p)
        return 3.0 <= v <= 20.0
    except (TypeError, ValueError):
        return False


def load_seed_history() -> dict:
    path = SEED_DIR / "oil_history.json"
    if not path.exists():
        return {"province": "江苏", "rows": []}
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for it in raw.get("history", []):
        if it.get("fuel_type") not in FUEL_TYPES:
            continue
        if not _valid_price(it.get("price")):
            continue
        eff = _parse_iso(it.get("effective_at", ""))
        if not eff:
            continue
        rows.append({
            "province": raw.get("province", "江苏"),
            "fuel_type": it["fuel_type"],
            "price": round(float(it["price"]), 2),
            "effective_at": eff.isoformat(timespec="seconds"),
            "source": raw.get("source", "seed"),
            "source_display": _display_source(raw.get("source", "seed")),
        })
    rows.sort(key=lambda r: (r["fuel_type"], r["effective_at"]))
    return {"province": raw.get("province", "江苏"), "rows": rows}


def load_seed_adjustments() -> list[dict]:
    path = SEED_DIR / "oil_adjustments.json"
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for e in raw.get("events", []):
        eff = _parse_iso(e.get("effective_at", ""))
        if not eff:
            continue
        try:
            gc = int(e["gasoline_change"]); dc = int(e["diesel_change"])
        except (KeyError, TypeError, ValueError):
            continue
        src = e.get("source", "seed")
        out.append({
            "effective_at": eff.isoformat(timespec="seconds"),
            "gasoline_change": gc,
            "diesel_change": dc,
            "source": src,
            "source_display": _display_source(src),
            "notice_url": e.get("notice_url"),
        })
    out.sort(key=lambda r: r["effective_at"], reverse=True)
    return out


def load_seed_map() -> dict:
    path = SEED_DIR / "oil_map.json"
    if not path.exists():
        return {"fuel_type": "92", "data": []}
    raw = json.loads(path.read_text(encoding="utf-8"))
    data = [d for d in raw.get("data", []) if d.get("name") in PROVINCES and _valid_price(d.get("value"))]
    prices = [d["value"] for d in data]
    return {
        "fuel_type": raw.get("fuel_type", "92"),
        "as_of": raw.get("as_of"),
        "min": round(min(prices), 2) if prices else None,
        "max": round(max(prices), 2) if prices else None,
        "data": data,
    }


def build_current(province: str = "江苏", history_rows=None, adjustments=None) -> dict:
    if history_rows is None:
        history_rows = load_seed_history()["rows"]
    if adjustments is None:
        adjustments = load_seed_adjustments()
    if province not in PROVINCES:
        province = "江苏"

    latest_per_fuel: dict[str, dict] = {}
    for r in history_rows:
        cur = latest_per_fuel.get(r["fuel_type"])
        if not cur or r["effective_at"] > cur["effective_at"]:
            latest_per_fuel[r["fuel_type"]] = r

    items = []
    for ft in FUEL_TYPES:
        row = latest_per_fuel.get(ft)
        if not row:
            continue
        items.append({
            "type": ft,
            "price": row["price"],
            "effective_at": row["effective_at"],
            "source": row["source"],
            "source_display": row.get("source_display") or _display_source(row["source"]),
            "confidence": "fallback" if row["source"] == "seed" else "official",
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })

    last_adj = adjustments[0] if adjustments else None
    if last_adj is not None and "source_display" not in last_adj:
        last_adj = {**last_adj, "source_display": _display_source(last_adj.get("source", ""))}
    return {
        "province": province,
        "as_of": items[0]["effective_at"] if items else None,
        "items": items,
        "last_adjustment": last_adj,
    }


def build_history_series(province: str, fuel: str, days: int, history_rows=None, adjustments=None) -> dict:
    from datetime import timedelta
    if fuel not in FUEL_TYPES:
        fuel = "92"
    if history_rows is None:
        history_rows = load_seed_history()["rows"]
    if adjustments is None:
        adjustments = load_seed_adjustments()

    points_by_date: dict[str, dict] = {}
    for row in history_rows:
        if row["fuel_type"] != fuel:
            continue
        date = row["effective_at"][:10]
        existing = points_by_date.get(date)
        # 实时源优先于同日种子值；同源时保留时间更晚的一条。
        is_live = row.get("source") not in (None, "seed")
        old_is_live = existing and existing.get("source") not in (None, "seed")
        if (existing is None or (is_live and not old_is_live)
                or row["effective_at"] >= existing["effective_at"]):
            points_by_date[date] = row
    points = sorted(points_by_date.values(), key=lambda row: row["effective_at"])
    if days > 0 and points:
        last_dt = datetime.fromisoformat(points[-1]["effective_at"])
        cutoff = last_dt - timedelta(days=days)
        points = [p for p in points if datetime.fromisoformat(p["effective_at"]) >= cutoff]

    series = [{"date": p["effective_at"][:10], "value": p["price"]} for p in points]
    start_date = series[0]["date"] if series else None
    end_date = series[-1]["date"] if series else None
    adj_by_date: dict[str, dict] = {}
    for adjustment in adjustments:
        date = adjustment["effective_at"][:10]
        if start_date and start_date <= date <= end_date and date not in adj_by_date:
            adj_by_date[date] = {
                "date": date,
                "gasoline_change": adjustment["gasoline_change"],
                "diesel_change": adjustment["diesel_change"],
            }
    adj_marks = sorted(adj_by_date.values(), key=lambda item: item["date"])

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
        "province": province, "type": fuel, "days": days,
        "series": series, "adjustments": adj_marks, "stats": stats,
    }


# ----- 抓取协议 -----

class FetchResult:
    def __init__(self, source: str, ok: bool, rows: int = 0,
                 error: str | None = None, data: list[dict] | None = None):
        self.source = source; self.ok = ok; self.rows = rows; self.error = error
        self.data = data or []


def parse_eastmoney_row(row: dict) -> dict | None:
    try:
        prov = str(row["province"]).strip()
        ft = str(row["fuel_type"]).strip()
        price = float(row["price"])
        eff = str(row["effective_at"])
    except (KeyError, TypeError, ValueError):
        return None
    if ft not in FUEL_TYPES or not _valid_price(price) or not _parse_iso(eff):
        return None
    return {"province": prov, "fuel_type": ft, "price": round(price, 2),
            "effective_at": eff, "source": "eastmoney",
            "source_display": _display_source("eastmoney")}


# 调价通知解析：支持多种表述
# 1) "汽油价格每吨提高300元，柴油价格每吨提高290元"
# 2) "汽油、柴油价格每吨分别提高300元和290元"
# 3) "汽柴油价格每吨分别降低145元和140元"
_NUMBER = r"(\d+)"
_GAS_UP = re.compile(r"汽油[^\d]{0,8}?(?:提高|上调)\s*" + _NUMBER)
_GAS_DN = re.compile(r"汽油[^\d]{0,8}?(?:降低|下调)\s*" + _NUMBER)
_DSL_UP = re.compile(r"柴油[^\d]{0,8}?(?:提高|上调)\s*" + _NUMBER)
_DSL_DN = re.compile(r"柴油[^\d]{0,8}?(?:降低|下调)\s*" + _NUMBER)
_PAIR = re.compile(
    r"(?:汽柴油|汽[、，]?柴油|汽油[、，]柴油)"
    r"[^\d]{0,20}?(?:分别)?(?:提高|上调|降低|下调)\s*"
    r"(\d+)\s*元[^\d]{0,8}?(\d+)\s*元"
)


def parse_adjust_notice(text: str) -> dict | None:
    if not text:
        return None
    # 优先匹配 "汽油、柴油 ... 300元 ... 290元" 格式
    pair = _PAIR.search(text)
    if pair:
        sign = 1 if any(word in text[max(0, pair.start() - 6):pair.end()] for word in ("提高", "上调")) else -1
        # sign 也要看是 提高 还是 降低 —— 重新检查
        # 在配对匹配范围内取方向
        around = text[max(0, pair.start() - 8):pair.end()]
        sign = 1 if "提高" in around or "上调" in around else -1
        return {"gasoline_change": sign * int(pair.group(1)),
                "diesel_change": sign * int(pair.group(2))}
    # 否则按独立词匹配
    gas = _GAS_UP.search(text) or _GAS_DN.search(text)
    dsl = _DSL_UP.search(text) or _DSL_DN.search(text)
    if not gas and not dsl:
        return None
    gasoline_change = int(gas.group(1)) * (1 if "提高" in gas.group(0) or "上调" in gas.group(0) else -1) if gas else 0
    diesel_change = int(dsl.group(1)) * (1 if "提高" in dsl.group(0) or "上调" in dsl.group(0) else -1) if dsl else gasoline_change
    return {"gasoline_change": gasoline_change, "diesel_change": diesel_change}
