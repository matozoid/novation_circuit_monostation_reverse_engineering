#!/usr/bin/env python3
"""Circuit Mono Station session SysEx transfer (see docs/session-transport.md).

A session dump is one transfer of three message kinds, all
`F0 00 20 29 00 <cmd> <body…> F7`:

    0x77  header   16 nibbles: unknown field (0x00012000), then total length
    0x79  data     293 septets -> 256 bytes, in order, no address
    0x7A  trailer  8 nibbles: CRC-32 (zlib) of the whole unpacked stream

Data packing: groups of 8 septets; the first septet of a group holds the high
bits of the next 7 bytes, bit i -> byte i. 293 = 36*8 + 5, so a block carries
36*7 + 4 = 256 bytes.

The unpacked stream is a concatenation of 90,112-byte `.monostationsession`
images: a full dump is 33 of them (32 user sessions, magic "USER", then one
"INIT" image identical to Components' session_32.monostationsession).

Usage:
    python3 tools/sessionsyx.py list  sessions.syx
    python3 tools/sessionsyx.py split sessions.syx outdir/   # session_NN.bin
    python3 tools/sessionsyx.py build outdir/session_*.bin out.syx
"""
import sys
import zlib
from pathlib import Path

NOVATION_HEADER = bytes([0xF0, 0x00, 0x20, 0x29, 0x00])

CMD_HEADER = 0x77
CMD_DATA = 0x79
CMD_TRAILER = 0x7A

HEADER_UNKNOWN = 0x00012000   # constant in every dump seen; meaning TBD
NIBBLES = 8

BLOCK_BYTES = 256
BLOCK_SEPTETS = 293
GROUP = 7                     # data bytes per high-bit septet

SESSION_SIZE = 90112
SESSION_NAME_OFF = 0x20
SESSION_NAME_LEN = 20


def split_messages(data):
    """Split a .syx byte string into a list of complete F0..F7 messages."""
    messages = []
    pos = 0
    while (start := data.find(0xF0, pos)) >= 0:
        end = data.find(0xF7, start)
        if end < 0:
            raise ValueError(f"unterminated SysEx message at offset {start}")
        messages.append(data[start:end + 1])
        pos = end + 1
    return messages


def command(message):
    if not message.startswith(NOVATION_HEADER):
        raise ValueError(f"not a Novation message: {message[:8].hex(' ')}")
    return message[len(NOVATION_HEADER)]


def to_nibbles(value):
    return bytes((value >> (4 * i)) & 0xF for i in reversed(range(NIBBLES)))


def from_nibbles(body):
    if len(body) != NIBBLES or any(b > 0xF for b in body):
        raise ValueError(f"bad nibble field: {body.hex(' ')}")
    return int("".join(f"{b:x}" for b in body), 16)


def decode_block(septets):
    """Unpack 293 septets (high-bit septet + 7 bytes per group) into 256 bytes."""
    if len(septets) != BLOCK_SEPTETS:
        raise ValueError(f"expected {BLOCK_SEPTETS} septets, got {len(septets)}")
    if any(s > 0x7F for s in septets):
        raise ValueError("SysEx data byte has high bit set")
    out = bytearray()
    for g in range(0, len(septets), GROUP + 1):
        high = septets[g]
        for i, s in enumerate(septets[g + 1:g + 1 + GROUP]):
            out.append(s | (0x80 if high >> i & 1 else 0))
    return bytes(out)


def encode_block(payload):
    """Pack 256 bytes into 293 septets (inverse of decode_block)."""
    if len(payload) != BLOCK_BYTES:
        raise ValueError(f"expected {BLOCK_BYTES} bytes, got {len(payload)}")
    out = bytearray()
    for g in range(0, len(payload), GROUP):
        chunk = payload[g:g + GROUP]
        out.append(sum(1 << i for i, b in enumerate(chunk) if b & 0x80))
        out += bytes(b & 0x7F for b in chunk)
    return bytes(out)


def decode_transfer(data):
    """Return (unknown_field, stream) from a session .syx; checks length and CRC."""
    messages = split_messages(data)
    cmds = [command(m) for m in messages]
    if cmds[0] != CMD_HEADER or cmds[-1] != CMD_TRAILER or \
            any(c != CMD_DATA for c in cmds[1:-1]):
        raise ValueError("expected one 0x77 header, 0x79 data blocks, one 0x7A trailer")
    body = messages[0][len(NOVATION_HEADER) + 1:-1]
    unknown, length = from_nibbles(body[:NIBBLES]), from_nibbles(body[NIBBLES:])
    stream = b"".join(decode_block(m[len(NOVATION_HEADER) + 1:-1]) for m in messages[1:-1])
    if len(stream) != length:
        raise ValueError(f"header length {length} != unpacked {len(stream)}")
    crc = from_nibbles(messages[-1][len(NOVATION_HEADER) + 1:-1])
    if crc != zlib.crc32(stream):
        raise ValueError(f"CRC mismatch: trailer {crc:08x}, stream {zlib.crc32(stream):08x}")
    return unknown, stream


def encode_transfer(stream, unknown=HEADER_UNKNOWN):
    """Build a session .syx from an unpacked stream (inverse of decode_transfer)."""
    if len(stream) % BLOCK_BYTES:
        raise ValueError(f"stream length {len(stream)} is not a multiple of {BLOCK_BYTES}")

    def message(cmd, body):
        return NOVATION_HEADER + bytes([cmd]) + body + b"\xF7"

    out = [message(CMD_HEADER, to_nibbles(unknown) + to_nibbles(len(stream)))]
    out += [message(CMD_DATA, encode_block(stream[i:i + BLOCK_BYTES]))
            for i in range(0, len(stream), BLOCK_BYTES)]
    out.append(message(CMD_TRAILER, to_nibbles(zlib.crc32(stream))))
    return b"".join(out)


def split_sessions(stream):
    if len(stream) % SESSION_SIZE:
        raise ValueError(f"stream length {len(stream)} is not a multiple of {SESSION_SIZE}")
    return [stream[i:i + SESSION_SIZE] for i in range(0, len(stream), SESSION_SIZE)]


def session_name(image):
    return image[SESSION_NAME_OFF:SESSION_NAME_OFF + SESSION_NAME_LEN].decode("latin1").rstrip()


def main(argv):
    if len(argv) == 3 and argv[1] == "list":
        unknown, stream = decode_transfer(Path(argv[2]).read_bytes())
        sessions = split_sessions(stream)
        print(f"{len(sessions)} session(s), {len(stream)} bytes, "
              f"header field 0x{unknown:08x}, CRC-32 ok")
        for i, s in enumerate(sessions):
            print(f"  {i:2d}  {s[:4].decode('latin1')}  {session_name(s)}")
        return
    if len(argv) == 4 and argv[1] == "split":
        _, stream = decode_transfer(Path(argv[2]).read_bytes())
        outdir = Path(argv[3])
        outdir.mkdir(parents=True, exist_ok=True)
        for i, s in enumerate(split_sessions(stream)):
            (outdir / f"session_{i:02d}.bin").write_bytes(s)
        return
    if len(argv) >= 4 and argv[1] == "build":
        stream = b"".join(Path(p).read_bytes() for p in argv[2:-1])
        Path(argv[-1]).write_bytes(encode_transfer(stream))
        return
    sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
