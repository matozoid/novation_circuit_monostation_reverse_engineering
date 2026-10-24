#!/usr/bin/env python3
"""Read a Circuit Mono Station 136-byte patch / bank / .monostationpack.

This is the format the hardware stores and the community PDF documents (device
id 01 61), distinct from Components' 154-byte packed wire (see tools/patch.py
and docs/sysex-spec.md). One patch message is 136 bytes:

    [0..3]  F0 00 20 29
    [4..5]  01 61            device id (Mono Station)
    [6]     message type     0 = current patch, 1 = slot write/dump
    [7]     slot index        (0..63 in a bank)
    [8]     00
    [9..24] name              16 ASCII, space-padded
    [25]    category          (1=Bass, 3=Drum/Percussion observed; enum TBD)
    [26]    genre
    [27..134] parameters      split "LSB + 00/40" encoding, offsets per the PDF
    [135]   F7

A full bank is 64 * 136 = 8704 bytes. A .monostationpack is a ZIP holding one
member, patches.syx, with that bank.

Framing, name, category, genre, slot and the synth parameters are decoded
here.  Parameters with a 0..255 range are stored as an LSB plus a 0x00/0x40
MSB flag one byte later (value = LSB + 128*(MSB==0x40)); see PARAMS and
decode_params.  Validated byte-exact against real packs; the Components editor
applies perceptual display curves to a few knobs, so its on-screen numbers can
differ from this raw, linear decode.

Usage:
    python3 tools/patchbank.py list Roboo.monostationpack
    python3 tools/patchbank.py list patches.syx
    python3 tools/patchbank.py show patches.syx 24      # by slot index
    python3 tools/patchbank.py show patches.syx Acid     # or by name
"""
import sys
import zipfile
from pathlib import Path

MSG_SIZE = 136
BANK_SIZE = MSG_SIZE * 64        # 8704
MFR = bytes([0xF0, 0x00, 0x20, 0x29])
DEVICE_ID = bytes([0x01, 0x61])
NAME_OFF = 9
NAME_LEN = 16
CATEGORY_OFF = 25
GENRE_OFF = 26
PARAMS_OFF = 27                  # through 134; 135 is F7

MSG_TYPES = {0: "current-patch", 1: "slot-write"}
# Category / genre names, by index (Components' Circuit-family librarian:
# modules frontend/constants/circuit/patch-categories and patch-genres).
CATEGORIES = ["None", "Arp", "Bass", "Bell", "Classic", "Drum", "Keyboard",
              "Lead", "Movement", "Pad", "Poly", "SFX", "String", "User",
              "Voc/Tune"]
GENRES = ["None", "Classic", "D&B/Breaks", "House", "Industrial", "Jazz",
          "R&B/HHop", "Rock/Pop", "Techno", "Dubstep"]

