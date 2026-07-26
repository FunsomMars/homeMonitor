import json
import tempfile
import unittest
from pathlib import Path

from server.app import Store
from server.xiaomi_mjwsd06 import AESCCM, decode_advertisement


class DecoderTests(unittest.TestCase):
    def test_atc_payload(self):
        self.assertEqual(decode_advertisement({
            "service_data": {"181a": "a4c138e0064c5a0000"}
        }).temperature_c, 17.6)

    def test_unknown_payload_is_retained_as_unknown(self):
        self.assertIsNone(decode_advertisement({"service_data": {"fe95": "ffffffffff"}}))

    @unittest.skipIf(AESCCM is None, "cryptography is required for Xiaomi encrypted frames")
    def test_mjwsd06_encrypted_temperature_and_humidity(self):
        address = "A4:C1:38:80:15:07"
        bindkey = "4d8f1373fb4d3bab557d0ebd1c78f8c4"
        temperature = decode_advertisement({
            "address": address,
            "service_data": {"fe95": "4859b5553a8699bda053448f1200005b046d6a"},
        }, bindkey)
        humidity = decode_advertisement({
            "address": address,
            "service_data": {"fe95": "5859b5553407158038c1a4bcc732980e000066960f10"},
        }, bindkey)
        self.assertAlmostEqual(temperature.temperature_c, 25.2, places=2)
        self.assertIsNone(temperature.humidity_pct)
        self.assertIsNone(humidity.temperature_c)
        self.assertEqual(humidity.humidity_pct, 39.0)


class StoreTests(unittest.TestCase):
    def test_advertisement_and_reading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "devices.json"
            config.write_text(json.dumps({"devices": [{"address": "AA:BB", "name": "书房"}]}), encoding="utf-8")
            store = Store(root / "test.db", config)
            store.add_advertisement({"address": "aa:bb", "rssi": -40, "service_data": {"181a": "a4c138e0064c5a0000"}})
            rows = store.readings(24)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["room"], "书房")
            self.assertEqual(rows[0]["temperature_c"], 17.6)

    @unittest.skipIf(AESCCM is None, "cryptography is required for Xiaomi encrypted frames")
    def test_store_merges_mjwsd06_temperature_and_humidity_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "devices.json"
            config.write_text(json.dumps({"devices": [{
                "address": "A4:C1:38:80:15:07",
                "name": "测试房间",
                "bindkey": "4d8f1373fb4d3bab557d0ebd1c78f8c4",
            }]}), encoding="utf-8")
            store = Store(root / "test.db", config)
            base = {"address": "A4:C1:38:80:15:07", "rssi": -45, "service_data": {"fe95": ""}}
            store.add_advertisement({**base, "service_data": {"fe95": "4859b5553a8699bda053448f1200005b046d6a"}})
            self.assertEqual(store.readings(24), [])
            store.add_advertisement({**base, "service_data": {"fe95": "5859b5553407158038c1a4bcc732980e000066960f10"}})
            rows = store.readings(24)
            self.assertEqual(len(rows), 1)
            self.assertAlmostEqual(rows[0]["temperature_c"], 25.2, places=2)
            self.assertEqual(rows[0]["humidity_pct"], 39.0)


if __name__ == "__main__":
    unittest.main()
