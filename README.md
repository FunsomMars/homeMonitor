# 家庭温湿度监控

基于 ESP32-S3-N16R8 蓝牙采集代理和 Mac mini 本地服务，采集两个 Xiaomi Smart Temperature and Humidity Monitor 3 Mini（型号 `MJWSD06MMC`）的温度、湿度、RSSI 和原始 BLE 广播，并在浏览器中查看历史曲线。

## 当前方案

```text
3 mini (BLE 广播) ──BLE──> ESP32-S3 ──USB CDC NDJSON──> Mac mini
                                                        │
                                                        ├── SQLite
                                                        ├── REST API
                                                        └── Web UI
```

- ESP32 只负责扫描和可靠转发原始广播，不在固件中绑定尚未确认的 Xiaomi 私有协议。
- ESP32 同时支持 USB NDJSON 和 Wi-Fi HTTP 批量上报；Wi-Fi 未配置或断线时 USB 仍可用。
- Mac 服务将每一帧原始数据保存到 `data/telemetry.db`；识别成功的温湿度样本保存到 `readings`。
- 未识别帧仍保存到 `advertisements`，方便从真实设备广播中确认 `MJWSD06MMC` 的 payload。
- 设备通过 MAC 地址配置房间，避免同型号设备被混淆。
- 多个 ESP32 通过 `proxies` 配置统一管理；每个 proxy 使用独立 USB 串口，服务会在 `/api/proxies` 报告连接状态和最近上报时间。

## 启动 Mac 服务

```bash
python3 -m pip install -r requirements.txt
python3 -m server --port 8787 --serial /dev/cu.usbmodem5C941513621
```

浏览器打开 <http://127.0.0.1:8787>。

局域网模式下服务监听 Mac mini 的局域网地址，并使用 `network.ingest_token` 保护写入接口。ESP32 不需要持续 USB 连接，只需要首次烧录和配置时连接 USB，之后可用 USB 电源供电。

也可以先使用模拟数据验证前端和数据库：

```bash
python3 -m server --demo --port 8787
```

服务默认从 `config/devices.json` 读取设备地址、房间名和 proxy。示例：

首次部署时复制 `config/devices.example.json` 为 `config/devices.json`，再填写实际的代理、温湿度计 MAC、bindkey 和随机 ingest token。`config/devices.json` 仅保存在本机，不要提交到 Git。

```json
{
  "proxies": [
    {
      "id": "proxy-living-area",
      "name": "客餐厅代理",
      "serial": "/dev/cu.usbmodemXXXX",
      "enabled": true
    },
    {
      "id": "proxy-bedroom",
      "name": "卧室代理",
      "serial": "/dev/cu.usbmodemYYYY",
      "enabled": true
    }
  ],
  "devices": []
}
```

也可以重复传入 `--serial`：

```bash
python3 -m server --port 8787 \
  --serial /dev/cu.usbmodemXXXX \
  --serial /dev/cu.usbmodemYYYY
```

## ESP32 固件

`firmware/ble_proxy/ble_proxy.ino` 是 Arduino-ESP32 固件草案：扫描所有 BLE 广播，并以一行 NDJSON 输出 `address`、`rssi`、`name`、`manufacturer_data`、`service_data`。需要安装 Arduino ESP32 core 后选择 ESP32-S3 对应开发板编译上传。

PlatformIO 的入口是 `src/main.cpp`，它复用上述 `.ino` 实现；依赖 `NimBLE-Arduino` 会由 `platformio.ini` 自动安装。编译命令：

```bash
pio run -e esp32-s3-n16r8
```

上传后可先检查串口输出：

```bash
python3 tools/serial_capture.py /dev/cu.usbmodem5C941513621
```

自动汇总广播地址：

```bash
python3 tools/discover_ble.py /dev/cu.usbmodem5C941513621
```

配置 Wi-Fi proxy（配置工具会从 `config/devices.json` 自动读取 ingest token；省略密码参数时会安全地交互输入）：

```bash
python3 tools/configure_proxy.py /dev/cu.usbmodemXXXX \
  --ssid "家庭WiFi" \
  --server-host 192.168.6.145 \
  --server-port 8787 \
  --proxy-id proxy-bedroom
```

配置后，USB 串口会继续输出广播，同时出现 `upload` 成功消息。Mac mini 上可查看：

```bash
curl http://127.0.0.1:8787/api/proxies
curl 'http://127.0.0.1:8787/api/readings?hours=24'
```

## 协议确认流程

`MJWSD06MMC` 原厂固件使用 Xiaomi MiBeacon V5。温湿度广播采用 AES-CCM 加密，且温度、湿度可能分开发送；因此每台设备除了 MAC 地址，还必须配置 32 位十六进制 bind key。

在 `config/devices.json` 中填写：

```json
{
  "address": "A4:C1:38:6F:A1:D9",
  "name": "客厅",
  "enabled": true,
  "bindkey": "32位十六进制密钥"
}
```

bind key 不能由 MAC 地址推导，也不能使用其他设备的密钥。没有密钥时系统仍会保存原始广播，但不会生成温湿度读数。获取密钥后，服务会自动解密 MiBeacon V5，并合并分开发送的温度/湿度帧后写入 `readings` 表。

安装服务依赖：

```bash
python3 -m pip install -r requirements.txt
```

解析器同时保留 ATC/PVVX 和 BTHome v2 兼容格式，并以明确的 `protocol` 字段标识来源。

## 验证

```bash
python3 -m unittest discover -s tests -v
```

服务健康检查：

```bash
curl http://127.0.0.1:8787/api/health
curl 'http://127.0.0.1:8787/api/readings?hours=24'
```
