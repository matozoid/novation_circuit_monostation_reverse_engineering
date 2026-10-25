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

Pattern = 16 steps x 8 bytes + 12-byte footer (footer[0] = last step index).
Automation lane = 12-byte mask (bit i, LSB first, = micro-step i recorded)
followed by 96 values (16 steps x 6 micro-steps).

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

MICRO_STEPS = 6
LANE_MASK_SIZE = STEPS * MICRO_STEPS // 8   # 12
LANE_SIZE = LANE_MASK_SIZE + STEPS * MICRO_STEPS   # 108

# (pattern base, pattern count, automation base, lanes per pattern)
TRACKS = (
    (0x00058, 16, 0x00918, 42),
    (0x12498, 8, 0x128F8, 12),
    (0x15178, 8, None, 0),
)

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
    """Return {'steps': [8-byte step records], 'footer': 12 bytes, 'last_step': n}."""
    base, count, _, _ = TRACKS[track]
    if not 0 <= index < count:
        raise IndexError(f"track {track} has {count} patterns")
    rec = image[base + index * PATTERN_SIZE:base + (index + 1) * PATTERN_SIZE]
    footer = rec[STEPS * STEP_SIZE:]
    return {
        "steps": [rec[i * STEP_SIZE:(i + 1) * STEP_SIZE] for i in range(STEPS)],
        "footer": footer,
        "last_step": footer[0],
    }


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
                    print(f"  pattern {i:2d} automated lanes {auto}")
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