# --- 136-byte parameter map (community PDF offsets == message byte offsets) ---
#
# Several Mono Station parameters have a 0..255 range.  They are stored as two
# bytes: an LSB (0..127) at the parameter's offset and an MSB flag one byte
# later that is 0x00 (clear) or 0x40 (set).  The value is simply:
#
#     value = LSB + (128 if MSB == 0x40 else 0)
#
# Verified byte-exact against real packs (e.g. YBC "Acid": mixer osc1 = 113,
# noise = 255; filter overdrive 47 / reso 81; env decay 24 / sustain 28; and
# the whole mod matrix).  The PDF's "MSB" is this flag byte, not a real MSB.
#
# Bipolar parameters (osc octave, mod-matrix depths) are stored centred on 64:
# the signed value is (stored - 64).  env->amp is the lone 0/1 boolean in the
# mod matrix.  Note the Components editor shows some knobs through a perceptual
# curve (filter cutoff, env release) and the pitch/cutoff mod depths at ~2x, so
# an editor's on-screen number can differ from this raw, linear decode.
#
# kind:  u     unsigned 0..127 (raw byte)
#        enum  small enumeration (raw byte); see *_VALUES below
#        bool  0/1 (raw byte)
#        oct   bipolar octave, value = raw - 64
#        pair  unsigned 0..255, LSB here + MSB flag at offset+1
#        mod   bipolar mod depth, value = raw - 64
#        modp  bipolar mod depth, value = LSB - 64, followed at offset+1 by a
#              redundant sign flag (0x40 when LSB>=64, else 0x00) that carries
#              no new information and is ignored here -- unlike a `pair`, whose
#              flag really is the high bit of a 0..255 value
PARAMS = [
    ("osc1_octave",        41, "oct"),
    ("osc1_waveform",      42, "enum"),
    ("osc1_pw",            43, "u"),
    ("osc1_coarse",        44, "pair"),
    ("osc1_fine",          46, "pair"),    # 0..100, centre 50
    ("osc1_range",         48, "enum"),    # 0..3 = 16'/8'/4'/2'
    ("osc2_octave",        50, "oct"),
    ("osc2_waveform",      51, "enum"),
    ("osc2_pw",            52, "u"),
    ("osc2_coarse",        53, "pair"),
    ("osc2_fine",          55, "pair"),
    ("osc2_range",         57, "enum"),
    ("osc_sync",           59, "bool"),
    ("mix_osc1",           61, "pair"),
    ("mix_osc2",           63, "pair"),
    ("mix_sub",            65, "pair"),
    ("mix_noise",          67, "pair"),
    ("mix_audio_in",       69, "pair"),
    ("mix_ring_mod",       71, "pair"),
    ("filter_overdrive",   73, "u"),
    ("filter_reso",        74, "u"),
    ("filter_cutoff",      75, "pair"),
    ("filter_shape",       77, "enum"),    # 0/1/2 = LP/BP/HP
    ("filter_slope",       78, "enum"),    # 0/1 = 12/24 dB
    ("filter_bypass",      79, "enum"),    # 0 none / 1 noise / 2 osc2 / 3 both
    ("filter_key_track",   80, "u"),       # 0..7
    ("env_attack",         81, "u"),
    ("env_decay",          82, "u"),
    ("env_sustain",        83, "u"),
    ("env_release",        84, "u"),
    ("lfo_waveform",       85, "enum"),
    ("lfo_rate",           86, "pair"),
    ("lfo_clock_sync_rate", 88, "u"),      # 0..34
    ("lfo_clock_sync",     89, "bool"),
    ("lfo_key_sync",       90, "bool"),
    ("dist_amount",        91, "u"),
    ("dist_type",          92, "enum"),    # 0..2
    ("mod_env_osc1_pitch", 93, "modp"),
    ("mod_env_osc1_pw",    95, "mod"),
    ("mod_env_osc2_pitch", 96, "modp"),
    ("mod_env_osc2_pw",    98, "mod"),
    ("mod_env_amp",        99, "bool"),
    ("mod_env_cutoff",    100, "modp"),
    ("mod_env_dist",      102, "mod"),
    ("mod_env_aux",       103, "mod"),
    ("mod_lfo_osc1_pitch", 104, "modp"),
    ("mod_lfo_osc1_pw",   106, "mod"),
    ("mod_lfo_osc2_pitch", 107, "modp"),
    ("mod_lfo_osc2_pw",   109, "mod"),
    ("mod_lfo_amp",       110, "mod"),
    ("mod_lfo_cutoff",    111, "modp"),
    ("mod_lfo_dist",      113, "mod"),
    ("mod_lfo_aux",       114, "mod"),
    ("mod_seq_osc1_pitch", 115, "modp"),
    ("mod_seq_osc1_pw",   117, "mod"),
    ("mod_seq_osc2_pitch", 118, "modp"),
    ("mod_seq_osc2_pw",   120, "mod"),
    ("mod_seq_amp",       121, "mod"),
    ("mod_seq_cutoff",    122, "modp"),
    ("mod_seq_dist",      124, "mod"),
    ("mod_seq_aux",       125, "mod"),
    ("mod_vel_osc1_pitch", 126, "mod"),
    ("mod_vel_osc1_pw",   127, "mod"),
    ("mod_vel_osc2_pitch", 128, "mod"),
    ("mod_vel_osc2_pw",   129, "mod"),
    ("mod_vel_amp",       130, "mod"),
    ("mod_vel_cutoff",    131, "mod"),
    ("mod_vel_dist",      132, "mod"),
    ("mod_vel_aux",       133, "mod"),
    ("selected_routing",  134, "enum"),    # 0..31
]


