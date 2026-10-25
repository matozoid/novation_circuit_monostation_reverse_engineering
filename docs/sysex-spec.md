# Circuit Mono Station patch SysEx — spec

This supersedes the community PDF (`CircuitMonoStationPatchSysExFormat.pdf`, rev. 2020-06-30), which documents the same format. It is derived from Novation **Components 1.72.5** (`app.asar` → `main-*.js`), from a real 64-patch bank (`.monostationpack`), and from the two default patches in firmware 800.

**The Mono Station has one patch format: 136 bytes** (`F0 00 20 29 01 61 …`). The same message is used on the wire, in `.monostationpack` files, and, minus the framing, in firmware memory. Components' Mono Station modules know only this format:
- the patch model, `frontend/models/monostation/patch`;
- the validator, `frontend/utils/monostation/validate-patch-sysex`, which accepts exactly 136 bytes.

> **Not Mono Station:** the "154-byte modern wire", the "122-byte legacy wire", the `kD` parameter array and the `RX`/`OX`/`UX` codec all belong to Components' **Bass Station II** modules (#26). They are kept as a reference in `docs/bass-station-ii.md` and `tools/bassstation2_patch.py`.

The Mono Station **device id (bytes 4–5) is `01 61`** (validator `yte`: `t[4]===1 && t[5]===97`; confirmed by the bank and the firmware). The original Novation **Circuit** uses `01 60`, and Bass Station II uses `00 33`.

## Components' view

The model's `toSysex(type, slot)` builds `F0 00 20 29 01 61 <type> <slot> 00` + the 126-byte patch + `F7`. Its `sysex` getter uses type `1` and the slot index. Loading a message keeps bytes 9–134.

The model maps only three fields of the 126-byte patch:
- `name`, at patch offset 0 (message byte 9);
- `category`, at offset 16 (byte 25);
- `genre`, at offset 17 (byte 26).

Components' init patch (`pR`, module `frontend/constants/monostation/init-patch`, 126 bytes) is byte-identical to the firmware's `Initial Patch` at `0x08028F6B`.

## The 136-byte bank/pack format (device `01 61`)

This is the format the hardware actually stores and the PDF documents. One patch = **136 bytes**; a full bank = 64 × 136 = **8704 bytes**. A `.monostationpack` file is a **ZIP** archive containing a single member `patches.syx` holding the 8704-byte bank. Components validates a bank as `8704===byteLength && yte(slice(0,136))` then splits on `F0…F7` (`JZ`); `yte` accepts message type (byte 6) `0` or `1`.

Per 136-byte message:

| Bytes | Field |
|-------|-------|
| 0–3 | `F0 00 20 29` |
| 4–5 | `01 61` device id |
| 6 | message type: `0` = current patch, `1` = slot write/dump |
| 7 | slot index (0–63 in a bank) |
| 8 | `00` |
| 9–24 | patch name, 16 ASCII, space-padded |
| 25 | category index: 0 None, 1 Arp, 2 Bass, 3 Bell, 4 Classic, 5 Drum, 6 Keyboard, 7 Lead, 8 Movement, 9 Pad, 10 Poly, 11 SFX, 12 String, 13 User, 14 Voc/Tune |
| 26 | genre index: 0 None, 1 Classic, 2 D&B/Breaks, 3 House, 4 Industrial, 5 Jazz, 6 R&B/HHop, 7 Rock/Pop, 8 Techno, 9 Dubstep |

The category and genre tables come from Components' Circuit-family librarian (modules `frontend/constants/circuit/patch-categories` and `patch-genres`, JS names `mP`/`gP`; the sidebar binds `categoryOptions=mP`, `genreOptions=gP`). The similar lists `kR`/`Wte` (`Motion`, `User 1`, `User 2`) belong to Peak, not the Circuit family. Real packs use the field loosely: Roboo tags its basses `1` (Arp) and its drums `3` (Bell), the YBC banks use only 0–3, and every genre seen is `0`. Treat the byte as a free user tag, not a reliable description of the sound.
| 27–40 | reserved / unmapped (`00` in the init patch; `27` = env retrigger/legato per the PDF) |
| 41–134 | parameters; 8-bit ones split over two bytes (high 7 bits, then the low bit as `0x00`/`0x40`, see below) — offsets per the community PDF (osc1/osc2 from 41, mixer, filter, env, LFO, distortion, 93–133 mod matrix, 134 selected routing) |
| 135 | `F7` |

