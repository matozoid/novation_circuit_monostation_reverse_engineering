"""Tests for the stdlib helpers in tools/hwtest_slots.py (no MIDI device needed)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import hwtest_slots as hw  # noqa: E402
import sessionsyx  # noqa: E402


class P40Test(unittest.TestCase):
    def test_read_request(self):
        msg = hw.p40_read_request(0xA100)
        self.assertEqual(msg.hex(" "), "f0 00 20 29 01 61 70 40 00 02 "
                                       "00 00 00 00 0a 01 00 00 f7")

    def test_reply_round_trip(self):
        data = bytes(range(256))
        nib = bytes(x for b in data for x in (b >> 4, b & 0xF))
        reply = hw.P40_PREFIX + bytes([2]) + nib + b"\xF7"
        self.assertEqual(len(reply), 523)
        self.assertEqual(hw.decode_p40_reply(reply), data)

    def test_reply_rejects_other_op(self):
        reply = hw.P40_PREFIX + bytes([1]) + bytes(512) + b"\xF7"
        with self.assertRaises(ValueError):
            hw.decode_p40_reply(reply)

    def test_hidden_range(self):
        self.assertEqual((hw.HIDDEN_START, hw.HIDDEN_END), (0xA000, 0x12000))
        reads = range(hw.HIDDEN_START, hw.HIDDEN_END, hw.P40_READ_BYTES)
        self.assertEqual(len(reads), 128)


class NrpnTest(unittest.TestCase):
    def test_trigger(self):
        self.assertEqual(hw.nrpn(*hw.TRIGGER_NRPN, 1), [(99, 0), (98, 73), (6, 1)])


class VersionTest(unittest.TestCase):
    def test_decode(self):
        msg = bytes([0xF0, 0, 0x20, 0x29, 0, 0x70, 0, 0, 1, 0, 0, 0, 0, 8, 0, 0, 0xF7])
        self.assertEqual(hw.decode_version(msg), (100, 800))


class ClassifyTest(unittest.TestCase):
    def test_kinds(self):
        init = b"Initial Patch   " + bytes(110)
        slot = lambda p: p + b"\xFF" * (hw.SLOT_SIZE - len(p))
        stream = slot(b"\xFF" * 126) + slot(init) + slot(b"Bass 1          " + bytes(110))
        got = hw.classify(stream, 64, init)
        self.assertEqual([s["kind"] for s in got], ["erased", "initial", "data"])
        self.assertEqual([s["slot"] for s in got], [64, 65, 66])
        self.assertEqual(hw.summarise(got), {"erased": 1, "initial": 1, "data": 1})


class TransferFilterTest(unittest.TestCase):
    def test_accepts_transfer_only(self):
        t = sessionsyx.encode_transfer(bytes(512), start=0x2000)
        msgs = sessionsyx.split_messages(t)
        self.assertTrue(all(hw.is_transfer_message(m) for m in msgs))
        self.assertFalse(hw.is_transfer_message(hw.VERSION_REQUEST))
        self.assertFalse(hw.is_transfer_message(hw.p40_read_request(0)))


if __name__ == "__main__":
    unittest.main()
