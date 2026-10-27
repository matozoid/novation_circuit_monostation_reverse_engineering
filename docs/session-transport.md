# Session SysEx transport

How sessions travel over SysEx. The layout of a single session image is in `docs/session-format.md`. Implementation: `tools/sessionsyx.py`. Reference dumps: the two `*Sessions*.syx` files in the YBC "Big City Flippn'Grooves" soundpack (3,484,838 bytes each).

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

The unpacked stream is a concatenation of 90,112-byte session images (the `.monostationsession` layout, `docs/session-format.md`). A full dump holds **33** of them:

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

## Storage map (read on hardware, 2026-10-25)

From `0x78` dumps of a stock unit (bootloader 740, firmware 800), cross-checked with `p 0x40` op 2 reads in the app:

| range | content |
|-------|---------|
| `0x0000`–`0x0043` | 32 little-endian u16 values (100, 131, 131, 131, 124, 134, …, mostly near 128), then their **zlib CRC-32** (LE) at `0x40`. The per-unit **calibration table**, written by the factory calibration runner (`power_calib_task`, `calibration_crc`, `nor_async_write`; `docs/source-map.md`). **Never erase or overwrite it**; back it up first. |
| `0x0044`–`0x0FFF` | `FF` |
| `0x1000`–`0x1017` | the 24-byte **System Settings record**: `08 00 00 00 00 00 00 00 2E 02 00 00 00 00 00 00 01 00 00 00 FF 00 00 00` on this unit. Layout below. |
| `0x1018`–`0x1FFF` | `FF` |
| `0x2000`–`0x12000` | 128 patch slots × 512 bytes. On this unit, slots 64–127 all hold `Initial Patch`. |
| `0x12000`–`0x2E8000` | 33 session images |
| `0x2E8000`–`0x1000000` | all `FF` (scanned completely). There is no factory bank in external flash. |

The bootloader returned `FF` at `0x1000000`, `0x2000000` and `0x7FFFF000`, so the read doesn't wrap at 16 MiB. **It doesn't map MCU memory:** `0x0800C000` (the app's vector table, the control), `0x08000000` (bootloader), `0x1FFF0000`, `0x1FFF7800`, `0x1FFFC000`, `0x10000000`, `0x20000000` and `0x40024000` all came back as valid transfers full of `00`. Why these give `00` and the others `FF` is unknown. The app's `p 0x40` read stops at `0x800000` (see `docs/sysex-handlers.md`), which suggests an 8 MiB (64 Mbit) chip.

### Settings record (`0x1000`)

The System Settings page (hold Shift while switching on; Play exits) is stored here. Mapped on hardware by changing one setting per power cycle and reading the record with `p 0x40` op 2 after each one. The record is written **only at power-off**: after Play it was still unchanged, and after a power cycle it had changed. The boot code `FUN_0800e1f8` reads it with `FUN_0801aef4(1, 0, buf, 0x18)` (sector 1, offset 0, 24 bytes).

| offset | content |
|--------|---------|
| `0x00` | `08`. Unknown. At boot, `FUN_0800e1f8` checks it: if it is above `0x1F`, a field gets cleared, which reads like a sanity check for an erased (`FF`) record. |
| `0x08` | flags. bit 2 = Note Tx on, bit 3 = Note Rx on, bit 4 = Clock Rx **off**, bit 5 = Clock Tx **off**, bit 6 = CC Tx **off**, bit 7 = CC Rx **off**. Bit 1 was set in every reading and bit 0 was always clear; their meaning is unknown. |
| `0x09` | bits 0–2 = clock out rate (pad 18, 2 ppqn → 2; pad 20, 8 ppqn → 4; so probably pads 17–21 → 1–5), bit 3 = pot pickup on |
| `0x0C` | Osc 1 MIDI channel, 0-based (ch 3 → `02`) |
| `0x10` | Osc 2 MIDI channel, 0-based. Inferred: it reads `01` while Osc 2 is on ch 2, but it wasn't changed during the test. |
| `0x14` | `FF`. Unknown. |
| others | `00` |

The Rx/Tx flags mix polarities: Note Rx/Tx are "on" bits, while CC and Clock are "off" bits. The defaults (Note, CC and Clock Rx on, Clock Tx off) therefore read `0x2E`. Readings are in `~/ms-dumps2/settings-1000.tsv`. The unit was restored afterwards, and the final record matches the first one byte for byte.

## Requesting and sending (Components `k0`, `decode-worker-*.js`)

These transfers are handled by the **bootloader**, not the app. The app's `F0 00 20 29 00` dispatcher handles only `0x60`/`0x61`/`0x70`/`0x71` and contains no `0x77`/`0x78`/`0x79` code; on firmware 800 hardware a `0x78` sent to the app gets no reply. Components' Mono Station packs page (`frontend/routes/monostation/packs/index`, `deviceMode="bootloader"`) reboots the device into the bootloader first with `F0 00 20 29 00 71 00 7F 00 00 F7` (`docs/update-protocol.md`). Play returns to the app.

- **Download:** the host sends `F0 00 20 29 00 78 <start: 8 nibbles> <length: 8 nibbles> F7` (command 120). The device answers with a `0x77` header that echoes the start and length; Components rejects any other values. The `0x79` blocks and the `0x7A` CRC trailer follow.
- **Upload:** the host sends the `0x77` header, then one `0x79` block each time the device sends a flow-control message. That message is either `0x7B` (8-nibble counter at bytes 6–13) or `0x75` (16-bit counter at bytes 7–8); a repeated counter value is ignored. At the end the device sends `0x7A` with its own CRC-32, and the host compares it with the CRC of what it sent.
- A patch-bank upload pads each 126-byte patch to 512 bytes (`patchLength`) before framing.

## Open

- **Resolved (hardware): the bootloader takes any start and length.** Each of these came back with a matching header and a valid CRC: one slot (`0x2000`/`0x200`), one session (`0x12000`/`0x16000`), all 128 slots (`0x2000`/`0x10000`), `0x0`/`0x2000`, and 4 KiB to 1 MiB reads anywhere up to `0x7FFFF000`.
- What the `0x0`–`0x44` calibration-like table means, and the unknown bytes of the settings record at `0x1000` (see *Storage map*).
- On Linux, receive big dumps from the ALSA rawmidi device (`amidi`, or `/dev/snd/midiC*D*`). Through the ALSA sequencer (rtmidi/mido) a 3 MB session dump lost 16 blocks to input overruns. Poll the rawmidi device before sending the request: input starts on the first poll or read, and the fast `0x77` header was lost once when the request went out first.
