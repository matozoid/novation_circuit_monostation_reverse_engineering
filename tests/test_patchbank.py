"""Tests for tools/patchbank.py: the 136-byte Mono Station bank/pack reader.

Uses the real bank in Roboo.monostationpack (repo root) when present; the
structural tests run on a synthetic bank otherwise.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import patchbank  # noqa: E402

PACK = ROOT / "Roboo.monostationpack"


def make_msg(slot, name, category=0, genre=0):
    m = bytearray(patchbank.MSG_SIZE)
    m[0:4] = patchbank.MFR
    m[4:6] = patchbank.DEVICE_ID
    m[6] = 1
    m[7] = slot
    nm = name.encode("ascii")[:16].ljust(16, b" ")
    m[9:25] = nm
    m[25] = category
    m[26] = genre
    m[-1] = 0xF7
    return bytes(m)


class StructureTest(unittest.TestCase):
    def test_split_validates_and_counts(self):
        bank = b"".join(make_msg(i, "patch%d" % i) for i in range(64))
        self.assertEqual(len(bank), patchbank.BANK_SIZE)
        msgs = patchbank.split_bank(bank)
        self.assertEqual(len(msgs), 64)

    def test_rejects_bad_device_id(self):
        m = bytearray(make_msg(0, "x"))
        m[5] = 0x60  # Circuit, not Mono Station
        with self.assertRaises(ValueError):
            patchbank.split_bank(bytes(m))

    def test_rejects_bad_length(self):
        with self.assertRaises(ValueError):
            patchbank.split_bank(b"\x00" * 100)

    def test_decode_meta(self):
        d = patchbank.decode_meta(make_msg(5, "Bass Thing", category=1))
        self.assertEqual(d["slot"], 5)
        self.assertEqual(d["name"], "Bass Thing")
        self.assertEqual(d["category"], 1)
        self.assertEqual(len(d["raw_params"]),
                         patchbank.MSG_SIZE - patchbank.PARAMS_OFF - 1)


@unittest.skipUnless(PACK.exists(), "Roboo.monostationpack absent")
class RealBankTest(unittest.TestCase):
    def setUp(self):
        self.msgs = patchbank.split_bank(patchbank.read_pack(PACK))

    def test_bank_has_64_patches(self):
        self.assertEqual(len(self.msgs), 64)

    def test_slots_are_sequential(self):
        self.assertEqual([m[7] for m in self.msgs], list(range(64)))

    def test_known_names(self):
        names = [patchbank.decode_meta(m)["name"] for m in self.msgs]
        self.assertEqual(names[0], "bass1")
        self.assertTrue(all(n == "Initial Patch" for n in names[32:]))

    def test_bass_patches_are_category_1(self):
        for m in self.msgs[:4]:
            self.assertEqual(patchbank.decode_meta(m)["category"], 1)


if __name__ == "__main__":
    unittest.main()
