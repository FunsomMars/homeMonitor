#!/usr/bin/env python3
"""Provision Wi-Fi/LAN settings into an ESP32 proxy over USB serial."""

from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path
import select
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
        command = (json.dumps(payload, separators=(",", ":")) + "\n").encode()
        written = 0
        while written < len(command):
            written += os.write(fd, command[written:])
        termios.tcdrain(fd)
        print("配置命令已发送，等待 ESP32 确认……")

        deadline = time.time() + 4
        buffer = b""
        responses = []
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if not ready:
                continue
            buffer += os.read(fd, 8192)
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace").strip()
                if '"type":"config"' in line or '"type":"wifi"' in line or '"type":"upload"' in line:
                    responses.append(line)

        if responses:
            print("ESP32 响应：")
            for line in responses:
                print(line)
        else:
            print("未收到 ESP32 配置确认，请检查串口、固件版本和 USB 连接。")
        print("随后可用 serial_capture.py 观察 wifi/upload 状态。")
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
