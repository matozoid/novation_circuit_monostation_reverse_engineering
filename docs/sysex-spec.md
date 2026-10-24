# Circuit Mono Station patch SysEx — authoritative spec

This supersedes the community PDF (`CircuitMonoStationPatchSysExFormat.pdf`, rev. 2020-06-30). It is derived from Novation **Components 1.72.5** (`app.asar` → `main-*.js`): the parameter array `kD`, the parse/build methods `fromPatchSysex` / `overlayFromPatchSysex`, the 7↔8-bit codec `RX`/`OX`/`LX`, the value transform `UX`, and the framing constants `PD` (`SYSEX_SIZE=154`, `HEADER_SIZE=9`, `LEGACY_SYSEX_SIZE=122`). Parameter names, offsets, types, ranges and CC/NRPN numbers are vendor data, taken verbatim. Byte-exact validation against a real hardware dump is the one remaining step (no standalone patch `.syx` was available); see *Open items*.

> **Why the PDF's "pseudo-MSB 0x40" bytes don't appear here:** the PDF read the raw 7-bit-packed payload without unpacking it. The real payload is a MIDI 7→8-bit bit stream (below). After unpacking, each parameter is a plain 8-bit byte; the `0x00`/`0x40` pattern the PDF saw was an artifact of the packing.

## Message framing (modern, protocol version 6)

The current-patch message is **154 bytes**:

| Bytes | Field |
|-------|-------|
| 0–3 | `F0 00 20 29` — SysEx start + Novation manufacturer id |
| 4–5 | device/model id |
| 6 | protocol version = `6` |
| 7 | message type |
| 8 | patch slot / index |
| 9–152 | 144-byte payload, 7-bit MIDI-packed |
| 153 | `F7` |

A **legacy 122-byte** message also exists (`LEGACY_SYSEX_SIZE=122`, `LEGACY_PATCH_SIZE=112`); Components up-converts it to 154 before parsing. Related short messages seen in the app: version inquiry `F0 00 20 29 00 70 F7` (reply carries the firmware version), and reboot-to-bootloader `F0 00 20 29 00 71 00 7F 00 00 F7`. A full bank/pack file is 64 × 136 bytes = **8704 bytes**.

*Still to confirm against hardware: bytes 4–5, and the set of message-type values at byte 7 (patch-dump, write-to-slot, request-dump).*

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
- Byte-exact confirmation of a real 154-byte dump (decode a known patch, compare).
- Header bytes 4–5 and the message-type enum at byte 7 (dump / write-slot / request).
- The exact name/category layout in unpacked bytes 100–125.
- The 122-byte legacy → 154 up-conversion (`JX`), if we ever need to read old files.
- The in-session 340-byte patch representation (tracked under #13, session format).
