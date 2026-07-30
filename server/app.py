from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .gold_format import CHANNELS, build_channels_meta, build_current, build_history_series
from .gold_store import DEFAULT_DB as DEFAULT_GOLD_DB, GoldStore
from .xiaomi_mjwsd06 import decode_advertisement

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "devices.json"
DEFAULT_DB = ROOT / "data" / "telemetry.db"
WEB_ROOT = ROOT / "web"
LOG = logging.getLogger("home-monitor")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: Path, config_path: Path, ingest_token: str | None = None,
                 gold_db_path: Path | None = None):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.Lock()
        self.proxies = self._load_proxies(config_path)
        config_network = self._read_config(config_path).get("network", {})
        self.ingest_token = ingest_token if ingest_token is not None else str(config_network.get("ingest_token", ""))
        self.proxy_status = {
            proxy_id: {
                "id": proxy_id,
                "name": item["name"],
                "serial": item["serial"],
                "enabled": item["enabled"],
                "connected": False,
                "last_seen": None,
                "error": None,
            }
            for proxy_id, item in self.proxies.items()
        }
        self.devices = self._load_devices(config_path)
        self.pending_readings: dict[str, dict[str, object]] = {}
        # 黄金子模块（独立 SQLite，与 telemetry.db 物理隔离）
        self.gold = GoldStore(gold_db_path or DEFAULT_GOLD_DB)
        self._init_db()

    @staticmethod
    def _read_config(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    @classmethod
    def _load_proxies(cls, path: Path) -> dict[str, dict]:
        data = cls._read_config(path)
        result = {}
        for index, item in enumerate(data.get("proxies", []), start=1):
            proxy_id = str(item.get("id") or f"proxy-{index}")
            serial = str(item.get("serial", ""))
            if serial:
                result[proxy_id] = {
                    "id": proxy_id,
                    "name": item.get("name") or proxy_id,
                    "serial": serial,
                    "enabled": bool(item.get("enabled", True)),
                }
        return result

    @staticmethod
    def _load_devices(path: Path) -> dict[str, dict]:
        data = Store._read_config(path)
        result = {}
        for item in data.get("devices", []):
            address = str(item.get("address", "")).upper()
            if address:
                result[address] = {
                    "address": address,
                    "name": item.get("name") or address,
                    "enabled": bool(item.get("enabled", True)),
                    "bindkey": item.get("bindkey", ""),
                    "proxy_id": item.get("proxy_id", ""),
                }
        return result

    def set_proxy_status(self, proxy_id: str, **updates):
        with self.lock:
            status = self.proxy_status.setdefault(proxy_id, {"id": proxy_id})
            status.update(updates)

    def proxy_snapshot(self) -> list[dict]:
        with self.lock:
            return [dict(item) for item in self.proxy_status.values()]

    def register_proxy(self, proxy_id: str, name: str = "", serial: str = ""):
        if not proxy_id:
            return
        with self.lock:
            status = self.proxy_status.setdefault(proxy_id, {
                "id": proxy_id,
                "name": name or proxy_id,
                "serial": serial,
                "enabled": True,
                "connected": False,
                "last_seen": None,
                "error": None,
            })
            if name:
                status["name"] = name

    def _init_db(self):
        with self.lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS advertisements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at TEXT NOT NULL,
                    address TEXT NOT NULL,
                    rssi INTEGER,
                    name TEXT,
                    manufacturer_data TEXT NOT NULL DEFAULT '{}',
                    service_data TEXT NOT NULL DEFAULT '{}',
                    source TEXT NOT NULL DEFAULT 'esp32-usb',
                    raw_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observed_at TEXT NOT NULL,
                    address TEXT NOT NULL,
                    room TEXT NOT NULL,
                    temperature_c REAL NOT NULL,
                    humidity_pct REAL NOT NULL,
                    battery_pct INTEGER,
                    rssi INTEGER,
                    protocol TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'esp32-usb'
                );
                CREATE INDEX IF NOT EXISTS readings_observed_idx ON readings(observed_at);
                CREATE INDEX IF NOT EXISTS readings_address_idx ON readings(address, observed_at);
                CREATE INDEX IF NOT EXISTS advertisements_observed_idx ON advertisements(observed_at);
                """
            )
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(advertisements)")}
            if "source" not in columns:
                self.db.execute("ALTER TABLE advertisements ADD COLUMN source TEXT NOT NULL DEFAULT 'esp32-usb'")
            self.db.commit()

    def add_advertisement(self, advertisement: dict, source: str = "esp32-usb") -> bool:
        address = str(advertisement.get("address", "")).upper()
        if not address:
            return False
        configured = self.devices.get(address)
        if configured and not configured["enabled"]:
            return False
        observed_at = advertisement.get("observed_at") or advertisement.get("ts")
        if isinstance(observed_at, (int, float)):
            # The first firmware uses uptime milliseconds/seconds because the
            # USB-only proxy has no clock. Host receipt time is authoritative
            # until the proxy is given Wi-Fi/NTP support.
            observed_at = (datetime.fromtimestamp(observed_at, timezone.utc).isoformat(timespec="seconds")
                           if observed_at >= 1_000_000_000 else now_iso())
        observed_at = str(observed_at or now_iso())
        advertisement = dict(advertisement)
        advertisement["address"] = address
        advertisement["observed_at"] = observed_at
        decoded = decode_advertisement(advertisement, configured.get("bindkey") if configured else None)
        self.set_proxy_status(source, connected=True, last_seen=observed_at, error=None)
        with self.lock:
            self.db.execute(
                """INSERT INTO advertisements
                (observed_at,address,rssi,name,manufacturer_data,service_data,source,raw_json)
                VALUES (?,?,?,?,?,?,?,?)""",
                (observed_at, address, advertisement.get("rssi"), advertisement.get("name", ""),
                 json.dumps(advertisement.get("manufacturer_data", {}), separators=(",", ":")),
                 json.dumps(advertisement.get("service_data", {}), separators=(",", ":")),
                 source,
                 json.dumps(advertisement, separators=(",", ":"))),
            )
            if decoded and configured:
                pending = self.pending_readings.setdefault(address, {})
                if decoded.temperature_c is not None:
                    pending["temperature_c"] = decoded.temperature_c
                if decoded.humidity_pct is not None:
                    pending["humidity_pct"] = decoded.humidity_pct
                if decoded.battery_pct is not None:
                    pending["battery_pct"] = decoded.battery_pct
                if "temperature_c" not in pending or "humidity_pct" not in pending:
                    self.db.commit()
                    return True
                self.db.execute(
                    """INSERT INTO readings
                    (observed_at,address,room,temperature_c,humidity_pct,battery_pct,rssi,protocol,source)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (observed_at, address, configured["name"], pending["temperature_c"],
                     pending["humidity_pct"], pending.get("battery_pct"), advertisement.get("rssi"),
                     decoded.protocol, source),
                )
                self.pending_readings[address] = {}
            self.db.commit()
        return True

    def readings(self, hours: float = 24, address: str | None = None) -> list[dict]:
        hours = min(max(hours, 0.25), 24 * 366)
        cutoff = time.time() - hours * 3600
        cutoff_iso = datetime.fromtimestamp(cutoff, timezone.utc).isoformat(timespec="seconds")
        sql = "SELECT * FROM readings WHERE observed_at >= ?"
        args: list[object] = [cutoff_iso]
        if address:
            sql += " AND address = ?"
            args.append(address.upper())
        sql += " ORDER BY observed_at ASC"
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, args).fetchall()]

    def latest(self) -> list[dict]:
        result = []
        for address, device in self.devices.items():
            with self.lock:
                row = self.db.execute(
                    "SELECT * FROM readings WHERE address = ? ORDER BY observed_at DESC LIMIT 1", (address,)
                ).fetchone()
            public_device = {key: value for key, value in device.items() if key != "bindkey"}
            result.append({**public_device, "latest": dict(row) if row else None})
        return result

    def recent_advertisements(self, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT id, observed_at, address, rssi, name, manufacturer_data, service_data, source FROM advertisements ORDER BY id DESC LIMIT ?",
                (min(max(limit, 1), 500),),
            ).fetchall()
        return [dict(row) for row in rows]


