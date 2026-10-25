"""Tests for tools/bassstation2_patch.py: the Bass Station II 7<->8 bit codec and schema."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import bassstation2_patch as patch  # noqa: E402


def ox(e):
    """Components' OX(): 8 packed 7-bit bytes -> 7 data bytes. Reference impl."""
    t = [0] * 7
    t[0] = ((e[0] << 1) & 255) + ((e[1] >> 6) & 255)
    t[1] = ((e[1] << 2) & 255) + ((e[2] >> 5) & 255)
    t[2] = ((e[2] << 3) & 255) + ((e[3] >> 4) & 255)
    t[3] = ((e[3] << 4) & 255) + ((e[4] >> 3) & 255)
    t[4] = ((e[4] << 5) & 255) + ((e[5] >> 2) & 255)
    t[5] = ((e[5] << 6) & 255) + ((e[6] >> 1) & 255)
    t[6] = ((e[6] << 7) & 255) + (255 & e[7])
    return bytes(t)


class CodecTest(unittest.TestCase):
    def test_matches_components_ox(self):
        # Our continuous-bitstream unpack must equal Components' per-8 OX grouping.
        for _ in range(200):
            packed = bytes(b & 0x7F for b in os.urandom(144))
            theirs = b"".join(ox(packed[i:i + 8]) for i in range(0, 144, 8))
            self.assertEqual(patch.unpack_7to8(packed), theirs)

    def test_round_trip(self):
        for _ in range(200):
            data = os.urandom(patch.PAYLOAD_UNPACKED)
            packed = patch.pack_8to7(data)
            self.assertEqual(len(packed), patch.PAYLOAD_PACKED)
            self.assertTrue(all(b <= 0x7F for b in packed))
            self.assertEqual(patch.unpack_7to8(packed), data)

    def test_rejects_non_7bit(self):
        with self.assertRaises(ValueError):
            patch.unpack_7to8(bytes([0x80] + [0] * 7))


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.schema = patch.load_schema()

    def test_count(self):
        self.assertEqual(len(self.schema), 85)

    def test_indices_in_range(self):
        for p in self.schema:
            self.assertLess(p["patchIndex"], patch.PAYLOAD_UNPACKED)

    def test_decode_value_types(self):
        self.assertEqual(patch.decode_value(64, "DT_S", 0, 0), 0)      # center
        self.assertEqual(patch.decode_value(128, "DT_S8", 0, 0), 0)    # center
        self.assertEqual(patch.decode_value(0b0100, "DT_B", 2, 0), True)
        self.assertEqual(patch.decode_value(0b0011, "DT_M", 0, 3), 3)
        self.assertEqual(patch.decode_value(100, "DT_U", 0, 0), 100)


if __name__ == "__main__":
    unittest.main()
