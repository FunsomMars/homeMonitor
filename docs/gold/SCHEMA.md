# 黄金监控 — 数据库 Schema

数据库：`data/gold.db`（SQLite，独立于 telemetry.db / oil.db）。

## 表结构

### `gold_current` — 4 个 channel 的最新价

```sql
CREATE TABLE gold_current (
  channel     TEXT NOT NULL,                 -- sge / shfe / yahoo / smm
  brand       TEXT NOT NULL DEFAULT '',      -- SMM 多品牌时填品牌名；其余为空
  price       REAL NOT NULL,                 -- 主数值
  unit        TEXT NOT NULL,                 -- 元/克 或 美元/盎司
  effective_at TEXT NOT NULL,                -- 业务时间（ISO8601）
  source      TEXT NOT NULL,                 -- sge / shfe / yahoo / smm / seed
  confidence  TEXT NOT NULL,                 -- official / fallback
  updated_at  TEXT NOT NULL,                 -- 入库时间（ISO8601）
  PRIMARY KEY (channel, brand)
);
```

### `gold_history` — 历史曲线（日线）

```sql
CREATE TABLE gold_history (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  channel      TEXT NOT NULL,
  brand        TEXT NOT NULL DEFAULT '',
  price        REAL NOT NULL,
  effective_at TEXT NOT NULL,
  source       TEXT NOT NULL,
  UNIQUE(channel, brand, effective_at)
);
CREATE INDEX gold_history_lookup ON gold_history(channel, brand, effective_at);
```

### `gold_fetch_log` — 抓取日志

```sql
CREATE TABLE gold_fetch_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source   TEXT NOT NULL,                    -- sge / shfe / yahoo / smm
  status   TEXT NOT NULL,                    -- ok / fail
  rows     INTEGER NOT NULL DEFAULT 0,
  error    TEXT,
  duration REAL NOT NULL,                    -- 秒
  ran_at   TEXT NOT NULL
);
CREATE INDEX gold_fetch_log_ran_at ON gold_fetch_log(ran_at);
```

### `gold_source` — 抓取源健康状态

```sql
CREATE TABLE gold_source (
  source       TEXT PRIMARY KEY,             -- sge / shfe / yahoo / smm
  last_status  TEXT,
  last_rows    INTEGER,
  last_error   TEXT,
  last_ran_at  TEXT,
  updated_at   TEXT
);
```

## Channel 与单位

| channel | 名称 | unit | kind |
|---|---|---|---|
| sge | 上海黄金交易所 Au99.99 | 元/克 | single |
| shfe | 上海期货交易所 沪金主力 | 元/克 | single |
| yahoo | COMEX 黄金主力 GC=F | 美元/盎司 | single |
| smm | 金店挂牌价（SMM 聚合） | 元/克 | multi |

## 价格合理范围（拒绝异常值）

| channel | 范围 |
|---|---|
| sge | 200 — 900 元/克 |
| shfe | 200 — 900 元/克 |
| yahoo | 1000 — 4000 美元/盎司 |
| smm | 400 — 1500 元/克 |

## Seed 数据

- `data/seed/gold_history.json` — 4 channel × 近 365 天日线（sge/shfe/yahoo 各 365 行；smm 4015 行 = 11 品牌 × 365 天）
- `data/seed/gold_brands.json` — SMM 12 品牌当前挂牌价快照