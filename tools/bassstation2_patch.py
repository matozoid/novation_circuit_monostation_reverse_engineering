#!/usr/bin/env python3
"""Decode / encode a Novation Bass Station II patch SysEx message.

This is NOT a Circuit Mono Station format: Components 1.72.5 keeps it in its
bass-station-ii modules. Mono Station patches are 136 bytes (tools/patchbank.py).
Kept as a reference; see docs/bass-station-ii.md.

The current-patch message is 154 bytes:
    [0..5]   fixed header  F0 00 20 29 00 33
    [6]      protocol version (0)
    [7]      message type
    [8]      patch slot / index
    [9..152] 144-byte payload, 7-bit MIDI-packed (8 packed bytes -> 7 data bytes)
    [153]    F7
The payload unpacks to 126 data bytes; named parameters live at byte positions
7..99 (see tools/bassstation2_params.tsv, from Components 1.72.5). Bytes
100..125 hold the patch name / category (exact split not worked out).

The 7->8 packing matches Components' RX/OX: the payload is one MSB-first bit
stream of 7-bit bytes, regrouped into 8-bit bytes. (A legacy 122-byte message
also exists; Components' JX pads it to 154. Not implemented here.)

Usage:
    python3 tools/bassstation2_patch.py decode patch.syx   # print named params
    python3 tools/bassstation2_patch.py selftest                  # codec round-trip
"""
import csv
import sys
from pathlib import Path

HEADER_SIZE = 9
SYSEX_SIZE = 154
PAYLOAD_PACKED = 144          # bytes 9..152
PAYLOAD_UNPACKED = 126        # 144 * 7 // 8
SCHEMA = Path(__file__).with_name("bassstation2_params.tsv")


def unpack_7to8(packed):
    """7-bit MIDI payload -> 8-bit data bytes (Components RX/OX)."""
    if len(packed) * 7 % 8:
        raise ValueError("packed length must make a whole number of bytes")
    bits = 0
    nbits = 0
    out = bytearray()
    for b in packed:
        if b > 0x7F:
            raise ValueError("payload byte %#x is not 7-bit" % b)
        bits = (bits << 7) | b
        nbits += 7
        while nbits >= 8:
            nbits -= 8
            out.append((bits >> nbits) & 0xFF)
    return bytes(out)


def pack_8to7(data):
    """8-bit data bytes -> 7-bit MIDI payload (inverse; Components LX)."""
    if len(data) * 8 % 7:
        raise ValueError("data length must make a whole number of septets")
    bits = 0
    nbits = 0
    out = bytearray()
    for b in data:
        bits = (bits << 8) | b
        nbits += 8
        while nbits >= 7:
            nbits -= 7
            out.append((bits >> nbits) & 0x7F)
    return bytes(out)


def load_schema():
    rows = []
    with open(SCHEMA) as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            rows.append(line)
    reader = csv.DictReader(rows, delimiter="\t")
    out = []
    for r in reader:
        r["patchIndex"] = int(r["patchIndex"])
        r["rs"] = int(r["rs"] or 0)
        r["min"] = int(r["min"] or 0)
        r["max"] = int(r["max"] or 0)
        out.append(r)
    return out


def decode_value(raw, dt, rs, mask):
    """Interpret an unpacked byte per its data type (Components UX)."""
    if dt == "DT_B":
        return bool((raw >> rs) & 1)
    if dt == "DT_M":
        return raw & mask
    if dt == "DT_S":
        return raw - 64
    if dt == "DT_S8":
        return raw - 128
    return raw  # DT_U, DT_U8


def decode_patch(message):
    if len(message) != SYSEX_SIZE:
        raise ValueError("expected %d bytes, got %d" % (SYSEX_SIZE, len(message)))
    payload = message[HEADER_SIZE:HEADER_SIZE + PAYLOAD_PACKED]
    data = unpack_7to8(payload)
    params = {}
    for p in load_schema():
        raw = data[p["patchIndex"]]
        params[p["name"]] = decode_value(raw, p["dt"], p["rs"], p["min"])
    return {
        "protocol_version": message[6],
        "message_type": message[7],
        "slot": message[8],
        "params": params,
        "name_region": bytes(data[100:126]),
    }


def _selftest():
    import os
    for _ in range(1000):
        data = bytes(os.urandom(PAYLOAD_UNPACKED))
        packed = pack_8to7(data)
        assert len(packed) == PAYLOAD_PACKED, len(packed)
        assert all(b <= 0x7F for b in packed)
        assert unpack_7to8(packed) == data
    # schema sanity
    schema = load_schema()
    assert len(schema) == 85, len(schema)
    assert max(p["patchIndex"] for p in schema) < PAYLOAD_UNPACKED
    print("selftest OK: codec round-trips, %d params, max index %d"
          % (len(schema), max(p["patchIndex"] for p in schema)))


def main(argv):
    if len(argv) >= 2 and argv[1] == "selftest":
        _selftest()
        return
    if len(argv) == 3 and argv[1] == "decode":
        msg = Path(argv[2]).read_bytes()
        info = decode_patch(msg)
        print("protocol v%d  type=%d  slot=%d" %
              (info["protocol_version"], info["message_type"], info["slot"]))
        for name, value in info["params"].items():
            print("  %-24s %s" % (name, value))
        print("name region:", info["name_region"])
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
