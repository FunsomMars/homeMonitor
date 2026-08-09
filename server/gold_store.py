"""黄金价格 SQLite DAO（独立 data/gold.db）。

表结构：
  - gold_current   : 每个 (channel, brand) 一行最新价；UPSERT
  - gold_history   : 每个 (channel, brand, effective_at) 一行；UNIQUE
  - gold_fetch_log : 抓取日志
  - gold_source    : 数据源元信息

API 入口：
  init_db() / upsert_current() / insert_history() / write_fetch_log()
  current(channel=None) / history(channel, brand, days) /
  channels_meta() / fetch_log(limit) / source_status()
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "gold.db"

from server.gold_format import CHANNEL_META, CHANNELS  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS gold_current (
    channel TEXT NOT NULL,
    brand   TEXT NOT NULL DEFAULT '',
    price   REAL NOT NULL,
    unit    TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    source  TEXT NOT NULL,
    confidence TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (channel, brand)
);

CREATE TABLE IF NOT EXISTS gold_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    brand   TEXT NOT NULL DEFAULT '',
    price   REAL NOT NULL,
    effective_at TEXT NOT NULL,
    source  TEXT NOT NULL,
    UNIQUE(channel, brand, effective_at)
);
CREATE INDEX IF NOT EXISTS gold_history_lookup
    ON gold_history(channel, brand, effective_at);

CREATE TABLE IF NOT EXISTS gold_fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source   TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status   TEXT,
    message  TEXT,
    rows_written INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS gold_source (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    url  TEXT,
    kind TEXT NOT NULL,
    last_run_at TEXT,
    last_status TEXT
);
"""


