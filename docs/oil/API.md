# 油价模块 API 清单 + 时序图

> 所有端点仅监听 `127.0.0.1:8787`（与现有服务同进程）。写操作 `POST /api/oil/refresh` 沿用现有 `ingest_token` 机制。

---

## 1. API 列表

### 1.1 `GET /api/oil/current`

**用途**：获取某省当前 92/95/98/0 四档油价。

**Query**：

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| province | 否 | 江苏 | 省份中文名 |

**响应 200**：

```json
{
  "province": "江苏",
  "as_of": "2026-07-18T00:00:00+08:00",
  "items": [
    {"type": "92", "price": 7.39, "effective_at": "2026-07-18T00:00:00+08:00", "source": "jiangsu_fgw", "confidence": "official", "updated_at": "2026-07-18T00:05:00+08:00"},
    {"type": "95", "price": 7.86, "effective_at": "2026-07-18T00:00:00+08:00", "source": "jiangsu_fgw", "confidence": "official", "updated_at": "2026-07-18T00:05:00+08:00"},
    {"type": "98", "price": 9.12, "effective_at": "2026-07-18T00:00:00+08:00", "source": "seed",       "confidence": "fallback",  "updated_at": "2026-07-18T00:05:00+08:00"},
    {"type": "0",  "price": 7.04, "effective_at": "2026-07-18T00:00:00+08:00", "source": "jiangsu_fgw", "confidence": "official", "updated_at": "2026-07-18T00:05:00+08:00"}
  ],
  "last_adjustment": {
    "effective_at": "2026-07-17T16:00:00Z",
    "gasoline_change": 300,
    "diesel_change": 290,
    "source": "national_fgw"
  }
}
```

**错误**：

| 状态码 | 含义 |
|---|---|
| 404 | 省份不存在 |
| 503 | 油价格式无效（不应发生，由 seed 兜底） |

---

### 1.2 `GET /api/oil/history`

**用途**：获取单省单油品历史曲线。

**Query**：

| 参数 | 必填 | 默认 | 说明 |
|---|---|---|---|
| province | 否 | 江苏 | |
| type | 否 | 92 | 92/95/98/0 |
| days | 否 | 365 | 最大 3650 |

**响应 200**：

```json
{
  "province": "江苏",
  "type": "92",
  "days": 365,
  "series": [
    {"date": "2025-07-18", "value": 7.42},
    {"date": "2025-07-25", "value": 7.38},
    ...
    {"date": "2026-07-18", "value": 7.39}
  ],
  "adjustments": [
    {"date": "2026-07-17", "gasoline_change": 300, "diesel_change": 290}
  ],
  "stats": {
    "first_date": "2025-07-18",
    "last_date": "2026-07-18",
    "first_value": 7.42,
    "last_value": 7.39,
    "delta": -0.03,
    "delta_pct": -0.40
  }
}
```

---

### 1.3 `GET /api/oil/adjustments`

**用途**：调价事件流。

**Query**：

| 参数 | 必填 | 默认 |
|---|---|---|
| limit | 否 | 10 |

**响应 200**：

```json
{
  "events": [
    {"effective_at": "2026-07-17T16:00:00Z", "gasoline_change": 300, "diesel_change": 290, "source": "national_fgw", "notice_url": "..."},
    {"effective_at": "2026-07-03T16:00:00Z", "gasoline_change": -145, "diesel_change": -140, "source": "national_fgw", "notice_url": "..."}
  ]
}
```

---

### 1.4 `GET /api/oil/map`

**用途**：全国地图色阶数据（31 省 × 92#）。

**响应 200**：

```json
{
  "fuel_type": "92",
  "min": 7.10,
  "max": 7.65,
  "data": [
    {"name": "北京", "value": 7.42, "code": "110000"},
    {"name": "江苏", "value": 7.39, "code": "320000"},
    ...
  ],
  "as_of": "2026-07-18T00:00:00+08:00"
}
```

> v1 数据来自 seed；后续可由 `eastmoney` 抓取填充。

---

### 1.5 `POST /api/oil/refresh`

**用途**：手动触发一次抓取。

