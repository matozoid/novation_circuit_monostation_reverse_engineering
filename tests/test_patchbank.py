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
FIRMWARE_BIN = ROOT / "firmware" / "monostation-firmware-800.bin"
FW_BASE = 0x0800C000
PARAM_TABLE = 0x0802A62A           # 8 bytes per firmware parameter id

# MIDI numbers from Novation's "Circuit Mono Station MIDI Parameters" PDF
# (circuit-mono-station-midi-parameters-v1.pdf): a CC, a CC(8) pair whose
# second CC is the first + 32, or an NRPN (MSB, LSB).
PDF_MIDI = {
    "env_retrigger": ("nrpn", 0, 73),
    "osc1_octave": ("cc", 117), "osc1_waveform": ("cc", 80),
    "osc1_pw": ("cc", 39), "osc1_coarse": ("cc8", 27),
    "osc1_fine": ("cc8", 26), "osc1_range": ("cc", 70),
    "osc1_glide": ("cc", 5),
    "osc2_octave": ("cc", 76), "osc2_waveform": ("cc", 81),
    "osc2_pw": ("cc", 79), "osc2_coarse": ("cc8", 30),
    "osc2_fine": ("cc8", 29), "osc2_range": ("cc", 75),
    "osc2_glide": ("cc", 37),
    "osc_sync": ("cc", 110), "patch_level": ("cc", 7),
    "mix_osc1": ("cc8", 20), "mix_osc2": ("cc8", 21), "mix_sub": ("cc8", 22),
    "mix_noise": ("cc8", 23), "mix_audio_in": ("cc8", 25),
    "mix_ring_mod": ("cc8", 24),
    "filter_overdrive": ("cc", 114), "filter_reso": ("cc", 71),
    "filter_cutoff": ("cc8", 16), "filter_shape": ("cc", 84),
    "filter_slope": ("cc", 40), "filter_bypass": ("cc", 83),
    "filter_key_track": ("cc", 69),
    "env_attack": ("cc", 90), "env_decay": ("cc", 91),
    "env_sustain": ("cc", 92), "env_release": ("cc", 93),
    "lfo_waveform": ("cc", 88), "lfo_rate": ("cc8", 18),
    "lfo_clock_sync_rate": ("cc", 87), "lfo_clock_sync": ("cc", 86),
    "lfo_key_sync": ("cc", 43),
    "dist_amount": ("cc", 94), "dist_type": ("cc", 95),
    "mod_env_osc1_pitch": ("cc8", 12), "mod_env_osc1_pw": ("cc", 41),
    "mod_env_osc2_pitch": ("cc8", 13), "mod_env_osc2_pw": ("cc", 33),
    "mod_env_amp": ("cc", 42), "mod_env_cutoff": ("cc8", 14),
    "mod_env_dist": ("cc", 103), "mod_env_aux": ("cc", 104),
    "mod_lfo_osc1_pitch": ("cc8", 28), "mod_lfo_osc1_pw": ("cc", 73),
    "mod_lfo_osc2_pitch": ("cc8", 3), "mod_lfo_osc2_pw": ("cc", 34),
    "mod_lfo_amp": ("cc", 78), "mod_lfo_cutoff": ("cc8", 17),
    "mod_lfo_dist": ("cc", 108), "mod_lfo_aux": ("cc", 109),
    "mod_seq_osc1_pitch": ("cc8", 15), "mod_seq_osc1_pw": ("cc", 118),
    "mod_seq_osc2_pitch": ("cc8", 19), "mod_seq_osc2_pw": ("cc", 38),
    "mod_seq_amp": ("cc", 119), "mod_seq_cutoff": ("cc8", 31),
    "mod_seq_dist": ("cc", 111), "mod_seq_aux": ("cc", 116),
    "mod_vel_osc1_pitch": ("cc", 115), "mod_vel_osc1_pw": ("cc", 107),
    "mod_vel_osc2_pitch": ("cc", 9), "mod_vel_osc2_pw": ("cc", 36),
    "mod_vel_amp": ("cc", 112), "mod_vel_cutoff": ("cc", 113),
    "mod_vel_dist": ("cc", 105), "mod_vel_aux": ("cc", 89),
    "selected_routing": ("cc", 102),
}


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
    def test_pair_joins_high_bits_and_low_bit(self):
        m = bytearray(patchbank.MSG_SIZE)
        m[61], m[62] = 98, 0x40           # 98 << 1 | 1
        self.assertEqual(patchbank.decode_param(m, 61, "pair"), 197)
        m[61], m[62] = 127, 0x40          # full scale
        self.assertEqual(patchbank.decode_param(m, 61, "pair"), 255)
        m[61], m[62] = 127, 0x00
        self.assertEqual(patchbank.decode_param(m, 61, "pair"), 254)

    def test_signed_pairs_subtract_centre(self):
        m = bytearray(patchbank.MSG_SIZE)
        m[100], m[101] = 70, 0x40         # 141 - 128
        self.assertEqual(patchbank.decode_param(m, 100, "spair"), 13)
        m[100], m[101] = 38, 0x00         # 76 - 128
        self.assertEqual(patchbank.decode_param(m, 100, "spair"), -52)
        m[46], m[47] = 50, 0x00           # fine tune centre
        self.assertEqual(patchbank.decode_param(m, 46, "fine"), 0)
        m[46], m[47] = 100, 0x00          # fine tune maximum, MIDI 200
        self.assertEqual(patchbank.decode_param(m, 46, "fine"), 100)

    def test_decodes_pdf_init_patch(self):
        d = patchbank.decode_params(make_init_msg())
        self.assertEqual(d["osc1_octave"], 0)       # 64 - 64
        self.assertEqual(d["osc1_fine"], 0)         # 50 << 1 = centre 100
        self.assertEqual(d["osc1_range"], 1)
        self.assertEqual(d["mix_osc1"], 255)        # 127 << 1 | 1 (flag 0x40)
        self.assertEqual(d["filter_cutoff"], 255)
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
class EnumTest(unittest.TestCase):
    def test_category_names(self):
        self.assertEqual(len(patchbank.CATEGORIES), 15)
        self.assertEqual(patchbank.category_name(0), "None")
        self.assertEqual(patchbank.category_name(2), "Bass")
        self.assertEqual(patchbank.category_name(14), "Voc/Tune")
        self.assertEqual(patchbank.category_name(15), "15")

    def test_genre_names(self):
        self.assertEqual(len(patchbank.GENRES), 10)
        self.assertEqual(patchbank.genre_name(3), "House")
        self.assertEqual(patchbank.genre_name(9), "Dubstep")
        self.assertEqual(patchbank.genre_name(10), "10")


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
        # Raw byte only: Roboo's author tagged bass1-4 with 1 (= "Arp" in
        # Components' table), so the stored value doesn't match the name.
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
                elif kind == "spair":
                    self.assertTrue(-128 <= v <= 127, (name, v))
                elif kind == "fine":
                    self.assertTrue(-100 <= v <= 100, (name, v))
                elif kind == "s":
                    self.assertTrue(-64 <= v <= 63, (name, v))
                else:
                    self.assertTrue(0 <= v <= 127, (name, v))


