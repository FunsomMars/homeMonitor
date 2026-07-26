#!/usr/bin/env python3
"""Print ESP32 NDJSON serial output without requiring pyserial."""
import sys

if len(sys.argv) != 2:
    raise SystemExit(f"usage: {sys.argv[0]} /dev/cu.usbmodem...")

with open(sys.argv[1], "rb", buffering=0) as stream:
    while True:
        line = stream.readline()
        if not line:
            break
        print(line.decode("utf-8", errors="replace"), end="")