**请求**：

```json
{"source": "all"}  // 或 "eastmoney" / "jiangsu_fgw" / "national_fgw"
```

**Headers**：`X-Home-Monitor-Token: <ingest_token>`

**响应 200**：

```json
{"ok": true, "source": "all", "rows": 12, "duration_ms": 1842}
```

---

## 2. 关键状态字段

| 字段 | 含义 | 取值 |
|---|---|---|
| `confidence` | 数据可信度 | `official` (省发改委) / `derived` (公式推算) / `fallback` (seed/上次快照) |
| `source` | 来源 ID | `jiangsu_fgw` / `national_fgw` / `eastmoney` / `seed` |
| `effective_at` | 生效时间（业务时间） | 与发布时刻无关 |
| `updated_at` | 入库时间 | 系统时间 |
| `fuel_type` | 油品 | `92` / `95` / `98` / `0` |
| `delta` | 区间首末差 | last_value - first_value |
| `delta_pct` | 区间百分比 | delta / first_value × 100 |

---

## 3. 前后端交互时序图

### 3.1 打开油价页面（首屏）

```
Browser                  server/app.py            server/oil_store.py
  │                            │                          │
  │  GET /oil                  │                          │
  │──────────────────────────> │                          │
  │  200 web/oil.html          │                          │
  │ <──────────────────────────│                          │
  │                            │                          │
  │  GET /api/oil/current?province=江苏                   │
  │──────────────────────────> │                          │
  │                            │  query current           │
  │                            │─────────────────────────>│
  │                            │  rows                    │
  │                            │ <────────────────────────│
  │  200 {items, last_adjustment}                         │
  │ <──────────────────────────│                          │
  │                            │                          │
  │  GET /api/oil/history?province=江苏&type=92&days=365  │
  │──────────────────────────> │                          │
  │                            │  query history + stats   │
  │                            │─────────────────────────>│
  │                            │  series + stats          │
  │                            │ <────────────────────────│
  │  200 {series, adjustments, stats}                    │
  │ <──────────────────────────│                          │
  │                            │                          │
  │  GET /api/oil/map          │                          │
  │──────────────────────────> │                          │
  │  200 {data, min, max}      │                          │
  │ <──────────────────────────│                          │
  │                            │                          │
  │  [ECharts render]          │                          │
```

### 3.2 用户切换"油品"

```
Browser             server           ECharts
  │                    │                │
  │  click 95#         │                │
  │───────────────────>│                │
  │  GET /api/oil/history?type=95      │
  │───────────────────>│                │
  │  200 {series, stats}               │
  │ <──────────────────│                │
  │  setOption({series:[...]})         │
  │────────────────────────────────────>│
  │                    │  重绘          │
```

### 3.3 调价事件自动同步（后台）

```
OilScheduler (daemon thread)
    │
    │  every 24h
    ▼
national_fgw.fetch()
    │
    │  发现新调价通知
    ▼
oil_adjust_event.upsert(effective_at, gasoline_change, diesel_change)
    │
    │  触发省份更新
    ▼
jiangsu_fgw.fetch()
    │
    ▼
oil_current.upsert(...)  ──> 浏览器下次轮询即看到
```

### 3.4 手动刷新

```
Browser              server            scheduler
  │                    │                  │
  │  POST /api/oil/refresh               │
  │  X-Home-Monitor-Token: xxx            │
  │───────────────────>│                  │
  │                    │  trigger         │
  │                    │─────────────────>│
  │                    │  blocks until    │
  │                    │  done (< 30s)   │
  │                    │ <────────────────│
  │  200 {ok, rows}    │                  │
  │ <──────────────────│                  │
```

---

## 4. 错误码

| HTTP | 含义 | 触发 |
|---|---|---|
| 400 | 参数非法 | days 非数字、type 不在枚举 |
| 401 | 未授权 | refresh 缺 token 或错 token |
| 404 | 资源不存在 | 省份未配置 |
| 500 | 服务异常 | SQLite 错误、JSON 解析失败 |
| 503 | 数据源不可用 | 所有抓取源 + seed 均失败（应不发生） |
