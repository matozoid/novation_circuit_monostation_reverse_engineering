"""Tests for tools/session.py: the .monostationsession image layout.

Synthetic images always run; the init-image test uses
firmware/session_32.monostationsession (git-ignored) when present.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import patchbank  # noqa: E402
import session  # noqa: E402

SESSION_32 = ROOT / "firmware" / "session_32.monostationsession"


def make_image():
    image = bytearray(session.SESSION_SIZE)
    image[:4] = b"USER"
    image[4:8] = session.STRUCT_SIZE.to_bytes(4, "little")
    image[session.FLAGS_OFF] = 7
    image[session.COLOUR_OFF] = 16
    image[session.NAME_OFF:session.NAME_OFF + session.NAME_LEN] = b"Test".ljust(32)
    image[session.TEMPO_OFF] = 120
    image[session.SWING_OFF] = 50
    image[0x51] = 3                       # track 1 chain end
    return image


class LayoutTest(unittest.TestCase):
    def test_regions_tile_the_struct(self):
        (p0, n0, a0, l0), (p1, n1, a1, l1), (p2, n2, _, _) = session.TRACKS
        self.assertEqual(p0 + n0 * session.PATTERN_SIZE, a0)
        self.assertEqual(a0 + n0 * l0 * session.LANE_SIZE, p1)
        self.assertEqual(p1 + n1 * session.PATTERN_SIZE, a1)
        self.assertEqual(a1 + n1 * l1 * session.LANE_SIZE, p2)
        self.assertEqual(p2 + n2 * session.PATTERN_SIZE, session.GLOBALS2_OFF)
        self.assertEqual(session.PATCH_OFF + session.PATCH_LEN + 0x22, session.STRUCT_SIZE)


class SyntheticTest(unittest.TestCase):
    def test_header(self):
        h = session.header(bytes(make_image()))
        self.assertEqual(h["type"], "user")
        self.assertEqual(h["name"], "Test")
        self.assertEqual((h["tempo"], h["swing"], h["flags"], h["colour"]), (120, 50, 7, 16))
        self.assertEqual(h["chains"][1], (0, 3))

    def test_pattern(self):
        image = make_image()
        base = session.TRACKS[1][0] + 2 * session.PATTERN_SIZE
        image[base + 3 * 8:base + 4 * 8] = bytes.fromhex("3c6006ffff000000")
        image[base + 128] = 7
        p = session.pattern(bytes(image), 1, 2)
        self.assertEqual(p["steps"][3], bytes.fromhex("3c6006ffff000000"))
        self.assertEqual(p["last_step"], 7)
        with self.assertRaises(IndexError):
            session.pattern(bytes(image), 1, 8)

    def test_lane(self):
        image = make_image()
        _, _, auto, lanes = session.TRACKS[0]
        off = auto + (5 * lanes + 9) * session.LANE_SIZE
        image[off] = 0b101                   # micro-steps 0 and 2
        image[off + 12] = 0x40
        image[off + 14] = 0x7F
        mask, values = session.lane(bytes(image), 0, 5, 9)
        self.assertEqual(mask, 0b101)
        self.assertEqual((values[0], values[2]), (0x40, 0x7F))
        self.assertEqual(session.automated_lanes(bytes(image), 0, 5), [9])
        with self.assertRaises(IndexError):
            session.lane(bytes(image), 2, 0, 0)

    def test_patch_message(self):
        image = make_image()
        image[session.PATCH_OFF:session.PATCH_OFF + 16] = b"Kick 5".ljust(16)
        msg = session.patch_message(bytes(image), slot=4)
        self.assertEqual(len(msg), patchbank.MSG_SIZE)
        d = patchbank.decode_meta(msg)
        self.assertEqual((d["name"], d["slot"]), ("Kick 5", 4))

    def test_rejects_bad_image(self):
        with self.assertRaises(ValueError):
            session.header(bytes(100))
        with self.assertRaises(ValueError):
            session.header(bytes(session.SESSION_SIZE))


@unittest.skipUnless(SESSION_32.exists(), "session_32.monostationsession absent")
class InitImageTest(unittest.TestCase):
    def setUp(self):
        self.image = SESSION_32.read_bytes()

    def test_header(self):
        h = session.header(self.image)
        self.assertEqual(h["type"], "init")
        self.assertEqual(h["name"], "Initial Session")
        self.assertEqual(h["struct_size"], session.STRUCT_SIZE)
        self.assertEqual((h["tempo"], h["swing"]), (120, 50))

    def test_empty_patterns(self):
        for t, (_, count, auto, _) in enumerate(session.TRACKS):
            for i in range(count):
                p = session.pattern(self.image, t, i)
                self.assertEqual(p["last_step"], 15)
                self.assertTrue(all(s[0] == 0 for s in p["steps"]))
                if auto is not None:
                    self.assertEqual(session.automated_lanes(self.image, t, i), [])

    def test_patch_is_classic_mono(self):
        msg = session.patch_message(self.image)
        self.assertEqual(patchbank.decode_meta(msg)["name"], "Classic Mono")
        self.assertEqual(self.image[-4:], b"\xFF" * 4)


if __name__ == "__main__":
    unittest.main()
