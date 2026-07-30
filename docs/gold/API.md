# 黄金监控 — REST API

Base path：`/api/gold/*`。所有响应均为 JSON，UTF-8 编码。

## 端点

### `GET /api/gold/health`

健康检查：4 个 channel 的最近抓取状态 + 最近 50 条抓取日志。

响应：

```json
{
  "channels": [
    {"channel": "sge",   "name": "上海黄金交易所 Au99.99", "unit": "元/克",     "kind": "single", "row_count": 0, "last_run_at": "2026-07-30T14:30:00+08:00", "last_status": "ok"},
    {"channel": "shfe",  "name": "上海期货交易所 沪金主力", "unit": "元/克",     "kind": "single", "row_count": 0, "last_run_at": "...", "last_status": "ok"},
    {"channel": "yahoo", "name": "COMEX 黄金主力 GC=F",     "unit": "美元/盎司", "kind": "single", "row_count": 0, "last_run_at": "...", "last_status": "fail"},
    {"channel": "smm",   "name": "金店挂牌价（SMM 聚合）",   "unit": "元/克",     "kind": "multi",  "row_count": 0, "last_run_at": "...", "last_status": "ok"}
  ],
  "fetch_log": [...],
  "time": "2026-07-30T14:30:59+00:00"
}
```

### `GET /api/gold/channels`

元信息：4 channel 的配置 + seed 行数 + seed as_of。

```json
{
  "channels": [
    {"channel": "sge",   "name": "上海黄金交易所 Au99.99", "unit": "元/克",     "kind": "single", "row_count": 365, "seed_as_of": "2026-07-31T00:00:00+08:00"},
    {"channel": "shfe",  "name": "上海期货交易所 沪金主力", "unit": "元/克",     "kind": "single", "row_count": 365, "seed_as_of": "2026-07-31T00:00:00+08:00"},
    {"channel": "yahoo", "name": "COMEX 黄金主力 GC=F",     "unit": "美元/盎司", "kind": "single", "row_count": 365, "seed_as_of": "2026-07-31T00:00:00+08:00"},
    {"channel": "smm",   "name": "金店挂牌价（SMM 聚合）",   "unit": "元/克",     "kind": "multi",  "row_count": 4015, "seed_as_of": "2026-07-31T00:00:00+08:00"}
  ]
}
```

### `GET /api/gold/current`

当前价。`smm` 包含 11 个品牌；其他 channel 单条。

```json
{
  "channels": {
    "sge":   {"channel":"sge",   "name":"...", "unit":"元/克",     "kind":"single", "as_of":"2026-07-31T00:00:00+08:00", "items":[{"brand":"", "price":783.52, "effective_at":"...", "source":"seed", "confidence":"fallback", "updated_at":"..."}]},
    "shfe":  {...},
    "yahoo": {"...unit":"美元/盎司..."},
    "smm":   {"channel":"smm", "kind":"multi", "items":[{"brand":"周大福","price":962.0, ...}, {"brand":"老凤祥",...}, ...]}
  },
  "as_of": "2026-07-31T00:00:00+08:00"
}
```

### `GET /api/gold/history`

单 channel 曲线。Query：`channel`（必填：`sge`/`shfe`/`yahoo`/`smm`）、`brand`（smm 必填，其他留空）、`days`（默认 90，上限 400）。

```json
{
  "channel": "smm",
  "brand": "周大福",
  "name": "金店挂牌价（SMM 聚合）",
  "unit": "元/克",
  "days": 90,
  "series": [{"date":"2026-05-02","value":962.46}, ...],
  "stats": {
    "first_date": "2026-05-02",
    "last_date":  "2026-07-30",
    "first_value": 962.46,
    "last_value":  963.85,
    "delta":       1.39,
    "delta_pct":   0.14
  }
}
```

### `POST /api/gold/refresh`

手动触发抓取。需要 `X-Home-Monitor-Token` 请求头（与 `/api/oil/refresh` 同源）。

请求体（可选）：`{"source": "sge"}` 或 `{"source": "all"}`（默认 all）。

响应：

```json
{
  "ok": true,
  "results": {
    "sge":   {"ok": true, "rows": 0},
    "shfe":  {"ok": true, "rows": 0},
    "yahoo": {"ok": false, "error": "HTTP Error 401: Unauthorized"},
    "smm":   {"ok": true, "rows": 0}
  }
}
```

抓取失败时，调用方继续保留上次快照 + seed 兜底（与油价模块降级策略一致）。

## 错误码

| HTTP | 场景 |
|---|---|
| 200 | 正常 |
| 400 | query 缺少 `channel` 或 `brand` |
| 401 | refresh 接口 token 校验失败 |
| 404 | 未知 channel（非 sge/shfe/yahoo/smm） |

## 抓取调度（默认）

| source | 频率 | 数据源 URL |
|---|---|---|
| sge | 24h | `https://www.sge.com.cn/sjzx/quotation_daily_new` |
| shfe | 24h | `https://www.shfe.cn/reports/tradedata/dailyandweeklydata/` |
| yahoo | 6h | `https://query1.finance.yahoo.com/v7/finance/download/GC=F?...` |
| smm | 24h | `https://precious.smm.cn/gold-price` |