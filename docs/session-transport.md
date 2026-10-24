# Session SysEx transport

How sessions travel over SysEx (#27). This is separate from the layout of a single session image (#13). Implementation: `tools/sessionsyx.py`. Reference dumps: the two `*Sessions*.syx` files in the YBC "Big City Flippn'Grooves" soundpack (3,484,838 bytes each).

## Messages

All messages are `F0 00 20 29 00 <cmd> <body…> F7`. A dump is a single transfer: one header, the data blocks in order, then one trailer.

| cmd | count (full dump) | length | body |
|-----|-------------------|--------|------|
| `0x77` | 1 | 23 | 16 nibbles: an unknown 32-bit field (`0x00012000` in every dump seen), then the unpacked stream length (`0x002D6000` = 2,973,696) |
| `0x79` | 11,616 | 300 | 293 septets → 256 bytes of stream, no address or index |
| `0x7A` | 1 | 15 | 8 nibbles: **CRC-32 (zlib/IEEE)** of the whole unpacked stream, most significant first |

Unlike the firmware container's `0x76` trailer, there is no `00` byte or name before the nibbles.

## Data block encoding

The 293 septets form groups of 8. The first septet of each group holds the high bits of the following 7 bytes: bit *i* is bit 7 of byte *i*. 293 = 36 × 8 + 5, so the last group is a high-bit septet plus 4 bytes, and a block carries 36 × 7 + 4 = **256 bytes**. This is a different scheme from both the firmware container (37-septet big-endian bitstream) and the 154-byte patch (`OX` bitstream).

The byte after the `0x79` command is therefore the first high-bit septet, not a flag. It's usually `00` because most session bytes are below `0x80`.

## Stream contents

The unpacked stream is a concatenation of 90,112-byte session images (the `.monostationsession` layout, #13). A full dump holds **33** of them:

- slots 0–31: user sessions, magic `USER` at offset 0, name at `0x20` (20 bytes, space-padded);
- slot 32: an `INIT` image, byte-identical to Components' `session_32.monostationsession`.

In the two YBC dumps, sessions 0–15 differ between the packs and 16–31 plus the `INIT` image are identical. The session names are all left as the default `User Session`.

`tools/sessionsyx.py` decodes and re-encodes both reference dumps byte-for-byte, and it checks the length and CRC as it decodes.

## Open

- The meaning of the header's `0x00012000` field. It may be a format version (`0 0 0 1 2 0 0 0` read as digits) or a size: 73,728 = 0x12000 bytes, which is 16 KB short of one session image.
- Whether the device accepts a partial transfer, for example one session, and how it would choose the target slot. No such transfer has been seen yet.
- How a transfer is requested from the device (#19).
