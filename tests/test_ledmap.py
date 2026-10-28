"""Tests for the stdlib helpers in tools/ledmap.py (no MIDI device needed)."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import hwtest_slots as hw  # noqa: E402
import ledmap  # noqa: E402


class RequestTest(unittest.TestCase):
    def test_request(self):
        self.assertEqual(ledmap.ledtest_request([24, 351]).hex(" "),
                         "f0 00 20 29 01 61 7d 02 00 18 02 5f f7")
        self.assertEqual(ledmap.ledtest_request([]).hex(" "), "f0 00 20 29 01 61 7d 00 f7")

    def test_labels_round_trip(self):
        leds = [("rgb0", [24, 25, 26]), ("mono0", [0])]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "leds.tsv"
            ledmap.write_labels(path, leds, {"rgb0": "pad 1", "mono0": ""})
            self.assertEqual(ledmap.read_labels(path), {"rgb0": "pad 1", "mono0": ""})


@unittest.skipUnless(hw.FIRMWARE_BIN.exists(), "firmware image not present")
class Firmware800Test(unittest.TestCase):
    def test_every_channel_once(self):
        leds = ledmap.all_leds(hw.FIRMWARE_BIN.read_bytes())
        self.assertEqual(len(leds), 82 + 21 + 6)
        channels = [c for _, cs in leds for c in cs]
        self.assertEqual(len(channels), len(set(channels)))
        self.assertTrue(all(0 <= c < ledmap.CHANNELS for c in channels))


if __name__ == "__main__":
    unittest.main()
