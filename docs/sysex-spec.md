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
| 27 | envelope Trigger (`env_retrigger`): firmware param id 2, NRPN 0:73, 0–1, default 0. The MIDI PDF lists it as `Trigger`, 0–1, default 0, and the user guide doesn't mention it. **On hardware (2026-10-25): 1 = retrigger, 0 = legato** (a second key held over the first replays the envelope only at 1). It is the panel's Shift + PWM toggle (light on = 1), and NRPN 0:73 sets it too, both checked by reading the current patch back. A first test with sustain at maximum couldn't hear the difference; use a pluck (attack 0, decay ~70, sustain 0). |
| 28–40 | unused: there is no firmware param-table record for patch offsets 19–31. They are `00` in `Initial Patch`, `Classic Mono`, all 64 Roboo patches and all 64 YBC patches. |
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
- the 64 YBC "Patch Details" screenshots (see *Editor screenshots* below) agree with the decoded bank value for value, e.g. Adlib's ring mod 255, Kick 8's mixer 175/0/255/9, Noise Snare's cutoff 115, seq → filter −80 and fine −83, and Filter Snare's cutoff 234.

`tests/test_patchbank.py` checks every kind and MIDI number in `PARAMS` against the firmware table and the PDF.

An earlier decode, `LSB + 128·flag` with the flag ignored for mod depths, agreed only at 0 and 255. It made the editor seem to show some values at "about 2×" through a display curve. That curve doesn't exist. The full offset → name → kind map is `PARAMS` in `tools/patchbank.py`; decode a patch with `python3 tools/patchbank.py show <pack> <slot|name>`.

### Editor screenshots

The YBC "Big City Flippin' Grooves" pack ships a `.syx` bank plus 64 "Patch Details" PNGs from a third-party editor. All 64 were compared with the decoded bank:
- **62 agree** on every displayed value (raw decode, no display curves), apart from the two editor quirks below. `Chord Disto.PNG` repeats Reverse Filter and `Semi Chords.PNG` repeats Derek, so those two patches have no screenshot of their own.
- **Acid and Italo were edited after export.** A handful of single values differ (Acid: cutoff 32 vs 104, release 39 vs 23, dist 127 vs 37; Italo: cutoff, res, dist, LFO rate, LFO → filter). Every other field matches, so the bank isn't misdecoded. An earlier "cutoff display curve" claim came from Acid and is wrong.
- **Glide is always shown as 0**, even where the bank has non-zero glide (ARP Lead 4/1, Animals 51/59, PWM Lead 34/25, …). The editor doesn't read bytes 49/58. The firmware table puts osc 1/2 glide (CC 5/37, 0–127) there.
- **Ext and Ringmod sliders are swapped.** The slider labelled Ext shows byte 71 and the one labelled Ringmod byte 69 (Adlib: 255 under Ext; Italo: 50 under Ringmod). The firmware table and the MIDI PDF put ring mod (CC 24) at 71 and audio in (CC 25) at 69, and 13 YBC patches use byte 71 against 1 for byte 69, so the editor mislabels.
- Enum values seen match the MIDI PDF tables: osc wave 0–3 Sin/Tri/Saw/PW, range 0–3 16'/8'/4'/2', filter shape 0–2 LP/BP/HP, slope 0/1 12/24 dB, bypass 0 off / 1 Noise / 2 Osc / 3 both, dist type 0–2 I/II/III, LFO wave 0–3 Tri/Saw/Sq/S&H, sync rate (e.g. 8 "21 2/3 beats", 16 "8 beats", 19 "4 beats", 25 4th, 29 16th dotted, 31 16th, 34 32nd triplet). Selected routing = source × 8 + destination. Env → amp is a bool (the lit "Amp" button).
- **Re-Trigger is always shown unlit**: Animals (byte 27 = 1, the only YBC patch with it set) and 808 Cow (byte 27 = 0) look the same. Like glide, the editor doesn't read byte 27.

### Request a patch
`F0 00 20 29 01 61 40 00 00 F7` — message type `0x40` requests the **current working patch**. On hardware (2026-10-25) the reply is a 136-byte type-0 message (`F0 00 20 29 01 61 00 00 00` + patch + `F7`) holding the current edit buffer, not slot 0's stored data. Other slot bytes (1, 40, 63, 64, 127) get no reply, so this can't sweep a bank; the bootloader's `0x78` dump does that (`docs/session-transport.md`). A type-1 slot write does work, including to slot 64 (#12).

## Reference data

