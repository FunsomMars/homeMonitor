"""油价实时抓取结果的独立 SQLite 存储。"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from server.oil_format import _display_source

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "oil.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS oil_current (
    province TEXT NOT NULL,
    fuel_type TEXT NOT NULL,
    price REAL NOT NULL,
    effective_at TEXT NOT NULL,
    source TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (province, fuel_type)
);
CREATE TABLE IF NOT EXISTS oil_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    province TEXT NOT NULL,
    fuel_type TEXT NOT NULL,
    price REAL NOT NULL,
    effective_at TEXT NOT NULL,
    source TEXT NOT NULL,
    UNIQUE (province, fuel_type, effective_at)
);
CREATE INDEX IF NOT EXISTS oil_history_lookup
  ON oil_history(province, fuel_type, effective_at);
CREATE TABLE IF NOT EXISTS oil_adjust_event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    effective_at TEXT NOT NULL UNIQUE,
    gasoline_change INTEGER NOT NULL,
    diesel_change INTEGER NOT NULL,
    source TEXT NOT NULL,
    notice_url TEXT
);
CREATE TABLE IF NOT EXISTS oil_fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    rows_written INTEGER NOT NULL DEFAULT 0
);
"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class OilStore:
    def __init__(self, db_path: Path = DEFAULT_DB):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    def upsert_current(self, row: dict) -> int:
        values = (
            row["province"], row["fuel_type"], round(float(row["price"]), 2),
            row["effective_at"], row.get("source", "jiangsu_fgw"), now_iso(),
        )
        with self.lock:
            cur = self.db.execute(
                "SELECT effective_at FROM oil_current WHERE province=? AND fuel_type=?",
                values[:2],
            ).fetchone()
            if cur and cur["effective_at"] >= values[3]:
                return 0
            self.db.execute(
                """INSERT INTO oil_current
                (province, fuel_type, price, effective_at, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(province, fuel_type) DO UPDATE SET
                  price=excluded.price, effective_at=excluded.effective_at,
                  source=excluded.source, updated_at=excluded.updated_at""",
                values,
            )
            self.db.execute(
                """INSERT OR IGNORE INTO oil_history
                (province, fuel_type, price, effective_at, source)
                VALUES (?, ?, ?, ?, ?)""",
                values[:5],
            )
            self.db.commit()
        return 1

    def rows(self, province: str, fuel_type: str | None = None, days: int = 0) -> list[dict]:
        with self.lock:
            sql = "SELECT province, fuel_type, price, effective_at, source FROM oil_history WHERE province=?"
            args: list[object] = [province]
            if fuel_type:
                sql += " AND fuel_type=?"
                args.append(fuel_type)
            if days > 0:
                last = self.db.execute(
                    "SELECT MAX(effective_at) AS m FROM oil_history WHERE province=?",
                    (province,),
                ).fetchone()
                if last and last["m"]:
                    try:
                        cutoff = datetime.fromisoformat(last["m"]) - timedelta(days=days)
                        sql += " AND effective_at>=?"
                        args.append(cutoff.isoformat(timespec="seconds"))
                    except ValueError:
                        pass
            sql += " ORDER BY effective_at ASC"
            result = [dict(r) for r in self.db.execute(sql, args).fetchall()]
        for row in result:
            row["source_display"] = _display_source(row["source"])
        return result

    def adjustments(self, limit: int = 10) -> list[dict]:
        with self.lock:
            rows = [dict(r) for r in self.db.execute(
                "SELECT effective_at, gasoline_change, diesel_change, source, notice_url "
                "FROM oil_adjust_event ORDER BY effective_at DESC LIMIT ?", (limit,)
            ).fetchall()]
        for row in rows:
            row["source_display"] = _display_source(row["source"])
        return rows

    def upsert_adjustment(self, event: dict) -> int:
        with self.lock:
            self.db.execute(
                """INSERT INTO oil_adjust_event
                (effective_at, gasoline_change, diesel_change, source, notice_url)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(effective_at) DO UPDATE SET
                  gasoline_change=excluded.gasoline_change,
                  diesel_change=excluded.diesel_change,
                  source=excluded.source, notice_url=excluded.notice_url""",
                (event["effective_at"], int(event["gasoline_change"]),
                 int(event["diesel_change"]), event.get("source", "national_fgw"),
                 event.get("notice_url")),
            )
            self.db.commit()
        return 1

    def write_fetch_log(self, source: str, started_at: str, finished_at: str,
                        status: str, message: str | None, rows_written: int) -> None:
        with self.lock:
            self.db.execute(
                "INSERT INTO oil_fetch_log(source, started_at, finished_at, status, message, rows_written) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (source, started_at, finished_at, status, message, rows_written),
            )
            self.db.commit()

    def close(self) -> None:
        with self.lock:
            self.db.close()


__all__ = ["OilStore", "DEFAULT_DB"]
