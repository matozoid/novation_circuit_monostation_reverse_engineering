# Circuit Mono Station patch SysEx — authoritative spec

This supersedes the community PDF (`CircuitMonoStationPatchSysExFormat.pdf`, rev. 2020-06-30). It is derived from Novation **Components 1.72.5** (`app.asar` → `main-*.js`): the parameter array `kD`, the parse/build methods `fromPatchSysex` / `overlayFromPatchSysex`, the 7↔8-bit codec `RX`/`OX`/`LX`, the value transform `UX`, and the framing constants `PD` (`SYSEX_SIZE=154`, `HEADER_SIZE=9`, `LEGACY_SYSEX_SIZE=122`). Parameter names, offsets, types, ranges and CC/NRPN numbers are vendor data, taken verbatim. It is corroborated by a real 64-patch bank (`.monostationpack`) and the two default patches embedded in firmware 800 (see *The 136-byte bank/pack format* and *Reference data*).

**There are (at least) three distinct patch encodings.** They must not be conflated:

1. **136-byte bank/pack** (`F0 00 20 29 01 61 …`) — the real-world storage format: firmware patch memory, `.monostationpack` files, and what the community PDF documents. Parameter values use the split "LSB + `0x00`/`0x40` MSB-flag" encoding. **Here the `0x40` bytes are real** — the PDF is correct.
2. **154-byte modern wire** (protocol version 6) — Components' current-patch transfer. Its 144-byte payload is a 7→8-bit MIDI bit stream unpacking to 126 bytes, after which each parameter is a plain 8-bit byte (`kD` + `RX`/`UX`). The `0x40` pattern does **not** appear here; in this format it would be an artifact of not unpacking.
3. **122-byte legacy wire** — up-converted to 154 by Components before parsing.

The Mono Station **device id (bytes 4–5) is `01 61`** (`yte` validator: `t[4]===1 && t[5]===97`; confirmed by the bank and firmware). The original Novation **Circuit** uses `01 60` — do not confuse the two.

## Message framing (modern, protocol version 6)

The current-patch message is **154 bytes**:

| Bytes | Field |
|-------|-------|
| 0–3 | `F0 00 20 29` — SysEx start + Novation manufacturer id |
| 4–5 | device/model id = `01 61` |
| 6 | protocol version = `6` |
| 7 | message type |
| 8 | patch slot / index |
| 9–152 | 144-byte payload, 7-bit MIDI-packed |
| 153 | `F7` |

A **legacy 122-byte** message also exists (`LEGACY_SYSEX_SIZE=122`, `LEGACY_PATCH_SIZE=112`); Components up-converts it to 154 before parsing. Related short messages seen in the app: version inquiry `F0 00 20 29 00 70 F7` (reply carries the firmware version), and reboot-to-bootloader `F0 00 20 29 00 71 00 7F 00 00 F7`.

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
| 25 | category (observed: `1` = Bass, `3` = Drum/Percussion; full enum TBD) |
| 26 | genre (`0` observed) |
| 27–40 | reserved / unmapped (`00` in the init patch; `27` = env retrigger/legato per the PDF) |
| 41–134 | parameters, in the split "LSB + `0x00`/`0x40`" encoding — offsets per the community PDF (osc1/osc2 from 41, mixer, filter, env, LFO, distortion, 93–133 mod matrix, 134 selected routing) |
| 135 | `F7` |

Components treats the 136-byte patch as an opaque blob mapping only `name`@9, `category`@25, `genre`@26 (its `kte`/`pR` model, mime `application/x-novation-circuit-monostation-patch+json`); it does **not** name the individual synth parameters in this format — the PDF offset table is the source for those.

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
- Firmware 800 embeds two defaults in its image: `Classic Mono` (`0x08028864`) and `Initial Patch` (`0x08028F6B`), each a 16-byte name followed by the same 136-byte body layout. (The firmware image contains **only** these templates, not a 64-slot factory bank — the shipped factory patches live in a separate data-flash region outside the app update image.)

## Payload packing (7 ↔ 8 bit)

