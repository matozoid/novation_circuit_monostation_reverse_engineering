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
| 41–134 | parameters, in the split "LSB + `0x00`/`0x40`" encoding — offsets per the community PDF (osc1/osc2 from 41, mixer, filter, env, LFO, distortion, 93–133 mod matrix, 134 selected routing) |
| 135 | `F7` |

Components treats the patch as an opaque blob apart from the name, category and genre (see *Components' view*). It doesn't name the synth parameters. The PDF's offset table and the firmware tables are the source for those.

### Split-pair decode (resolved)

The parameter region uses **two** kinds of two-byte value, both written as an LSB followed one byte later by the PDF's "MSB" flag (`0x00` or `0x40`). They decode differently — the PDF notes this but does not separate them:

- **Genuine 0–255 value** (unsigned params that exceed 127: mixer levels, filter cutoff, LFO rate): `value = LSB + 128·(MSB == 0x40)`. E.g. the init/full mixer and cutoff store LSB `127`, flag `0x40` → `255`.
- **Redundant sign flag** (bipolar mod-matrix pitch/cutoff depths, centred on 64): `value = LSB − 64`; the flag byte is just `0x40` when `LSB ≥ 64` and `0x00` otherwise, carrying no new information. Decoders must **ignore** it, not add 128.

Single-byte params are plain: `u` 0–127, `enum`/`bool` raw, osc octave and the remaining mod depths bipolar as `raw − 64`.

This is validated byte-exact against real packs — e.g. YBC "Acid": mixer noise `255`, filter overdrive `47` / reso `81`, env attack/decay/sustain `0`/`24`/`28`, and mod depths `seq→dist +24`, `vel→amp −3`, `vel→cutoff +22`. The full offset→name→kind map is `PARAMS` in `tools/patchbank.py`; decode a patch with `python3 tools/patchbank.py show <pack> <slot|name>`.

**Caveat — editor display curves.** The Components GUI shows a few knobs through a perceptual/scaled mapping, so an on-screen number can differ from this raw, linear byte: filter cutoff and env release are shown on a curve, and the pitch/cutoff **mod** depths are shown at ≈2× the stored `raw − 64`. The raw decode is the ground truth; the editor's number is cosmetic.

### Request a patch
`F0 00 20 29 01 61 40 <slot> 00 F7` — message type `0x40` requests a patch dump (seen on the Circuit path with its own id; expected to apply to Mono Station). Lets a bank be dumped from a connected unit by sweeping slots 0–63.

## Reference data

- `Roboo.monostationpack` (repo root) — a real 64-patch bank: slots 0–31 named (bass1–4, drum one-shots, pad1–4, key1–4, …), 32–63 = `Initial Patch`. Primary corroboration for the 136-byte layout above.
- Firmware 800 embeds two defaults in its image: `Classic Mono` (`0x08028864`) and `Initial Patch` (`0x08028F6B`), each a bare 126-byte patch (the 136-byte message without its 9-byte header and `F7`), name first. (The firmware image contains **only** these templates, not a 64-slot factory bank — the shipped factory patches live in a separate data-flash region outside the app update image.)

## Open items
- The 136-byte split-pair value decode is **resolved** (see *Split-pair decode* above), validated against vendor soundpack banks.
- The exact centre and scale of osc coarse (about centre 128) and fine (centre 50) are still fuzzy.
- The `category`/`genre` enums are resolved (#28), although real packs don't set them consistently.
- Request message (`0x40`) to be confirmed on hardware.
- The in-session patch copy is covered by #13 (`docs/session-format.md`).