@unittest.skipUnless(FIRMWARE_BIN.exists(), "monostation-firmware-800.bin absent")
class FirmwareTableTest(unittest.TestCase):
    """PARAMS against the firmware's parameter table and the MIDI PDF."""

    @classmethod
    def setUpClass(cls):
        fw = FIRMWARE_BIN.read_bytes()
        off = PARAM_TABLE - FW_BASE
        recs = [fw[off + 8 * i:off + 8 * i + 8] for i in range(76)]
        cls.by_offset = {r[0] + 9: r for r in recs}

    def test_every_param_has_a_record(self):
        self.assertEqual(sorted(off for _, off, _ in patchbank.PARAMS),
                         sorted(o for o in self.by_offset if o >= patchbank.PARAMS_OFF))

    def test_kinds_match_firmware(self):
        for name, off, kind in patchbank.PARAMS:
            r = self.by_offset[off]
            fw_kind, centre, midi = r[2], r[3], r[6]
            if midi == 0xFC:
                expected = {(1, 0): "pair", (2, 128): "spair", (2, 100): "fine"}
                self.assertEqual(expected[fw_kind, centre], kind, name)
            elif fw_kind == 2:
                self.assertEqual((centre, kind), (64, "s"), name)
            elif fw_kind == 1:
                self.assertEqual(kind, "u", name)
            else:
                self.assertIn(kind, ("enum", "bool"), name)

    def test_midi_numbers_match_pdf(self):
        self.assertEqual(set(PDF_MIDI), {name for name, _, _ in patchbank.PARAMS})
        for name, off, _ in patchbank.PARAMS:
            r = self.by_offset[off]
            pdf = PDF_MIDI[name]
            if pdf[0] == "nrpn":
                self.assertEqual((r[6], r[7]), pdf[1:], name)
            else:
                self.assertEqual((r[6], r[7]),
                                 (0xFC if pdf[0] == "cc8" else 0xFF, pdf[1]), name)


if __name__ == "__main__":
    unittest.main()
