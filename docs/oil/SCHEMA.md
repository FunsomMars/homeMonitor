# 油价数据库 Schema

数据库：`data/oil.db`（SQLite，独立文件，与 `telemetry.db` 物理隔离）。

---

## 1. 表结构

### 1.1 `oil_current` — 当前价（每省每油品一行，UPSERT）

| 字段 | 类型 | 说明 |
|---|---|---|
| province | TEXT NOT NULL | 省份，如 "江苏" |
| fuel_type | TEXT NOT NULL | 92 / 95 / 98 / 0 |
| price | REAL NOT NULL | 元/升 |
| effective_at | TEXT NOT NULL | 生效时间 ISO8601 |
| source | TEXT NOT NULL | jiangsu_fgw / national_fgw / eastmoney / seed |
| confidence | TEXT NOT NULL | official / derived / fallback |
| updated_at | TEXT NOT NULL | 入库时间 |
| PRIMARY KEY | (province, fuel_type) | |

### 1.2 `oil_history` — 历史曲线

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | |
| province | TEXT NOT NULL | |
| fuel_type | TEXT NOT NULL | |
| price | REAL NOT NULL | |
| effective_at | TEXT NOT NULL | 生效日期 |
| source | TEXT NOT NULL | |
| UNIQUE | (province, fuel_type, effective_at) | |

索引：`(province, fuel_type, effective_at)`。

### 1.3 `oil_adjust_event` — 调价事件

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | |
| effective_at | TEXT NOT NULL UNIQUE | 生效时间 |
| gasoline_change | INTEGER NOT NULL | 汽油 元/吨（可正可负） |
| diesel_change | INTEGER NOT NULL | 柴油 元/吨 |
| source | TEXT NOT NULL | national_fgw / seed |
| notice_url | TEXT | 公告链接 |
| created_at | TEXT NOT NULL | |

### 1.4 `oil_fetch_log` — 抓取日志

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | |
| source | TEXT NOT NULL | 抓取源 ID |
| started_at | TEXT NOT NULL | |
| finished_at | TEXT | |
| status | TEXT | ok / failed |
| message | TEXT | 错误信息 |
| rows_written | INTEGER DEFAULT 0 | |

### 1.5 `oil_source` — 数据源元信息

| 字段 | 类型 | 说明 |
|---|---|---|
| id | TEXT PK | eastmoney / jiangsu_fgw / national_fgw / seed |
| name | TEXT NOT NULL | |
| url | TEXT | |
| kind | TEXT NOT NULL | history / current / event |
| last_run_at | TEXT | |
| last_status | TEXT | |

---

## 2. 初始化 SQL

```sql
CREATE TABLE IF NOT EXISTS oil_current (
  province TEXT NOT NULL,
  fuel_type TEXT NOT NULL,
  price REAL NOT NULL,
  effective_at TEXT NOT NULL,
  source TEXT NOT NULL,
  confidence TEXT NOT NULL,
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
  UNIQUE(province, fuel_type, effective_at)
);
CREATE INDEX IF NOT EXISTS oil_history_lookup
  ON oil_history(province, fuel_type, effective_at);

CREATE TABLE IF NOT EXISTS oil_adjust_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  effective_at TEXT NOT NULL UNIQUE,
  gasoline_change INTEGER NOT NULL,
  diesel_change INTEGER NOT NULL,
  source TEXT NOT NULL,
  notice_url TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oil_fetch_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT,
  message TEXT,
  rows_written INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS oil_source (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  url TEXT,
  kind TEXT NOT NULL,
  last_run_at TEXT,
  last_status TEXT
);
```

---

## 3. 数据流

```
[东方财富/省发改委/国家发改委]
        │  HTTP (urllib)
        ▼
[oil_fetcher.py]  ──失败──> [seed JSON 兜底]
        │
        ▼
[oil_store.py: insert/upsert]
        │
        ▼
[data/oil.db]
        │
        ▼
[server/app.py: /api/oil/*]
        │
        ▼
[web/oil.html: ECharts]
```
