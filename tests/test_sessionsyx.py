"""Tests for tools/sessionsyx.py: the session SysEx transfer codec.

Synthetic streams always run; the real-session test uses
firmware/session_32.monostationsession (git-ignored) when present.
"""
import os
import sys
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import sessionsyx  # noqa: E402

SESSION_32 = ROOT / "firmware" / "session_32.monostationsession"


class BlockTest(unittest.TestCase):
    def test_round_trip(self):
        for _ in range(50):
            payload = os.urandom(sessionsyx.BLOCK_BYTES)
            septets = sessionsyx.encode_block(payload)
            self.assertEqual(len(septets), sessionsyx.BLOCK_SEPTETS)
            self.assertTrue(all(s <= 0x7F for s in septets))
            self.assertEqual(sessionsyx.decode_block(septets), payload)

    def test_high_bit_septet_maps_bit_i_to_byte_i(self):
        # First block of a real dump: high septet 0x10 sets bit 7 of byte 4 only.
        septets = bytes([0x10, 0x55, 0x53, 0x45, 0x52, 0x7C, 0x5F, 0x01])
        septets += bytes(sessionsyx.BLOCK_SEPTETS - len(septets))
        self.assertEqual(sessionsyx.decode_block(septets)[:7],
                         bytes.fromhex("55534552fc5f01"))

    def test_rejects_high_bit(self):
        with self.assertRaises(ValueError):
            sessionsyx.decode_block(bytes([0x80]) + bytes(sessionsyx.BLOCK_SEPTETS - 1))


class TransferTest(unittest.TestCase):
    def setUp(self):
        self.stream = os.urandom(sessionsyx.BLOCK_BYTES * 5)

    def test_round_trip(self):
        syx = sessionsyx.encode_transfer(self.stream)
        start, stream = sessionsyx.decode_transfer(syx)
        self.assertEqual(stream, self.stream)
        self.assertEqual(start, sessionsyx.SESSIONS_START)
        self.assertEqual(sessionsyx.encode_transfer(stream, start), syx)

    def test_framing(self):
        msgs = sessionsyx.split_messages(sessionsyx.encode_transfer(self.stream))
        self.assertEqual(len(msgs[0]), 23)
        self.assertEqual(len(msgs[-1]), 15)
        self.assertTrue(all(len(m) == 300 for m in msgs[1:-1]))
        self.assertEqual(sessionsyx.from_nibbles(msgs[-1][6:-1]), zlib.crc32(self.stream))

    def test_request(self):
        msg = sessionsyx.request()
        self.assertEqual(msg[:6], bytes.fromhex("f000202900") + bytes([0x78]))
        self.assertEqual(len(msg), 23)
        self.assertEqual(sessionsyx.from_nibbles(msg[6:14]), 0x12000)
        self.assertEqual(sessionsyx.from_nibbles(msg[14:22]), 2973696)

    def test_crc_mismatch_rejected(self):
        syx = bytearray(sessionsyx.encode_transfer(self.stream))
        syx[-2] ^= 0x01                      # last trailer nibble
        with self.assertRaises(ValueError):
            sessionsyx.decode_transfer(bytes(syx))


@unittest.skipUnless(SESSION_32.exists(), "session_32.monostationsession absent")
class RealSessionTest(unittest.TestCase):
    def test_init_session_round_trip(self):
        image = SESSION_32.read_bytes()
        self.assertEqual(len(image), sessionsyx.SESSION_SIZE)
        self.assertEqual(image[:4], b"INIT")
        self.assertEqual(sessionsyx.session_name(image), "Initial Session")
        _, stream = sessionsyx.decode_transfer(sessionsyx.encode_transfer(image))
        self.assertEqual(sessionsyx.split_sessions(stream), [image])


if __name__ == "__main__":
    unittest.main()
