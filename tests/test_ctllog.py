"""Tests for the stdlib helpers in tools/ctllog.py (no MIDI device needed)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import ctllog  # noqa: E402


class CtlLogTest(unittest.TestCase):
    def test_debug_request(self):
        self.assertEqual(ctllog.debug_request(1, 0x1F).hex(" "),
                         "f0 00 20 29 01 61 70 32 01 1f f7")

    def test_decode_report(self):
        msg = bytes.fromhex("f0 00 20 29 01 61 70 33 10 05 00 00 00 01 f7")
        self.assertEqual(ctllog.decode_report(msg), ("button", 5, 1))
        msg = bytes.fromhex("f0 00 20 29 01 61 70 33 01 2e 00 0f 0a 03 f7")
        self.assertEqual(ctllog.decode_report(msg), ("pot", 0x2E, 0xFA3))

    def test_other_messages_ignored(self):
        self.assertIsNone(ctllog.decode_report(bytes.fromhex("f0 00 20 29 00 70 f7")))
        self.assertIsNone(ctllog.decode_report(
            bytes.fromhex("f0 00 20 29 01 61 70 40 00 00 00 00 00 00 f7")))


if __name__ == "__main__":
    unittest.main()
