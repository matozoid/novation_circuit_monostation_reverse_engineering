#!/usr/bin/env python3
"""Circuit Mono Station session image (.monostationsession), see docs/session-format.md.

A session image is 90,112 bytes: the 0x15FFC-byte session struct the firmware
builds in FUN_08018CA4, then 4 bytes of 0xFF. Layout:

    0x00000  header: magic u32 LE ("INIT"/"USER"/"DEMO"), struct size 0x15FFC,
             flags u32 @8, pad colour u8 @12, name @0x20 (32 bytes)
    0x00040  globals: tempo @0x40, swing @0x41; per-track chain at 0x4C/0x50/0x54
    0x00058  track 0: 16 patterns x 140 bytes
    0x00918  track 0 automation: 16 patterns x 42 lanes x 108 bytes
    0x12498  track 1: 8 patterns x 140 bytes
    0x128F8  track 1 automation: 8 patterns x 12 lanes x 108 bytes
    0x15178  track 2: 8 patterns x 140 bytes (no automation)
    0x155D8  7 bytes of further globals (meaning TBD)
    0x15F5C  the session's patch: 126 bytes = bytes 9..134 of a 136-byte patch

Pattern = 16 steps x 8 bytes + 12-byte footer: footer[0] = start step << 4 |
end step, footer[1] = sync rate index (RATE_TICKS), footer[2] = direction.
Automation lane = 12-byte mask (bit i, LSB first, = micro-step i recorded)
followed by 96 values (16 steps x 6 micro-steps).

Track 0 is the synth/osc 1 track, track 1 the osc 2 track, track 2 the mod
sequence. Each automation lane drives one firmware parameter id (LANE_IDS,
from the firmware tables at 0x08027588 / 0x0802775C); a parameter id names a
byte of the patch (PARAM_OFFSETS, from the id table at 0x0802A62A).

Usage:
    python3 tools/session.py info session.bin
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import patchbank  # noqa: E402

SESSION_SIZE = 90112
STRUCT_SIZE = 0x15FFC

PROJECT_TYPES = {b"INIT": "init", b"USER": "user", b"DEMO": "demo"}

FLAGS_OFF = 8
COLOUR_OFF = 12
NAME_OFF = 0x20
NAME_LEN = 32
TEMPO_OFF = 0x40
SWING_OFF = 0x41
CHAIN_OFFS = (0x4C, 0x50, 0x54)       # (start, end) pattern index per track

STEPS = 16
STEP_SIZE = 8
PATTERN_SIZE = STEPS * STEP_SIZE + 12   # 140

# Footer byte 1 indexes this ticks-per-step table (24 PPQN; firmware table at
# 0x08029F6C, read by playback FUN_080234AC and song position FUN_0801B698).
RATE_TICKS = (2, 3, 4, 6, 8, 12, 16, 24)
RATE_NAMES = ("1/32T", "1/32", "1/16T", "1/16", "1/8T", "1/8", "1/4T", "1/4")
# Footer byte 2; ping-pong doubles the pattern's length in FUN_0801B698.
DIRECTIONS = ("forward", "reverse", "ping-pong", "random")

MICRO_STEPS = 6
LANE_MASK_SIZE = STEPS * MICRO_STEPS // 8   # 12
LANE_SIZE = LANE_MASK_SIZE + STEPS * MICRO_STEPS   # 108

# (pattern base, pattern count, automation base, lanes per pattern)
TRACKS = (
    (0x00058, 16, 0x00918, 42),
    (0x12498, 8, 0x128F8, 12),
    (0x15178, 8, None, 0),
)

# Firmware parameter id -> offset of its byte in the 136-byte patch message
# (= patch body offset + 9).  Id table at 0x0802A62A, 8 bytes per id: body
# offset, 0, kind, centre, min, max, NRPN MSB (0xFF = CC, 0xFC = CC + LSB CC),
# CC/NRPN LSB.  76 ids; the setter FUN_0801C842 also takes 0x4C, 0x4D and
# 0x50 (STEP_PATCH_ID).
PARAM_OFFSETS = (
    25, 26, 27, 41, 42, 43, 44, 46, 48, 49, 50, 51, 52, 53, 55, 57,
    58, 59, 60, 61, 63, 65, 67, 69, 71, 73, 74, 75, 77, 78, 79, 80,
    81, 82, 83, 84, 85, 86, 88, 89, 90, 91, 92, 93, 95, 96, 98, 99,
    100, 102, 103, 104, 106, 107, 109, 110, 111, 113, 114, 115, 117, 118, 120, 121,
    122, 124, 125, 126, 127, 128, 129, 130, 131, 132, 133, 134,
)

# Parameter id driven by each automation lane, per track (FUN_08020EA0 args).
LANE_IDS = (
    (0x06, 0x07, 0x05, 0x13, 0x15, 0x16, 0x17, 0x18, 0x19, 0x1A, 0x1B, 0x29,
     0x25, 0x26, 0x20, 0x21, 0x22, 0x23, 0x2B, 0x2C, 0x2F, 0x30, 0x31, 0x32,
     0x33, 0x34, 0x37, 0x38, 0x39, 0x3A, 0x3B, 0x3C, 0x3F, 0x40, 0x41, 0x42,
     0x43, 0x44, 0x47, 0x48, 0x49, 0x4A),
    (0x0D, 0x0E, 0x0C, 0x14, 0x2D, 0x2E, 0x35, 0x36, 0x3D, 0x3E, 0x45, 0x46),
    (),
)

# Parameter id that step byte 3 drives on each track (0xFF in the step = none).
STEP_PARAM_IDS = (0x09, 0x10, 0x4C)

# Step byte 4 is sent as this id: patch change to slot 0..63 (0xFF = none).
# The session controller loads the slot from the patch store at
# 0x2000 + slot * 0x200 into the session's patch (handler FUN_08019AE4).
STEP_PATCH_ID = 0x50

GLOBALS2_OFF = 0x155D8
GLOBALS2_LEN = 7
PATCH_OFF = 0x15F5C
PATCH_LEN = 126


def check(image):
    if len(image) != SESSION_SIZE:
        raise ValueError(f"expected {SESSION_SIZE} bytes, got {len(image)}")
    if image[:4] not in PROJECT_TYPES:
        raise ValueError(f"unknown session magic {image[:4]!r}")


def header(image):
    check(image)
    return {
        "type": PROJECT_TYPES[image[:4]],
        "struct_size": int.from_bytes(image[4:8], "little"),
        "flags": int.from_bytes(image[FLAGS_OFF:FLAGS_OFF + 4], "little"),
        "colour": image[COLOUR_OFF],
        "name": image[NAME_OFF:NAME_OFF + NAME_LEN].decode("latin1").rstrip(),
        "tempo": image[TEMPO_OFF],
        "swing": image[SWING_OFF],
        "chains": [(image[o], image[o + 1]) for o in CHAIN_OFFS],
    }


def pattern(image, track, index):
    """Return the steps (8-byte records), raw footer and decoded footer fields.

    start_step..end_step is the played range; end < start wraps past step 15.
    """
    base, count, _, _ = TRACKS[track]
    if not 0 <= index < count:
        raise IndexError(f"track {track} has {count} patterns")
    rec = image[base + index * PATTERN_SIZE:base + (index + 1) * PATTERN_SIZE]
    footer = rec[STEPS * STEP_SIZE:]
    return {
        "steps": [rec[i * STEP_SIZE:(i + 1) * STEP_SIZE] for i in range(STEPS)],
        "footer": footer,
        "start_step": footer[0] >> 4,
        "end_step": footer[0] & 0x0F,
        "length": pattern_length(footer[0]),
        "rate": footer[1],
        "direction": footer[2],
    }


def pattern_length(start_end):
    """Steps played for a footer[0] byte (firmware FUN_08016A5A)."""
    start, end = start_end >> 4, start_end & 0x0F
    return end + 1 - start + (STEPS if end < start else 0)


def lane(image, track, index, lane_no):
    """Return (mask, values): mask is a 96-bit int, values a 96-byte string."""
    _, count, auto_base, lanes = TRACKS[track]
    if auto_base is None or not 0 <= index < count or not 0 <= lane_no < lanes:
        raise IndexError(f"no automation lane {track}/{index}/{lane_no}")
    off = auto_base + (index * lanes + lane_no) * LANE_SIZE
    mask = int.from_bytes(image[off:off + LANE_MASK_SIZE], "little")
    return mask, image[off + LANE_MASK_SIZE:off + LANE_SIZE]


def automated_lanes(image, track, index):
    lanes = TRACKS[track][3]
    return [n for n in range(lanes) if lane(image, track, index, n)[0]]


_PARAM_NAMES = {off: name for name, off, _ in patchbank.PARAMS}


def param_name(param_id):
    """Name a firmware parameter id after its 136-byte patch field."""
    if param_id < len(PARAM_OFFSETS):
        off = PARAM_OFFSETS[param_id]
        return _PARAM_NAMES.get(off, f"patch[{off}]")
    return f"id 0x{param_id:02x}"


def lane_param(track, lane_no):
    """Return (param id, name) of the parameter an automation lane drives."""
    param_id = LANE_IDS[track][lane_no]
    return param_id, param_name(param_id)


def patch_message(image, slot=0):
    """Wrap the session's patch as a 136-byte bank message for patchbank."""
    body = image[PATCH_OFF:PATCH_OFF + PATCH_LEN]
    return (patchbank.MFR + patchbank.DEVICE_ID
            + bytes([1, slot, 0]) + body + b"\xF7")


def main(argv):
    if len(argv) == 3 and argv[1] == "info":
        image = Path(argv[2]).read_bytes()
        h = header(image)
        print(f"{h['type']}  {h['name']!r}  tempo={h['tempo']} swing={h['swing']} "
              f"flags=0x{h['flags']:x} colour={h['colour']}")
        print(f"patch: {patchbank.decode_meta(patch_message(image))['name']!r}")
        for t, (_, count, _, _) in enumerate(TRACKS):
            start, end = h["chains"][t]
            used = [i for i in range(count)
                    if any(s[0] for s in pattern(image, t, i)["steps"])]
            print(f"track {t}: chain {start}-{end}, patterns with notes {used}")
            for i in range(count):
                auto = automated_lanes(image, t, i) if TRACKS[t][2] else []
                if auto:
                    names = ", ".join(lane_param(t, n)[1] for n in auto)
                    print(f"  pattern {i:2d} automated: {names}")
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
