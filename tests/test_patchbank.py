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


# Init-patch body, offsets 25..134, from the community PDF's "init dec" column.
PDF_INIT_BODY = {
    41: 64, 42: 2, 43: 64, 44: 64, 45: 0, 46: 50, 47: 0, 48: 1, 50: 64, 51: 2,
    52: 64, 53: 64, 54: 0, 55: 50, 56: 0, 57: 1, 60: 100, 61: 127, 62: 64,
    75: 127, 76: 64, 78: 1, 80: 7, 81: 2, 82: 90, 83: 127, 84: 40, 86: 34,
    93: 64, 95: 64, 96: 64, 98: 64, 99: 1, 100: 64, 102: 64, 103: 64, 104: 64,
    106: 64, 107: 64, 109: 64, 110: 64, 111: 64, 113: 64, 114: 64, 115: 64,
    117: 64, 118: 64, 120: 64, 121: 64, 122: 64, 124: 64, 125: 64, 126: 64,
    127: 64, 128: 64, 129: 64, 130: 64, 131: 64, 132: 64, 133: 64, 134: 13,
}


def make_init_msg():
    m = bytearray(patchbank.MSG_SIZE)
    m[0:4] = patchbank.MFR
    m[4:6] = patchbank.DEVICE_ID
    m[9:25] = b"Initial Patch   "
    for off, val in PDF_INIT_BODY.items():
        m[off] = val
    m[-1] = 0xF7
    return bytes(m)


class ParamDecodeTest(unittest.TestCase):
    def test_pair_merges_msb_flag(self):
        m = bytearray(patchbank.MSG_SIZE)
        m[61], m[62] = 98, 0x40           # LSB + set flag -> 98 + 128
        self.assertEqual(patchbank.decode_param(m, 61, "pair"), 226)
        m[61], m[62] = 127, 0x00          # clear flag -> LSB only
        self.assertEqual(patchbank.decode_param(m, 61, "pair"), 127)

    def test_modp_ignores_redundant_sign_flag(self):
        m = bytearray(patchbank.MSG_SIZE)
        m[100], m[101] = 70, 0x40         # >= centre: flag set, value +6
        self.assertEqual(patchbank.decode_param(m, 100, "modp"), 6)
        m[100], m[101] = 38, 0x00         # < centre: flag clear, value -26
        self.assertEqual(patchbank.decode_param(m, 100, "modp"), -26)

    def test_decodes_pdf_init_patch(self):
        d = patchbank.decode_params(make_init_msg())
        self.assertEqual(d["osc1_octave"], 0)       # 64 - 64
        self.assertEqual(d["osc1_fine"], 50)
        self.assertEqual(d["osc1_range"], 1)
        self.assertEqual(d["mix_osc1"], 255)        # 127 + 128 (flag 0x40)
        self.assertEqual(d["filter_cutoff"], 255)   # 127 + 128
        self.assertEqual(d["filter_slope"], 1)
        self.assertEqual(d["filter_key_track"], 7)
        self.assertEqual(d["env_decay"], 90)
        self.assertEqual(d["env_sustain"], 127)
        self.assertEqual(d["env_release"], 40)
        self.assertEqual(d["dist_amount"], 0)
        self.assertEqual(d["mod_env_amp"], 1)
        self.assertEqual(d["mod_env_cutoff"], 0)    # 64 - 64, centred
        self.assertEqual(d["mod_vel_aux"], 0)
        self.assertEqual(d["selected_routing"], 13)

    def test_every_param_decoded(self):
        d = patchbank.decode_params(make_init_msg())
        self.assertEqual(len(d), len(patchbank.PARAMS))


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

    def test_decoded_values_in_range(self):
        for m in self.msgs:
            d = patchbank.decode_params(m)
            self.assertEqual(len(d), len(patchbank.PARAMS))
            for name, off, kind in patchbank.PARAMS:
                v = d[name]
                if kind == "pair":
                    self.assertTrue(0 <= v <= 255, (name, v))
                elif kind in ("mod", "modp", "oct"):
                    self.assertTrue(-64 <= v <= 63, (name, v))
                else:
                    self.assertTrue(0 <= v <= 127, (name, v))


if __name__ == "__main__":
    unittest.main()
