# Bass Station II patch SysEx (reference only)

**This is not a Circuit Mono Station format.** Every piece of it lives in Components' Bass Station II modules. It is kept here because the work is correct for that product, and because the codec idea may matter if a later Mono Station firmware adopts something similar. For the Mono Station's real patch format see `docs/sysex-spec.md`.

## Provenance

All of this comes from Components 1.72.5, `app.asar` → `ember-dist/assets/main-*.js`. Each JS name maps to an Ember module path:

| JS name | module | content |
|---------|--------|---------|
| `kD` | `frontend/constants/bass-station-ii/afx-overlay-midi-config` | the 85-entry parameter array (`tools/bassstation2_params.tsv`) |
| `PD`, `TD`, `ED` | `frontend/constants/bass-station-ii/content-transfer` | framing: `SYSEX_SIZE=154`, `PATCH_SIZE=144`, `LEGACY_SYSEX_SIZE=122`, `LEGACY_PATCH_SIZE=112`, `SYSEX_HEADER_SIZE=9` |
| `JX` | `frontend/utils/bass-station-ii/convert-legacy-patch` | the 122 → 154 converter |
| `ID` | `frontend/constants/bass-station-ii/firmware-versions` | `AFX_EDITOR_SUPPORTED` 696, `AFX_MODE_SUPPORTED` 673, `MINIMUM_FIRMWARE_VERSION` 605 |
| `FD` | `frontend/constants/bass-station-ii/init-patch` | init patch |

The device class is `productName="Bass Station II"` with product id **`00 33`** (`Kre`), so `checkDeviceId` wants bytes 4–5 = `00 33`. The `kD` parameter set fits that synth and not the Mono Station: sub oscillator, two LFOs, a mod envelope, aftertouch routings, and filter resonance on CC 82.

## Message framing

| Bytes | Field |
|-------|-------|
| 0–3 | `F0 00 20 29` |
| 4–5 | product id `00 33` |
| 6 | protocol version, value `0`. The old spec said `6`, but that is the byte's index in `PD` (`MESSAGE_PROTOCOL_VERSION:6`), not its value. The pack loader checks `n[6]===0`. |
| 7 | message type (`TD`): `0` replace current patch, `1` replace stored patch, `0x40` request current, `0x41` request stored |
| 8 | patch index |
| 9–152 | 144-byte payload, 7-bit packed |
| 153 | `F7` |

**Legacy 122-byte message.** `JX` copies the first 121 bytes into a zeroed 154-byte buffer, writes `NO NAME` at offset 137 (`length − 17`), and ends it with `F7`. So the legacy message is the same layout with a 112-byte payload and no name.

## Payload packing (7 ↔ 8 bit)

The 144-byte payload is one MSB-first bit stream of 7-bit bytes, regrouped into 8-bit bytes. 144 × 7 = 1008 bits = **126 data bytes**. Components does it per group of 8 packed bytes → 7 data bytes (`OX`). Because 8 × 7 = 56 is byte-aligned, the per-group and continuous forms give the same result. `tools/bassstation2_patch.py` implements both directions (`unpack_7to8` / `pack_8to7`).

The 126 unpacked bytes:

| Bytes | Contents |
|-------|----------|
| 0–6 | header/reserved |
| 7–99 | the 85 parameters |
| 100–125 | name region |

## Value types (`UX`)

| `dt` | Meaning |
|------|---------|
| `DT_U` | unsigned 0–127 |
| `DT_U8` | unsigned 0–255 |
| `DT_S` | signed, centre 64 → `v − 64` |
| `DT_S8` | signed, centre 128 → `v − 128` |
| `DT_B` | boolean = bit `rs` of `v` (several booleans share one byte) |
| `DT_M` | enum = `v & min` (`min` is used as the mask) |

## Parameters (bytes 7–99)

The full table is `tools/bassstation2_params.tsv`. Summary:

| Byte | Param(s) | Type | MIDI |
|------|----------|------|------|
| 7 | voiceOscSync | B | CC110 |
| 8–12 | osc1: Wave, PulseWidth, Octave, TuneCoarse, TuneFine | M/U/S/S8/S8 | NRPN0:72, CC74/70/27/26 |
| 13–17 | osc2: Wave, PulseWidth, Octave, TuneCoarse, TuneFine | M/U/S/S8/S8 | NRPN0:82, CC79/75/30/29 |
| 18 | oscSubWave (M) + oscSubMode (B, bit 2) | M/B | NRPN0:85 / 0:79 |
| 19–24 | oscSub: PulseWidth, Octave, TuneCoarse, TuneFine, WaveSubMode, OctaveSubMode | U/S/S8/S8/M/S | NRPN0:83/81/84/77, CC80/81 |
| 25–30 | mixer: Osc1, Osc2, OscSub, Noise, RingMod, External | U8 | CC20–25 |
| 31 | filterFrequency | U8 | CC16 |
| 32–33 | filterResonance, filterOverdrive | U | CC82, CC114 |
| 34 | filterShape (M) + filterCircuit (B bit 2) + filterSlope (B bit 3) | M/B/B | CC84/83/106 |
| 35–40 | envAmp: Velocity, Attack, Decay, Sustain, Release, Trigger | S/U/U/U/U/M | CC112/90/91/92/93, NRPN0:73 |
| 41–46 | envMod: Velocity, Attack, Decay, Sustain, Release, Trigger | S/U/U/U/U/M | CC113/102/103/104/105, NRPN0:105 |
| 47–52 | lfo1: Waveform, Delay, LfoSlew, Speed, SpeedSync, SpeedMode (bit 0) + KeySync (bit 1) | U/U/U/U8/U/B/B | CC88/86, NRPN0:86, CC18, NRPN0:87/88/89 |
| 53–58 | lfo2: same as lfo1 | U/U/U/U8/U/B/B | CC89/87, NRPN0:90, CC19, NRPN0:91/92/93 |
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
| 96–97 | envAmpTimedSustain, envModTimedSustain (B bit 0) | B | NRPN0:114/115 |
| 98–99 | envAmpRetriggerCount, envModRetriggerCount (0–16) | U | NRPN0:117/118 |

Bytes 59–67, 88–90 and 93 hold no named parameter.

## Overlays

`overlayIndex` (1–80 in the TSV) indexes the AFX overlay map, which marks the parameters a key overlay overrides (`hasOverlay`, `clearOverlay`, `initOverlaysFromCurrentPatch`). It plays no part in decoding a single patch.