class SerialReader(threading.Thread):
    daemon = True

    def __init__(self, port: str, store: Store, proxy_id: str | None = None):
        super().__init__(name=f"esp32-serial-reader-{proxy_id or port}")
        self.port = port
        self.store = store
        self.proxy_id = proxy_id or port
        self.stop_event = threading.Event()
        self.store.register_proxy(self.proxy_id, serial=port)

    def run(self):
        LOG.info("opening ESP32 proxy %s on %s", self.proxy_id, self.port)
        self.store.set_proxy_status(self.proxy_id, connected=False, error=None)
        while not self.stop_event.is_set():
            try:
                with open(self.port, "rb", buffering=0) as serial:
                    self._configure(serial.fileno())
                    self.store.set_proxy_status(self.proxy_id, connected=True, error=None)
                    while not self.stop_event.is_set():
                        line = serial.readline()
                        if not line:
                            break
                        try:
                            message = json.loads(line.decode("utf-8", errors="replace"))
                        except json.JSONDecodeError:
                            LOG.debug("ignoring non-JSON serial line: %r", line[:200])
                            continue
                        if message.get("type") == "advertisement":
                            self.store.add_advertisement(message, source=self.proxy_id)
            except (FileNotFoundError, PermissionError, OSError) as exc:
                self.store.set_proxy_status(self.proxy_id, connected=False, error=str(exc))
                LOG.warning("ESP32 proxy %s unavailable (%s); retrying in 3s", self.proxy_id, exc)
                self.stop_event.wait(3)

    @staticmethod
    def _configure(fd: int):
        # macOS device settings; failures are harmless when the board already
        # has the expected USB CDC configuration.
        try:
            import termios
            attrs = termios.tcgetattr(fd)
            attrs[4] = termios.B115200
            attrs[5] = termios.B115200
            attrs[2] = attrs[2] | termios.CLOCAL | termios.CREAD
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
        except (ImportError, OSError, AttributeError):
            pass


