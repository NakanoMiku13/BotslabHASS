"""Offline unit tests — no network, no real secrets."""
import base64
import hashlib
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import botslab_client as bc  # noqa: E402


class TestSigner(unittest.TestCase):
    def test_sign_concatenation_order(self):
        # sign = md5(appkey + m2 + sign_ts + sign_no + secret)
        got = bc.make_sign("APPKEY", "M2", "1700000000", "NONCE", "SECRET")
        expected = hashlib.md5(b"APPKEYM21700000000NONCESECRET").hexdigest()
        self.assertEqual(got, expected)
        self.assertEqual(len(got), 32)

    def test_sign_is_lowercase_hex(self):
        got = bc.make_sign("a", "b", "c", "d", "e")
        self.assertEqual(got, got.lower())
        int(got, 16)  # raises if not hex


class TestAuthorization(unittest.TestCase):
    def test_token_contains_qts_and_sid(self):
        tok = bc.build_authorization(q="Q!", t="T!", sid="SID!")
        self.assertTrue(tok.startswith("jws "))
        payload = json.loads(base64.b64decode(tok[len("jws "):]))
        self.assertEqual(payload, {"Q": "Q!", "T": "T!", "sid": "SID!"})
        # sid must be present — its absence is what caused code 1015
        self.assertIn("sid", payload)


class TestParsers(unittest.TestCase):
    def test_parse_property(self):
        resp = {"data": {"result": json.dumps({"BatteryLevel": 100, "AutoBoost": False})}}
        self.assertEqual(bc.parse_property(resp), {"BatteryLevel": 100, "AutoBoost": False})

    def test_parse_desired(self):
        resp = {"data": {"report": [
            {"identifier": "BatteryLevel", "timestamp": "1", "value": 90},
            {"identifier": "VolumeLevel", "timestamp": "2", "value": 30},
        ]}}
        self.assertEqual(bc.parse_desired(resp), {"BatteryLevel": 90, "VolumeLevel": 30})


if __name__ == "__main__":
    unittest.main()