def _pair(msg, off):
    """Merge an LSB at off with the 0x00/0x40 MSB flag at off+1 -> 0..255."""
    return msg[off] + (128 if msg[off + 1] == 0x40 else 0)


def decode_param(msg, offset, kind):
    """Decode one parameter from a 136-byte message by its kind (see PARAMS)."""
    if kind in ("u", "enum", "bool"):
        return msg[offset]
    if kind == "oct":
        return msg[offset] - 64
    if kind == "pair":
        return _pair(msg, offset)
    if kind in ("mod", "modp"):
        return msg[offset] - 64
    raise ValueError("unknown kind %r" % kind)


def decode_params(msg):
    """Return {name: value} for every mapped parameter of a 136-byte message."""
    if len(msg) != MSG_SIZE:
        raise ValueError("expected %d bytes, got %d" % (MSG_SIZE, len(msg)))
    return {name: decode_param(msg, off, kind) for name, off, kind in PARAMS}


def read_pack(path):
    """Return the raw bank/patch bytes from a .monostationpack (ZIP) or .syx."""
    data = Path(path).read_bytes()
    if data[:2] == b"PK":
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            member = "patches.syx" if "patches.syx" in names else names[0]
            return z.read(member)
    return data


def split_bank(data):
    """Split a bank/patch blob into 136-byte messages, validating each."""
    if len(data) % MSG_SIZE:
        raise ValueError("length %d is not a multiple of %d" % (len(data), MSG_SIZE))
    msgs = []
    for i in range(0, len(data), MSG_SIZE):
        m = data[i:i + MSG_SIZE]
        if m[:4] != MFR or m[4:6] != DEVICE_ID:
            raise ValueError("message %d: bad header %s" % (i // MSG_SIZE, m[:6].hex()))
        if m[-1] != 0xF7:
            raise ValueError("message %d: missing F7 terminator" % (i // MSG_SIZE))
        msgs.append(m)
    return msgs


def decode_meta(msg):
    """Framing + name/category/genre of one 136-byte message (reliable fields)."""
    if len(msg) != MSG_SIZE:
        raise ValueError("expected %d bytes, got %d" % (MSG_SIZE, len(msg)))
    return {
        "message_type": msg[6],
        "slot": msg[7],
        "name": msg[NAME_OFF:NAME_OFF + NAME_LEN].decode("latin1").rstrip(),
        "category": msg[CATEGORY_OFF],
        "genre": msg[GENRE_OFF],
        "raw_params": bytes(msg[PARAMS_OFF:MSG_SIZE - 1]),
    }


def _fmt_type(t):
    return MSG_TYPES.get(t, "?%d" % t)


def category_name(c):
    return CATEGORIES[c] if c < len(CATEGORIES) else str(c)


def genre_name(g):
    return GENRES[g] if g < len(GENRES) else str(g)


def main(argv):
    if len(argv) == 3 and argv[1] == "list":
        data = read_pack(argv[2])
        msgs = split_bank(data)
        print("%d message(s), device id 01 61" % len(msgs))
        for m in msgs:
            d = decode_meta(m)
            print("  slot %2d  %-16s  type=%-13s category=%-9s genre=%s"
                  % (d["slot"], d["name"], _fmt_type(d["message_type"]),
                     category_name(d["category"]), genre_name(d["genre"])))
        return
    if len(argv) == 4 and argv[1] == "show":
        msgs = split_bank(read_pack(argv[2]))
        try:
            slot = int(argv[3])
        except ValueError:
            slot = next(i for i, m in enumerate(msgs)
                        if decode_meta(m)["name"] == argv[3])
        m = msgs[slot]
        d = decode_meta(m)
        print("slot %d  %r  category=%s genre=%s"
              % (d["slot"], d["name"], category_name(d["category"]),
                 genre_name(d["genre"])))
        for name, value in decode_params(m).items():
            print("  %-22s %d" % (name, value))
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