class DemoReader(threading.Thread):
    daemon = True

    def __init__(self, store: Store):
        super().__init__(name="demo-reader")
        self.store = store
        self.stop_event = threading.Event()

    def run(self):
        import math
        while not self.stop_event.is_set():
            stamp = time.time()
            for index, address in enumerate(self.store.devices):
                temp = 22.5 + index * 1.2 + math.sin(stamp / 1800 + index) * 1.5
                humidity = 48 + index * 5 + math.cos(stamp / 2400 + index) * 8
                self.store.add_advertisement({
                    "type": "advertisement", "ts": stamp, "address": address, "rssi": -50 - index * 8,
                    "service_data": {"fe95": ""},
                    "demo_reading": {"temperature_c": round(temp, 2), "humidity_pct": round(humidity, 2)},
                })
                # Demo readings bypass the unknown production decoder.
                configured = self.store.devices[address]
                with self.store.lock:
                    self.store.db.execute(
                        """INSERT INTO readings
                        (observed_at,address,room,temperature_c,humidity_pct,battery_pct,rssi,protocol,source)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                        (datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds"), address,
                         configured["name"], round(temp, 2), round(humidity, 2), 87, -50 - index * 8,
                         "demo", "demo"),
                    )
                    self.store.db.commit()
            self.stop_event.wait(60)


def json_response(handler: BaseHTTPRequestHandler, payload: object, status: int = 200):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    store: Store

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/api/health":
            return json_response(self, {"ok": True, "service": "home-monitor", "time": now_iso()})
        if parsed.path == "/api/devices":
            return json_response(self, self.store.latest())
        if parsed.path == "/api/proxies":
            return json_response(self, self.store.proxy_snapshot())
        if parsed.path == "/api/readings":
            try:
                hours = float(query.get("hours", [24])[0])
            except ValueError:
                hours = 24
            address = query.get("address", [None])[0]
            return json_response(self, {"readings": self.store.readings(hours, address), "hours": hours})
        if parsed.path == "/api/advertisements":
            return json_response(self, {"advertisements": self.store.recent_advertisements()})
        # ---- 黄金监控 ----
        if parsed.path == "/api/gold/current":
            return json_response(self, build_current())
        if parsed.path == "/api/gold/channels":
            return json_response(self, build_channels_meta())
        if parsed.path == "/api/gold/history":
            channel = query.get("channel", ["sge"])[0]
            if channel not in CHANNELS:
                channel = "sge"
            brand = query.get("brand", [""])[0]
            try:
                days = int(query.get("days", ["365"])[0])
            except ValueError:
                days = 365
            days = max(0, min(days, 3650))
            # 优先用 DB 真实历史；空时回退 seed
            db_series = self.store.gold.history(channel, brand, days)
            if not db_series:
                from server.gold_format import load_seed_history as _seed_loader
                seed_hist = _seed_loader()
                payload = build_history_series(channel, brand, days, seed_history=seed_hist)
            else:
                from server.gold_format import CHANNEL_META as _gold_meta
                meta = _gold_meta[channel]
                stats = None
                if db_series:
                    first, last = db_series[0], db_series[-1]
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
                payload = {
                    "channel": channel,
                    "brand": brand,
                    "name": meta["name"],
                    "unit": meta["unit"],
                    "days": days,
                    "series": db_series,
                    "stats": stats,
                }
            return json_response(self, payload)
        if parsed.path == "/api/gold/health":
            return json_response(self, {
                "channels": self.store.gold.channels_meta(),
                "fetch_log": self.store.gold.fetch_log(20),
                "time": now_iso(),
            })
        return self._static(parsed.path)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/ingest":
            # ---- 黄金手动刷新 ----
            if parsed.path == "/api/gold/refresh":
                configured_token = self.store.ingest_token
                supplied_token = self.headers.get("X-Home-Monitor-Token", "")
                authorization = self.headers.get("Authorization", "")
                if authorization.startswith("Bearer "):
                    supplied_token = authorization[7:]
                if configured_token and supplied_token != configured_token:
                    return json_response(self, {"error": "unauthorized"}, 401)
                # 同步跑一次 4 个 fetcher；不影响后台守护线程节奏
                from server.gold_fetcher.shfe import ShfeFuturesFetcher
                from server.gold_fetcher.sge import SgeCurrentFetcher
                from server.gold_fetcher.smm import SmmBrandFetcher
                from server.gold_fetcher.yahoo import YahooGoldFetcher
                from server.gold_format import parse_shfe_kx, parse_sge_table, parse_smm_html, parse_yahoo_csv
                from server.gold_fetcher.yahoo import build_url as yahoo_url
                from server.gold_fetcher.sge import LIST_URL as SGE_URL
                from server.gold_fetcher.shfe import LIST_URL as SHFE_URL
                from server.gold_fetcher.smm import LIST_URL as SMM_URL
                import urllib.request as _ur

                results: dict[str, dict] = {}
                for ch, fetcher, url, parser in (
                    ("sge",   SgeCurrentFetcher(),   SGE_URL,  parse_sge_table),
                    ("shfe",  ShfeFuturesFetcher(),  SHFE_URL, parse_shfe_kx),
                    ("yahoo", YahooGoldFetcher(),    yahoo_url(), parse_yahoo_csv),
                    ("smm",   SmmBrandFetcher(),    SMM_URL,  parse_smm_html),
                ):
                    started = now_iso()
                    try:
                        req = _ur.Request(url, headers={"User-Agent": "homeMonitor-gold/1.0"})
                        with _ur.urlopen(req, timeout=8) as resp:  # noqa: S310
                            body = resp.read().decode("utf-8", errors="replace")
                        rows = parser(body)
                        for r in rows:
                            self.store.gold.upsert_current(
                                channel=ch, brand=r.get("brand", ""),
                                price=r["price"], effective_at=r["effective_at"],
                                source=ch, confidence="official",
                            )
                            self.store.gold.insert_history(
                                channel=ch, brand=r.get("brand", ""),
                                price=r["price"], effective_at=r["effective_at"],
                                source=ch,
                            )
                        results[ch] = {"ok": True, "rows": len(rows)}
                        self.store.gold.write_fetch_log(ch, started, now_iso(), "ok", None, len(rows))
                    except Exception as exc:
                        results[ch] = {"ok": False, "error": str(exc)}
                        self.store.gold.write_fetch_log(ch, started, now_iso(), "failed", str(exc), 0)
                return json_response(self, {"ok": True, "results": results})
            return json_response(self, {"error": "not found"}, 404)
        configured_token = self.store.ingest_token
        supplied_token = self.headers.get("X-Home-Monitor-Token", "")
        authorization = self.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            supplied_token = authorization[7:]
        if configured_token and supplied_token != configured_token:
            return json_response(self, {"error": "unauthorized"}, 401)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 1024 * 1024:
            return json_response(self, {"error": "invalid content length"}, 413)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return json_response(self, {"error": "invalid json"}, 400)
        if not isinstance(payload, dict):
            return json_response(self, {"error": "payload must be an object"}, 400)
        proxy_id = str(payload.get("proxy_id", "")).strip()
        if not proxy_id or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", proxy_id):
            return json_response(self, {"error": "invalid proxy_id"}, 400)
        if not isinstance(payload.get("advertisements"), list):
            return json_response(self, {"error": "advertisements must be a list"}, 400)
        self.store.register_proxy(proxy_id)
        accepted = 0
        for item in payload["advertisements"]:
            if isinstance(item, dict) and item.get("type") == "advertisement":
                accepted += int(self.store.add_advertisement(item, source=proxy_id))
        self.store.set_proxy_status(proxy_id, connected=True, last_seen=now_iso(), error=None)
        return json_response(self, {"ok": True, "proxy_id": proxy_id, "accepted": accepted})

    def _static(self, path: str):
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        target = (WEB_ROOT / relative).resolve()
        if WEB_ROOT not in target.parents or not target.is_file():
            return json_response(self, {"error": "not found"}, 404)
        mime = ".js", "application/javascript"
        content_type = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}.get(target.suffix, "application/octet-stream")
        data = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        LOG.info("%s - %s", self.address_string(), fmt % args)


def main():
    parser = argparse.ArgumentParser(description="Local home temperature monitor")
    parser.add_argument("--host", default=os.getenv("HOME_MONITOR_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("HOME_MONITOR_PORT", "8787")))
    parser.add_argument("--serial", action="append", help="ESP32 serial port; repeat for multiple proxies")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--gold-db", type=Path, default=DEFAULT_GOLD_DB,
                        help="gold prices sqlite (independent file)")
    parser.add_argument("--ingest-token", default=os.getenv("HOME_MONITOR_INGEST_TOKEN"))
    parser.add_argument("--no-gold-scheduler", action="store_true",
                        help="disable background gold fetcher daemon")
    parser.add_argument("--demo", action="store_true", help="generate synthetic readings")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    store = Store(args.db, args.config, ingest_token=args.ingest_token,
                  gold_db_path=args.gold_db)
    readers: list[threading.Thread] = []
    if args.demo:
        readers.append(DemoReader(store))
    else:
        serials = args.serial
        if serials is None:
            serials = [value for value in os.getenv("HOME_MONITOR_SERIAL", "").split(",") if value]
        proxy_specs = list(store.proxies.values()) if not serials else [
            {"id": Path(port).name or f"proxy-{index}", "serial": port, "name": Path(port).name,
             "enabled": True}
            for index, port in enumerate(serials, start=1)
        ]
        for proxy in proxy_specs:
            if proxy["enabled"]:
                store.proxy_status.setdefault(proxy["id"], {
                    "id": proxy["id"], "name": proxy["name"], "serial": proxy["serial"],
                    "enabled": True, "connected": False, "last_seen": None, "error": None,
                })
                readers.append(SerialReader(proxy["serial"], store, proxy["id"]))
    # 黄金后台调度（默认开启；demo 模式不跑，避免外部请求污染日志）
    if not args.demo and not args.no_gold_scheduler:
        from .gold_fetcher.scheduler import GoldScheduler
        sched = GoldScheduler(store.gold)
        sched.start()
        readers.append(sched)
    for reader in readers:
        reader.start()
    Handler.store = store
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    if args.host not in {"127.0.0.1", "localhost"} and not store.ingest_token:
        LOG.warning("HTTP ingest is listening beyond localhost without an ingest token")
    LOG.info("home monitor listening on http://%s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        for reader in readers:
            if hasattr(reader, "stop_event"):
                reader.stop_event.set()


if __name__ == "__main__":
    main()