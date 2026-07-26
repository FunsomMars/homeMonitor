"""Decoders for Xiaomi and compatible BLE temperature sensors.

The stock MJWSD06MMC firmware uses Xiaomi MiBeacon V5.  Its temperature and
humidity values are AES-CCM encrypted and the two values may arrive in
separate advertisements, so the caller is allowed to receive a partial
reading.  The bind key is never inferred or guessed: it must be supplied in
the device configuration.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESCCM
except ImportError:  # Keep raw advertisement capture usable without the extra dependency.
    AESCCM = None  # type: ignore[assignment,misc]

    class InvalidTag(Exception):
        pass


@dataclass(frozen=True)
class Reading:
    temperature_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    battery_pct: Optional[int] = None
    protocol: str = "unknown"


def _hex(value: object) -> bytes:
    if not isinstance(value, str):
        return b""
    try:
        return bytes.fromhex(value)
    except ValueError:
        return b""


def _mac_bytes(address: object) -> bytes:
    if not isinstance(address, str):
        return b""
    try:
        value = bytes.fromhex(address.replace(":", "").replace("-", ""))
    except ValueError:
        return b""
    return value if len(value) == 6 else b""


def _bindkey_bytes(bindkey: object) -> bytes:
    value = _hex(bindkey)
    return value if len(value) == 16 else b""


def _parse_datapoints(payload: bytes) -> Optional[Reading]:
    """Parse decrypted MiBeacon data points.

    MJWSD06MMC uses 0x4801 (float temperature) and 0x4802 (integer humidity),
    while the common 0x100D/0x1004/0x1006 forms are retained for compatibility.
    """
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    battery: Optional[int] = None
    index = 0
    while index + 3 <= len(payload):
        value_type = payload[index] | (payload[index + 1] << 8)
        length = payload[index + 2]
        index += 3
        if length < 1 or length > 4 or index + length > len(payload):
            break
        data = payload[index:index + length]
        index += length

        if value_type == 0x4801 and length == 4:
            candidate = struct.unpack("<f", data)[0]
            if -40 <= candidate <= 85:
                temperature = candidate
        elif value_type == 0x4802 and length == 1:
            candidate = data[0]
            if 0 <= candidate <= 100:
                humidity = float(candidate)
        elif value_type == 0x100D and length == 4:
            candidate_temperature = int.from_bytes(data[:2], "little", signed=True) / 10
            candidate_humidity = int.from_bytes(data[2:], "little", signed=True) / 10
            if -40 <= candidate_temperature <= 85:
                temperature = candidate_temperature
            if 0 <= candidate_humidity <= 100:
                humidity = candidate_humidity
        elif value_type == 0x1004 and length == 2:
            candidate = int.from_bytes(data, "little", signed=True) / 10
            if -40 <= candidate <= 85:
                temperature = candidate
        elif value_type == 0x1006 and length == 2:
            candidate = int.from_bytes(data, "little", signed=True) / 10
            if 0 <= candidate <= 100:
                humidity = candidate
        elif value_type in (0x100A, 0x4803) and length == 1 and data[0] <= 100:
            battery = data[0]

    if temperature is None and humidity is None and battery is None:
        return None
    return Reading(temperature, humidity, battery, "xiaomi-mibeacon-v5")


def _decode_mibeacon_v5(payload: bytes, address: object, bindkey: object) -> Optional[Reading]:
    """Decrypt a Xiaomi MiBeacon V5 payload used by MJWSD06MMC."""
    mac = _mac_bytes(address)
    key = _bindkey_bytes(bindkey)
    if AESCCM is None or not mac or not key:
        return None

    # 19-byte packets have no capability block; 22-24-byte packets do.
    if len(payload) == 19:
        cipher_pos = 5
        data_size = 7
    elif 22 <= len(payload) <= 24:
        cipher_pos = 11
        data_size = len(payload) - 18
    else:
        return None
    if not (payload[0] & 0x08) or cipher_pos + data_size > len(payload) - 4:
        return None

    # Xiaomi's nonce is reverse BLE address + sensor type/packet id + counter.
    nonce = mac[::-1] + payload[2:5] + payload[-7:-4]
    ciphertext_with_tag = payload[cipher_pos:cipher_pos + data_size] + payload[-4:]
    try:
        plaintext = AESCCM(key, tag_length=4).decrypt(nonce, ciphertext_with_tag, b"\x11")
    except (InvalidTag, ValueError):
        return None
    return _parse_datapoints(plaintext)


def decode_advertisement(advertisement: dict, bindkey: object = None) -> Optional[Reading]:
    """Decode a single advertisement, returning a complete or partial reading."""
    service_data = advertisement.get("service_data") or {}
    for uuid, raw in service_data.items():
        normalized_uuid = str(uuid).lower().replace("0x", "")
        if not (normalized_uuid == "fe95" or normalized_uuid.endswith("fe95")):
            continue
        payload = _hex(raw)
        decoded = _decode_mibeacon_v5(payload, advertisement.get("address"), bindkey)
        if decoded:
            return decoded

        # Unencrypted MiBeacon data points, for compatible/custom firmware.
        decoded = _parse_datapoints(payload)
        if decoded:
            return Reading(decoded.temperature_c, decoded.humidity_pct, decoded.battery_pct, "xiaomi-fe95")

    # ATC/PVVX custom firmware format: temperature int16 LE / 100, humidity byte.
    for raw in service_data.values():
        payload = _hex(raw)
        if len(payload) >= 8 and payload[:3] == bytes.fromhex("a4c138"):
            temperature = int.from_bytes(payload[3:5], "little", signed=True) / 100
            humidity = payload[5]
            battery = payload[6]
            if -40 <= temperature <= 85 and 0 <= humidity <= 100:
                return Reading(temperature, humidity, battery, "atc-pvvx")

    # BTHome v2 simple temperature/humidity objects.
    for raw in service_data.values():
        payload = _hex(raw)
        if len(payload) < 2:
            continue
        temperature = humidity = None
        index = 1
        while index < len(payload):
            object_id = payload[index]
            index += 1
            if object_id == 0x02 and index + 2 <= len(payload):
                temperature = int.from_bytes(payload[index:index + 2], "little", signed=True) / 100
                index += 2
            elif object_id == 0x03 and index + 2 <= len(payload):
                humidity = int.from_bytes(payload[index:index + 2], "little") / 100
                index += 2
            else:
                break
        if temperature is not None and humidity is not None and -40 <= temperature <= 85 and 0 <= humidity <= 100:
            return Reading(temperature, humidity, protocol="bthome-v2")

    return None