- `Roboo.monostationpack` (repo root) — a real 64-patch bank: slots 0–31 named (bass1–4, drum one-shots, pad1–4, key1–4, …), 32–63 = `Initial Patch`. Primary corroboration for the 136-byte layout above.
- Firmware 800 embeds two defaults in its image: `Classic Mono` (`0x08028868`) and `Initial Patch` (`0x08028F6B`), each a bare 126-byte patch (the 136-byte message without its 9-byte header and `F7`), name first. `Classic Mono` is the patch a blank session gets: `FUN_08018ca4` copies it to session offset `0x15F5C`. `Initial Patch` is the "empty slot" reference: at boot the firmware reads 128 stored patches from external flash (block `n/8 + 2`, offset `(n % 8) × 512`, i.e. from `0x2000`) and builds a bitmask of slots that differ from it, and `FUN_0801b4e0` updates that mask when a patch is saved. (The firmware image contains **only** these templates, not a 64-slot factory bank — the shipped factory patches live in a separate data-flash region outside the app update image.)

### Patch store: 128 slots, 64 reachable from the panel

The firmware's patch store has **128 slots** of 512 bytes at external-flash `0x2000`–`0x12000`. Components and the user guide use only 64 (`0x2000`–`0xA000`). Every firmware check on a slot number is `< 0x80`, and none is `< 0x40`:
- the boot scan `FUN_0800e1f8` reads all 128 and keeps a 128-bit "differs from `Initial Patch`" mask in four observable words (`0x10`–`0x13`);
- the patch save `FUN_0801b4e0` updates that mask. Its two callers reject slot ≥ `0x80`: the session-controller port handler at `0x08019A26` (save the current patch, or clear a slot with `Initial Patch`), and `0x08020210` (slot 2 of the session-controller vtable `0x08027068`, which writes the patch it is given to slot `r4`; probably the SysEx slot write, not yet confirmed);
- the patch-load handler `FUN_08019AE4` (parameter id `0x50`) loads slot `< 0x80`.

Only the panel stops at 64. The patch view (render `FUN_08013ac4`, pads `FUN_08014330`) shows 32 pads, and slot = page × 32 + pad. Oct ↓ / Oct ↑ (button events `0x14` / `0x15` in `FUN_08011FBC`) call `FUN_08022fdc(view, 0)` / `(view, 1)`, so the page is only ever 0 or 1. The render does mask the page with `& 7` and lights the Oct LEDs by `page == 0` vs `page != 0`.

So slots 64–127 are probably dead space left over from a 4-page design, and the firmware would load, save and track them as normal. A 7-bit SysEx slot byte can address them, and so could a patched page setter (#31). On hardware, a type-1 slot write to slot 64 works, but program change doesn't load it (see below).

**Program change already reaches them.** The MIDI PDF documents program change 0–63 on the Osc 1 channel, but the firmware doesn't clamp it:
- The MIDI-in router at `0x080186B8` (missed by Ghidra; registered from the UI constructor `FUN_0800FB5C` via literal `DAT_0800FB04`) dispatches on `status & 0xF0`.
- For `0xC0` it calls slot 3 of the MIDI target's vtable `0x08026FE4` with a base of `0` when the channel matches context byte `+0xB` (Osc 1 channel, default 0 = ch 1), or `0x20` when it matches byte `+0xC` (default 15 = ch 16).
- Slot 3, `0x0801C626`, sends:
  - base 0 → `set(0x50, program, 1)`, the patch-number setter `FUN_0801C842`, with no range check, so on to `FUN_08019AE4` (`< 0x80`);
  - base `0x20` → `FUN_08014D2E(obj + 0x2C, program)`, session select (PDF: 0–31 instant, 64–95 queued).

So program change 64–127 on the Osc 1 channel was expected to load slot 64–127 from `0xA000`–`0x12000`. **Hardware says no (2026-10-25, #31):** with `Min 3rd` written to flash slot 64, PC 64, 96, 104 and 127 all loaded the built-in `Initial Patch`, also after a power cycle. The path that substitutes the template isn't traced: the load handler `FUN_08019AE4` has a port (`+0x34`) that copies `Initial Patch` with no slot argument, and the setter posts through a port chain (`FUN_08014D2E`) not yet resolved.

## Open items
- The 136-byte split-pair value decode is **resolved** (see *Split-pair decode* above), from the firmware's store routine and checked against packs and editor screenshots.
- Coarse tune spans −128…127. The user guide gives the range as ±1 octave (p. 18 control list and the oscillator section), so about 10.7 steps per semitone. Whether the engine quantises to semitones is not traced.
- The `category`/`genre` enums are resolved (#28), although real packs don't set them consistently.
- Request message (`0x40`): confirmed on hardware, current patch only (`40 00`).
- Why program change ≥ 64 loads the `Initial Patch` template instead of the flash slot.
- The in-session patch copy is covered by #13 (`docs/session-format.md`).
