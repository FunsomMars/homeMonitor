#!/usr/bin/env python3
"""Provision Wi-Fi/LAN settings into an ESP32 proxy over USB serial."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import termios
import time


def main() -> None:
    parser = argparse.ArgumentParser(description="Configure an ESP32 BLE proxy over USB")
    parser.add_argument("port", help="ESP32 serial port")
    parser.add_argument("--ssid", help="Wi-Fi SSID")
    parser.add_argument("--password", help="Wi-Fi password; omitted means prompt securely")
    parser.add_argument("--server-host", required=True, help="Mac mini LAN IP or hostname")
    parser.add_argument("--server-port", type=int, default=8787)
    parser.add_argument("--proxy-id", required=True, help="stable ID, e.g. proxy-bedroom")
    parser.add_argument("--token", default=None, help="ingest token; otherwise read from config")
    parser.add_argument("--config", default="config/devices.json", help="local config containing network.ingest_token")
    args = parser.parse_args()

    if not args.ssid:
        args.ssid = input("Wi-Fi SSID: ").strip()
    if args.password is None:
        args.password = getpass.getpass("Wi-Fi password: ")
    if args.token is None:
        try:
            config = json.loads(Path(args.config).read_text(encoding="utf-8"))
            args.token = str(config.get("network", {}).get("ingest_token", ""))
        except (OSError, json.JSONDecodeError):
            args.token = os.getenv("HOME_MONITOR_INGEST_TOKEN", "")

    if not args.proxy_id.replace("-", "").replace("_", "").replace(".", "").isalnum():
        raise SystemExit("proxy-id may contain only letters, numbers, '.', '_' and '-'")
    payload = {
        "type": "configure",
        "wifi_ssid": args.ssid,
        "wifi_password": args.password,
        "server_host": args.server_host,
        "server_port": args.server_port,
        "proxy_id": args.proxy_id,
        "ingest_token": args.token,
    }
    fd = os.open(args.port, os.O_RDWR | os.O_NOCTTY)
    try:
        attrs = termios.tcgetattr(fd)
        attrs[4] = termios.B115200
        attrs[5] = termios.B115200
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        time.sleep(1)
        os.write(fd, (json.dumps(payload, separators=(",", ":")) + "\n").encode())
        print("配置命令已发送；设备会保存 Wi-Fi、Mac mini 地址、proxy_id 和 token。")
        print("随后可用 serial_capture.py 观察 wifi/upload 状态。")
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
