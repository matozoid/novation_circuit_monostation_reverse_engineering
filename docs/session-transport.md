# Session SysEx transport

How sessions travel over SysEx (#27). The layout of a single session image (#13) is in `docs/session-format.md`. Implementation: `tools/sessionsyx.py`. Reference dumps: the two `*Sessions*.syx` files in the YBC "Big City Flippn'Grooves" soundpack (3,484,838 bytes each).

## Messages

All messages are `F0 00 20 29 00 <cmd> <body…> F7`. A dump is a single transfer: one header, the data blocks in order, then one trailer.

| cmd | count (full dump) | length | body |
|-----|-------------------|--------|------|
| `0x77` | 1 | 23 | 16 nibbles: **start address** in the device's storage (`0x00012000`), then the length (`0x002D6000` = 2,973,696) |
| `0x79` | 11,616 | 300 | 293 septets → 256 bytes of stream, no address or index |
| `0x7A` | 1 | 15 | 8 nibbles: **CRC-32 (zlib/IEEE)** of the whole unpacked stream, most significant first |

Unlike the firmware container's `0x76` trailer, there is no `00` byte or name before the nibbles.

## Data block encoding

The 293 septets form groups of 8. The first septet of each group holds the high bits of the following 7 bytes: bit *i* is bit 7 of byte *i*. 293 = 36 × 8 + 5, so the last group is a high-bit septet plus 4 bytes, and a block carries 36 × 7 + 4 = **256 bytes**. This is a different scheme from both the firmware container (37-septet big-endian bitstream) and Bass Station II's 154-byte patch (`OX` bitstream, `docs/bass-station-ii.md`).

The byte after the `0x79` command is therefore the first high-bit septet, not a flag. It's usually `00` because most session bytes are below `0x80`.

## Stream contents

The unpacked stream is a concatenation of 90,112-byte session images (the `.monostationsession` layout, #13). A full dump holds **33** of them:

- slots 0–31: user sessions, magic `USER` at offset 0, name at `0x20` (32 bytes, space-padded; layout in `docs/session-format.md`);
- slot 32: an `INIT` image, byte-identical to Components' `session_32.monostationsession`.

In the two YBC dumps, sessions 0–15 differ between the packs and 16–31 plus the `INIT` image are identical. The session names are all left as the default `User Session`.

`tools/sessionsyx.py` decodes and re-encodes both reference dumps byte-for-byte, and it checks the length and CRC as it decodes.

## Address ranges (Components `k0`/`Yie`)

The header names a region of the device's storage, not a type:

| name | start | length | content |
|------|-------|--------|---------|
| `MS_ALL_PATCHES` | `0x2000` | `0x8000` | 64 patch slots × 512 bytes, 126 bytes used per slot (`chunkMonostationPatches`) |
| `MS_ALL_SESSIONS` | `0x12000` | `0x2D6000` | 33 × 90,112-byte session images (`chunkMonostationSessions`) |
| `MS_ONE_SESSION` | `0x12000` | `0x16000` | one image. Components only uses this to export a single session to a file (`make-session-file`), not to talk to the device. |

## Requesting and sending (Components `k0`, `decode-worker-*.js`)

These transfers are handled by the **bootloader**, not the app. The app's `F0 00 20 29 00` dispatcher handles only `0x60`/`0x61`/`0x70`/`0x71` and contains no `0x77`/`0x78`/`0x79` code; on firmware 800 hardware a `0x78` sent to the app gets no reply. Components' Mono Station packs page (`frontend/routes/monostation/packs/index`, `deviceMode="bootloader"`) reboots the device into the bootloader first with `F0 00 20 29 00 71 00 7F 00 00 F7` (`docs/update-protocol.md`). Play returns to the app.

- **Download:** the host sends `F0 00 20 29 00 78 <start: 8 nibbles> <length: 8 nibbles> F7` (command 120). The device answers with a `0x77` header that echoes the start and length; Components rejects any other values. The `0x79` blocks and the `0x7A` CRC trailer follow.
- **Upload:** the host sends the `0x77` header, then one `0x79` block each time the device sends a flow-control message. That message is either `0x7B` (8-nibble counter at bytes 6–13) or `0x75` (16-bit counter at bytes 7–8); a repeated counter value is ignored. At the end the device sends `0x7A` with its own CRC-32, and the host compares it with the CRC of what it sent.
- A patch-bank upload pads each 126-byte patch to 512 bytes (`patchLength`) before framing.

## Open

- Whether the device accepts other start/length values, such as `0x12000 + n × 0x16000` for one session, or a single patch slot. At least one non-Components range works: on hardware the bootloader answered `0x2000`/`0x10000` (all 128 patch slots) with a matching header and a valid CRC.
- What lives at `0x0`–`0x2000`. The region `0xA000`–`0x12000` is patch slots 64–127 (`docs/sysex-spec.md`, *Patch store*); on a stock unit every one of them holds `Initial Patch`.
- On Linux, receive big dumps from the ALSA rawmidi device (`amidi`, or `/dev/snd/midiC*D*`). Through the ALSA sequencer (rtmidi/mido) a 3 MB session dump lost 16 blocks to input overruns.