Components treats the patch as an opaque blob apart from the name, category and genre (see *Components' view*). It doesn't name the synth parameters. The PDF's offset table and the firmware tables are the source for those.

### Split-pair decode (resolved)

Every parameter that the firmware sends as a CC plus a second CC 32 higher (`CC(8)` in Novation's MIDI parameter PDF) has an 8-bit value and takes two bytes in the patch. The firmware's patch store `FUN_080176e8` writes them as

```
byte[off]     = value >> 1          (0..127)
byte[off + 1] = (value & 1) << 6    (0x00 or 0x40)
```

and reads them back as `value = byte[off] << 1 | byte[off + 1] >> 6`. The MIDI-out encoder `FUN_08018B90` splits the value the same way across the two CCs. The PDF's "MSB" byte is therefore the value's **low bit**, not a high bit.

The parameter table at `0x0802A62A` (`docs/session-format.md`) gives each parameter's kind and centre, so the signed ones are decoded by subtracting it:

| params | stored | value |
|--------|--------|-------|
| mixer levels, filter cutoff, LFO rate | 2 bytes | 0–255 |
| osc coarse tune; env/LFO/seq → osc pitch and → cutoff depths | 2 bytes | `v − 128`, −128…127 |
| osc fine tune | 2 bytes | `v − 100`, −100…100 (MIDI 0–200) |
| osc octave and pulse width, the other mod depths | 1 byte | `raw − 64` |
| everything else | 1 byte | raw: 0–127, enum or bool |

Checked three ways:
- the firmware's `Initial Patch` stores coarse `40 00` and fine `32 00`, i.e. 128 and 100, the table's centres, and LFO rate `22 00` = 68, the PDF's default;
- in every pack seen, fine tune never exceeds 200;
- the YBC "Acid" screenshot shows osc 1 coarse −128, mixer 226/79/255, env → filter +13, seq → osc 2 pitch +127, seq → filter −51 and LFO rate 68, which are exactly the decoded values.

`tests/test_patchbank.py` checks every kind and MIDI number in `PARAMS` against the firmware table and the PDF.

An earlier decode, `LSB + 128·flag` with the flag ignored for mod depths, agreed only at 0 and 255. It made the editor seem to show some values at "about 2×" through a display curve. That curve doesn't exist. The full offset → name → kind map is `PARAMS` in `tools/patchbank.py`; decode a patch with `python3 tools/patchbank.py show <pack> <slot|name>`.

**Display curve.** The Components editor still shows filter cutoff on a curve: Acid stores 32 and the editor shows 104. Env release is probably shown on one too.

### Request a patch
`F0 00 20 29 01 61 40 <slot> 00 F7` — message type `0x40` requests a patch dump (seen on the Circuit path with its own id; expected to apply to Mono Station). Lets a bank be dumped from a connected unit by sweeping slots 0–63.

## Reference data

- `Roboo.monostationpack` (repo root) — a real 64-patch bank: slots 0–31 named (bass1–4, drum one-shots, pad1–4, key1–4, …), 32–63 = `Initial Patch`. Primary corroboration for the 136-byte layout above.
- Firmware 800 embeds two defaults in its image: `Classic Mono` (`0x08028864`) and `Initial Patch` (`0x08028F6B`), each a bare 126-byte patch (the 136-byte message without its 9-byte header and `F7`), name first. (The firmware image contains **only** these templates, not a 64-slot factory bank — the shipped factory patches live in a separate data-flash region outside the app update image.)

## Open items
- The 136-byte split-pair value decode is **resolved** (see *Split-pair decode* above), from the firmware's store routine and checked against packs and editor screenshots.
- Coarse tune spans −128…127, but how that maps to semitones is unknown.
- The `category`/`genre` enums are resolved (#28), although real packs don't set them consistently.
- Request message (`0x40`) to be confirmed on hardware.
- The in-session patch copy is covered by #13 (`docs/session-format.md`).