The 144-byte payload is a single **MSB-first bit stream of 7-bit bytes**, regrouped into 8-bit bytes: 144 × 7 = 1008 bits = **126 data bytes**. Equivalently, each group of 8 packed bytes yields 7 data bytes (Components' `OX`; because 8×7 = 56 is byte-aligned, the per-group and continuous forms are identical). `tools/patch.py` implements both directions (`unpack_7to8` / `pack_8to7`); `tests/test_patch.py` checks `unpack_7to8` against the exact `OX` formula and round-trips the inverse.

The 126 unpacked data bytes:

| Bytes | Contents |
|-------|----------|
| 0–6 | header/reserved (no named parameter) |
| 7–99 | the 85 patch parameters below |
| 100–125 | patch name / category (26 bytes; exact split pending a dump) |

## Value types (`UX`)

Each parameter has a type `dt` that interprets its unpacked byte `v`:

| `dt` | Meaning |
|------|---------|
| `DT_U` | unsigned 0–127 (raw) |
| `DT_U8` | unsigned 0–255 (raw; full 8-bit) |
| `DT_S` | signed, centre 64 → value = `v − 64` |
| `DT_S8` | signed, centre 128 → value = `v − 128` |
| `DT_B` | boolean = bit `rs` of `v` (several booleans share one byte) |
| `DT_M` | menu/enum = `v & min` (the `min` field is used as the bit mask) |

## Parameters (byte 7–99)

Full machine-readable table: `tools/monostation_patch_params.tsv` (name, patchIndex, overlayIndex, dt, signed, min, max, rs, type, cc, nrpnMsb, nrpnLsb). Summary:

| Byte | Param(s) | Type | MIDI |
|------|----------|------|------|
| 7 | voiceOscSync | B | CC110 |
| 8–12 | osc1: Wave, PulseWidth, Octave, TuneCoarse, TuneFine | M/U/S/S8/S8 | NRPN0:72, CC74/70/27/26 |
| 13–17 | osc2: Wave, PulseWidth, Octave, TuneCoarse, TuneFine | M/U/S/S8/S8 | NRPN0:82, CC79/75/30/29 |
| 18 | oscSubWave (M) + oscSubMode (B, bit2) | M/B | NRPN0:85 / 0:79 |
| 19–24 | oscSub: PulseWidth, Octave, TuneCoarse, TuneFine, WaveSubMode, OctaveSubMode | U/S/S8/S8/M/S | NRPN0:83/81/84/77, CC80/81 |
| 25–30 | mixer: Osc1, Osc2, OscSub, Noise, RingMod, External | U8 | CC20–25 |
| 31 | filterFrequency | U8 | CC16 |
| 32–33 | filterResonance, filterOverdrive | U | CC82, CC114 |
| 34 | filterShape (M) + filterCircuit (B bit2) + filterSlope (B bit3) | M/B/B | CC84/83/106 |
| 35–40 | envAmp: Velocity, Attack, Decay, Sustain, Release, Trigger | S/U/U/U/U/M | CC112/90/91/92/93, NRPN0:73 |
| 41–46 | envMod: Velocity, Attack, Decay, Sustain, Release, Trigger | S/U/U/U/U/M | CC113/102/103/104/105, NRPN0:105 |
| 47–52 | lfo1: Waveform, Delay, LfoSlew, Speed, SpeedSync, SpeedMode(bit0)+KeySync(bit1) | U/U/U/U8/U/B/B | CC88/86, NRPN0:86, CC18, NRPN0:87/88/89 |
| 53–58 | lfo2: Waveform, Delay, LfoSlew, Speed, SpeedSync, SpeedMode(bit0)+KeySync(bit1) | U/U/U/U8/U/B/B | CC89/87, NRPN0:90, CC19, NRPN0:91/92/93 |
| 68–70 | modAftertouch → FilterFreq, Lfo1OscFreq, Lfo2Speed | S | NRPN0:74/75/76 |
| 71–73 | modLfo1 → Osc1Pitch, Osc2Pitch, OscSubPitch | S8 | CC28/31, NRPN0:95 |
| 74–76 | modLfo2 → Osc1PW, Osc2PW, OscSubPW | S | CC73/78, NRPN0:102 |
| 77 | modLfo2FilterFreq | S8 | CC17 |
| 78–80 | modEnv2 → Osc1Pitch, Osc2Pitch, OscSubPitch | S | CC71/76, NRPN0:103 |
| 81–84 | modEnv2 → Osc1PW, Osc2PW, OscSubPW, FilterFreq | S | CC72/77, NRPN0:104, CC85 |
| 85 | modOsc2FilterFreq | U | CC115 |
| 86–87 | distortAmount, compressAmount | U | CC94, CC95 |
| 91–92 | envAmpRetrigger, envModRetrigger | U | NRPN0:109/110 |
| 94 | pitch (0–7) | U | NRPN0:111 |
| 95 | outputLevel | U | — |
| 96–97 | envAmpTimedSustain, envModTimedSustain (B bit0) | B | NRPN0:114/115 |
| 98–99 | envAmpRetriggerCount, envModRetriggerCount (0–16) | U | NRPN0:117/118 |

Unused payload bytes in 7–99: 59–67, 88–90, 93 (reserved / sequencer handled elsewhere).

## Overlays

`overlayIndex` (1–80 in the TSV) is **not** a value MSB — it indexes a separate per-parameter *overlay* map used by packs to mark which parameters a pack overrides (`hasOverlay`, `clearOverlay`, `initOverlaysFromCurrentPatch` in Components). Irrelevant to decoding a single patch.

## Open items (need a hardware dump to close #12)
- The 136-byte split-pair value decode is **resolved** (see *Split-pair decode* above), validated against vendor soundpack banks. Remaining per-param semantics still fuzzy: the exact centre/scale of osc coarse (≈ centre 128) and fine (centre 50), and the `category` enum (byte 25 values in real packs don't track patch names — see `issues.md` #12).
- Byte-exact confirmation of a real 154-byte dump (decode a known patch, compare).
- Header bytes 4–5 and the message-type enum at byte 7 (dump / write-slot / request).
- The exact name/category layout in unpacked bytes 100–125.
- The 122-byte legacy → 154 up-conversion (`JX`), if we ever need to read old files.
- The in-session 340-byte patch representation (tracked under #13, session format).
