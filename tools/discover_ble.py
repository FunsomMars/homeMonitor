#!/usr/bin/env python3
"""Aggregate BLE addresses emitted by the ESP32 proxy.

Run this while a sensor is close to the ESP32, then use the address that
repeats consistently. Stop with Ctrl-C; the summary is printed at the end.
"""

from __future__ import annotations

import argparse
import os
import json
import select
import termios
import time
from collections import defaultdict


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("port", help="macOS serial device, e.g. /dev/cu.usbmodem...")
    parser.add_argument("--seconds", type=float, default=0, help="stop automatically after N seconds; 0 means Ctrl-C")
    args = parser.parse_args()
    stats = defaultdict(lambda: {"count": 0, "rssi": None, "name": "", "services": set(), "manufacturers": set(), "last": 0.0})
    print("正在监听 BLE 广播；把一个温湿度计放到 ESP32 旁边，按 Ctrl-C 结束并查看汇总。\n")
    try:
        fd = os.open(args.port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            attrs = termios.tcgetattr(fd)
            attrs[4] = termios.B115200
            attrs[5] = termios.B115200
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            buffer = b""
            deadline = time.time() + args.seconds if args.seconds > 0 else None
            while deadline is None or time.time() < deadline:
                ready, _, _ = select.select([fd], [], [], 0.25)
                if not ready:
                    continue
                try:
                    buffer += os.read(fd, 16384)
                except BlockingIOError:
                    continue
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    try:
                        message = json.loads(line.decode("utf-8", errors="replace"))
                    except json.JSONDecodeError:
                        continue
                    if message.get("type") != "advertisement" or not message.get("address"):
                        continue
                    address = str(message["address"]).upper()
                    item = stats[address]
                    item["count"] += 1
                    item["rssi"] = message.get("rssi")
                    item["name"] = message.get("name") or item["name"]
                    item["services"].update(str(x) for x in (message.get("service_data") or {}))
                    item["manufacturers"].update(str(x) for x in (message.get("manufacturer_data") or {}))
                    item["last"] = time.time()
                    services_live = ",".join(sorted(item["services"]))
                    marker = f" services={services_live}" if services_live else ""
                    print(f"{address:20} RSSI={str(item['rssi']):>4} count={item['count']:>4} "
                          f"name={item['name']}{marker}", flush=True)
        finally:
            os.close(fd)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n地址汇总（优先选择 count 较高、RSSI 较强且随温湿度计移动的地址）：")
        for address, item in sorted(stats.items(), key=lambda pair: pair[1]["count"], reverse=True):
            services = ",".join(sorted(item["services"])) or "-"
            manufacturers = ",".join(sorted(item["manufacturers"])) or "-"
            print(f"{address:20} count={item['count']:>4} RSSI={str(item['rssi']):>4} "
                  f"name={item['name'] or '-'} services={services} manufacturer={manufacturers}")


if __name__ == "__main__":
    main()