class GoldStore:
    def __init__(self, db_path: Path = DEFAULT_DB):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self._init_db()

    # ---------- 初始化 ----------

    def _init_db(self):
        with self.lock:
            self.db.executescript(SCHEMA)
            # 4 个数据源 seed 一次
            for ch in CHANNELS:
                meta = CHANNEL_META[ch]
                self.db.execute(
                    """INSERT OR IGNORE INTO gold_source (id, name, url, kind)
                       VALUES (?, ?, ?, ?)""",
                    (ch, meta["name"], None, meta["kind"]),
                )
            self.db.commit()

    # ---------- 写 ----------

    def upsert_current(self, channel: str, brand: str, price: float,
                       effective_at: str, source: str, confidence: str) -> int:
        unit = CHANNEL_META[channel]["unit"]
        with self.lock:
            cur = self.db.execute(
                "SELECT effective_at FROM gold_current WHERE channel=? AND brand=?",
                (channel, brand),
            ).fetchone()
            # 单调保留：effective_at 不倒退
            if cur and cur["effective_at"] and effective_at < cur["effective_at"]:
                # 数据源偶发返回未来时间时，不能让这条异常记录永久阻塞正常更新。
                try:
                    current_dt = datetime.fromisoformat(cur["effective_at"])
                    incoming_dt = datetime.fromisoformat(effective_at)
                    now_dt = datetime.now(current_dt.tzinfo or timezone.utc)
                    if not (current_dt > now_dt and incoming_dt <= now_dt):
                        self.db.commit()
                        return 0
                except ValueError:
                    self.db.commit()
                    return 0
            self.db.execute(
                """INSERT INTO gold_current
                (channel, brand, price, unit, effective_at, source, confidence, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(channel, brand) DO UPDATE SET
                    price=excluded.price,
                    unit=excluded.unit,
                    effective_at=excluded.effective_at,
                    source=excluded.source,
                    confidence=excluded.confidence,
                    updated_at=excluded.updated_at""",
                (channel, brand, round(float(price), 2), unit, effective_at,
                 source, confidence, now_iso()),
            )
            self.db.commit()
        return 1

    def insert_history(self, channel: str, brand: str, price: float,
                       effective_at: str, source: str) -> int:
        with self.lock:
            try:
                self.db.execute(
                    """INSERT INTO gold_history
                    (channel, brand, price, effective_at, source)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(channel, brand, effective_at) DO UPDATE SET
                        price=excluded.price,
                        source=excluded.source
                    WHERE gold_history.source=excluded.source
                          OR gold_history.source='seed'""",
                    (channel, brand, round(float(price), 2), effective_at, source),
                )
                self.db.commit()
                return self.db.total_changes
            except sqlite3.IntegrityError:
                return 0

    def write_fetch_log(self, source: str, started_at: str, finished_at: str,
                        status: str, message: str | None,
                        rows_written: int) -> None:
        with self.lock:
            self.db.execute(
                """INSERT INTO gold_fetch_log
                (source, started_at, finished_at, status, message, rows_written)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (source, started_at, finished_at, status, message, rows_written),
            )
            self.db.execute(
                """INSERT INTO gold_source (id, name, url, kind, last_run_at, last_status)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     last_run_at=excluded.last_run_at,
                     last_status=excluded.last_status""",
                (source, CHANNEL_META[source]["name"], None,
                 CHANNEL_META[source]["kind"], finished_at, status),
            )
            self.db.commit()

    # ---------- 读 ----------

    def current(self, channel: str | None = None) -> list[dict]:
        with self.lock:
            if channel:
                rows = self.db.execute(
                    "SELECT * FROM gold_current WHERE channel=? "
                    "ORDER BY brand ASC", (channel,)
                ).fetchall()
            else:
                rows = self.db.execute(
                    "SELECT * FROM gold_current ORDER BY channel ASC, brand ASC"
                ).fetchall()
        return [dict(r) for r in rows]

    def history(self, channel: str, brand: str, days: int) -> list[dict]:
        if channel not in CHANNELS:
            return []
        with self.lock:
            if days > 0:
                last = self.db.execute(
                    "SELECT MAX(effective_at) AS m FROM gold_history WHERE channel=? AND brand=?",
                    (channel, brand),
                ).fetchone()
                last_dt = None
                if last and last["m"]:
                    try:
                        last_dt = datetime.fromisoformat(last["m"])
                    except ValueError:
                        last_dt = None
                if last_dt:
                    cutoff = (last_dt - timedelta(days=days)).isoformat(timespec="seconds")
                    rows = self.db.execute(
                        "SELECT effective_at, price FROM gold_history "
                        "WHERE channel=? AND brand=? AND effective_at >= ? "
                        "ORDER BY effective_at ASC",
                        (channel, brand, cutoff),
                    ).fetchall()
                else:
                    rows = []
            else:
                rows = self.db.execute(
                    "SELECT effective_at, price FROM gold_history "
                    "WHERE channel=? AND brand=? "
                    "ORDER BY effective_at ASC",
                    (channel, brand),
                ).fetchall()
        return [{"date": r["effective_at"][:10], "value": r["price"]} for r in rows]

    def history_rows(self, channel: str | None = None) -> list[dict]:
        """返回完整历史行，供 API 与种子历史按日期合并。"""
        with self.lock:
            if channel:
                rows = self.db.execute(
                    "SELECT channel, brand, price, effective_at, source "
                    "FROM gold_history WHERE channel=? "
                    "ORDER BY channel ASC, brand ASC, effective_at ASC",
                    (channel,),
                ).fetchall()
            else:
                rows = self.db.execute(
                    "SELECT channel, brand, price, effective_at, source "
                    "FROM gold_history ORDER BY channel ASC, brand ASC, effective_at ASC"
                ).fetchall()
        return [dict(row) for row in rows]

    def history_multi_brand(self, channel: str, days: int) -> list[dict]:
        """SMM 多品牌专用：返回 ``[{brand, points: [[date, value], ...]}, ...]``。

        每个 brand 内部按 ``effective_at`` 升序；空 brand 列表会被丢弃。
        """
        if channel not in CHANNELS:
            return []
        with self.lock:
            brand_rows = self.db.execute(
                "SELECT DISTINCT brand FROM gold_history WHERE channel=? AND brand<>'' ORDER BY brand ASC",
                (channel,),
            ).fetchall()
            brands = [r["brand"] for r in brand_rows]
            out: list[dict] = []
            for bname in brands:
                if days > 0:
                    last = self.db.execute(
                        "SELECT MAX(effective_at) AS m FROM gold_history WHERE channel=? AND brand=?",
                        (channel, bname),
                    ).fetchone()
                    last_dt = None
                    if last and last["m"]:
                        try:
                            last_dt = datetime.fromisoformat(last["m"])
                        except ValueError:
                            last_dt = None
                    if last_dt:
                        cutoff = (last_dt - timedelta(days=days)).isoformat(timespec="seconds")
                        rows = self.db.execute(
                            "SELECT effective_at, price FROM gold_history "
                            "WHERE channel=? AND brand=? AND effective_at >= ? "
                            "ORDER BY effective_at ASC",
                            (channel, bname, cutoff),
                        ).fetchall()
                    else:
                        rows = []
                else:
                    rows = self.db.execute(
                        "SELECT effective_at, price FROM gold_history "
                        "WHERE channel=? AND brand=? ORDER BY effective_at ASC",
                        (channel, bname),
                    ).fetchall()
                points = [[r["effective_at"][:10], r["price"]] for r in rows]
                if points:
                    out.append({"brand": bname, "points": points})
        return out

    def channels_meta(self) -> list[dict]:
        with self.lock:
            sources = {r["id"]: dict(r) for r in self.db.execute(
                "SELECT * FROM gold_source").fetchall()}
            counts = {ch: 0 for ch in CHANNELS}
            for r in self.db.execute(
                "SELECT channel, COUNT(*) AS c FROM gold_history GROUP BY channel"
            ).fetchall():
                counts[r["channel"]] = r["c"]
        out: list[dict] = []
        for ch in CHANNELS:
            meta = CHANNEL_META[ch]
            src = sources.get(ch, {})
            out.append({
                "channel": ch,
                "name": meta["name"],
                "unit": meta["unit"],
                "kind": meta["kind"],
                "row_count": counts[ch],
                "last_run_at": src.get("last_run_at"),
                "last_status": src.get("last_status"),
            })
        return out

    def fetch_log(self, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM gold_fetch_log ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [dict(r) for r in rows]

    def source_status(self) -> dict[str, dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM gold_source").fetchall()
        return {r["id"]: dict(r) for r in rows}


__all__ = ["GoldStore", "DEFAULT_DB"]
