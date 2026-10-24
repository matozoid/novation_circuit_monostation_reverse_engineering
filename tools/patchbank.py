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

Framing, name, category, genre and slot are decoded here and are reliable.
Per-parameter value decoding (the 0x40 split-pairing in bytes 27..134) is not
yet implemented byte-exact; raw_params exposes those bytes for analysis.

Usage:
    python3 tools/patchbank.py list Roboo.monostationpack
    python3 tools/patchbank.py list patches.syx
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
CATEGORIES = {1: "Bass", 3: "Drum/Percussion"}   # partial; full enum TBD


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


def _fmt_cat(c):
    return CATEGORIES.get(c, str(c))


def main(argv):
    if len(argv) == 3 and argv[1] == "list":
        data = read_pack(argv[2])
        msgs = split_bank(data)
        print("%d message(s), device id 01 61" % len(msgs))
        for m in msgs:
            d = decode_meta(m)
            print("  slot %2d  %-16s  type=%-13s category=%-14s genre=%d"
                  % (d["slot"], d["name"], _fmt_type(d["message_type"]),
                     _fmt_cat(d["category"]), d["genre"]))
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
