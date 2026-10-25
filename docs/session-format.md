# Session image format

Layout of one 90,112-byte `.monostationsession` image (#13). The SysEx framing that carries these images is described in `docs/session-transport.md`. Parser: `tools/session.py` (`python3 tools/session.py info <image>`).

Sources:
- the firmware's session initialiser `FUN_08018CA4`, which builds the struct in RAM;
- Components 1.72.5, whose session model maps only the header;
- `session_32.monostationsession` (the `INIT` image);
- the 48 distinct `USER` sessions in the two YBC soundpack dumps.

All 370 automation lanes that hold data in the YBC sessions fit the lane format below, and every session's patch matches the 136-byte patch format.

## Map

| offset | size | content |
|--------|------|---------|
| `0x00000` | 4 | magic, u32 LE: `INIT` (`0x54494E49`), `USER` or `DEMO` (Components `lQ`) |
| `0x00004` | 4 | struct size, u32 LE: always `0x15FFC` |
| `0x00008` | 4 | flags, u32 LE. Components reads bits 0–2 as the minimum firmware ("V1.2" each). The firmware writes `7`; the Components `INIT` file has `0`. |
| `0x0000C` | 1 | pad colour index (Components `binColor`). The YBC sessions use 16, 5 and 26 as group colours. |
| `0x00020` | 32 | name, space-padded |
| `0x00040` | 1 | tempo, BPM (init 120) |
| `0x00041` | 1 | swing (init 50) |
| `0x00042` | 1 | `3` everywhere seen (meaning TBD) |
| `0x00043` | 9 | 0 everywhere seen |
| `0x0004C` | 3 × 4 | per-track pattern chain: byte 0 = first pattern, byte 1 = last pattern, then 2 bytes that are always 0. One group each for tracks 0, 1 and 2, at `0x4C`, `0x50` and `0x54`. |
| `0x00058` | 16 × 140 | track 0 (synth / osc 1) patterns |
| `0x00918` | 16 × 42 × 108 | track 0 automation (42 lanes per pattern) |
| `0x12498` | 8 × 140 | track 1 (osc 2) patterns |
| `0x128F8` | 8 × 12 × 108 | track 1 automation (12 lanes per pattern) |
| `0x15178` | 8 × 140 | track 2 (mod sequence) patterns, no automation |
| `0x155D8` | 7 | further globals. The firmware initialises them to `00 00 00 00 00 00 40`. In real sessions byte 2 varies from 0 to 0x3A, byte 3 is 0 or 1, and bytes 0–1 are rarely set. Meaning TBD. |
| `0x155DF` | 0x97D | zero |
| `0x15F5C` | 126 | the session's patch: bytes 9–134 of the 136-byte bank format (name, category, genre, params) |
| `0x15FDA` | 0x22 | zero in files. The firmware fills this region with `0xFF` in RAM. |
| `0x15FFC` | 4 | `FF FF FF FF`, past the struct |

The firmware's initialiser reads its defaults from these templates:
- magic and size: literal pool `0x08018E80`;
- name `Initial Session`: `0x08028D70`;
- globals `78 32 03 00 …` and pattern counts `10 08 08`: `0x08026D98`;
- default step range `0F` (steps 0–15): `0x08029E9C`;
- `Classic Mono` patch body: `0x08028868`.

**Each session holds one patch, not 64.** The 64 × 512-byte patch store is a separate flash area. Components calls it `MS_ALL_PATCHES` (start `0x2000`, length `0x8000`, 512 bytes per slot, 126 bytes used).

## Pattern (140 bytes)

The 16 steps are 8 bytes each, followed by a 12-byte footer.

The tracks, from the parameters their automation lanes drive (below): **track 0** is the synth with oscillator 1, **track 1** is oscillator 2, and **track 2** is the mod sequence. The playback routine `FUN_080234AC`, which Ghidra had not defined as a function, reads the steps like this:

| byte | init | meaning |
|------|------|---------|
| 0 | `00` | note number; 0 = empty step. Sent with byte 1 as a note-on. |
| 1 | `60` (tracks 0, 1), `00` (track 2) | velocity; 0 plays as 96. On track 2 this is the mod-sequence value (centred on `0x40`). |
| 2 | `00` | gate in micro-steps (1/6 step), clamped to 1–96; 6 = one step, above 6 ties into later steps. Played as `gate × ticks_per_step / 6`. |
| 3 | `FF` | value for the track's step parameter: id `0x09` (patch byte 49, CC 5) on track 0, id `0x10` (patch byte 58, CC 37) on track 1, id `0x4C` on track 2. `FF` = none. All YBC sessions leave it `FF`. |
| 4 | `FF` | **patch change**: patch slot 0–63 to load when the step plays, sent as id `0x50`. `FF` = none. In the YBC sessions it is set only on track 0 note steps, values 0–60. |
| 5–7 | `00` | always 0 |

Byte offsets here are in the 136-byte patch message (see `tools/patchbank.py`). Bytes 49 and 58 sit at the end of the osc 1 and osc 2 groups and are not in the community PDF. CC 5 and CC 37 suggest per-oscillator glide.

Footer:
- Byte 0 is the played step range: start step in the high nibble, end step in the low nibble (init `0F` = steps 0–15). The length is `end + 1 − start`, plus 16 when end < start, so the range can wrap past step 15 (`FUN_08016A5A`). The YBC sessions only use `0F` and `07`.
- Byte 1 is the sync rate: an index 0–7 into the ticks-per-step table `[2, 3, 4, 6, 8, 12, 16, 24]` (24 PPQN) at `0x08029F6C`, i.e. 1/32T, 1/32, 1/16T, 1/16, 1/8T, 1/8, 1/4T, 1/4. Init `03` = 1/16. The YBC sessions also use `04` and `06`.
- Byte 2 is the play direction: 0 forward, 1 reverse, 2 ping-pong, 3 random. Always 0 in the YBC sessions.

The song-position routine `FUN_0801B698` reads bytes 0–2. It sums each chained pattern's length as `ticks[byte 1] × steps(byte 0)`, doubled for ping-pong. It then takes the clock modulo that total and walks the chain to the current step: up from start for forward, down from end for reverse, both ways for ping-pong, and through a random generator for random. Playback `FUN_080234AC` uses the same table for gate timing.
- Byte 3 was `01` once, on track 2. The pattern byte at `+0x83` switches `FUN_08023998` to an interpolated value path.
- The rest is 0.

Step byte 4 in detail: the setter `FUN_0801C842` treats id `0x50` apart from the patch parameters. It compares the value with the current patch number (an observable at controller `+0x64`) and, if it differs, posts it to a port of the session controller (vtable `0x08027068`; handler `FUN_08019AE4`, missed by Ghidra). For a value below `0x80` the handler reads 126 bytes from the patch store at `0x2000 + value × 0x200` (`FUN_0801AEF4(sector = (value >> 3) + 2, offset = (value & 7) << 9, …)`, the `MS_ALL_PATCHES` area) into the session's patch at `0x15F5C`, loads it into the synth engine, and stores the new patch number.

The Components `INIT` file stores step byte 4 as `00`, where the firmware and all real sessions use `FF`.

## Automation lane (108 bytes)

A lane has a 12-byte mask followed by 96 values, one per micro-step: 16 steps × 6 micro-steps. Mask bit *i* (LSB first, little-endian) marks micro-step *i* as recorded. Values outside the mask are always 0. The firmware clears each lane through `FUN_0802323c(lane, step, micro)` and records through `FUN_08017248`, which interpolates between micro-steps.

### Lane → parameter

The sequencer track constructor `FUN_08020EA0` gets one table of firmware parameter ids per track. Track 0 uses 42 u16 ids at `0x08027588`, track 1 uses 12 at `0x0802775C`, and track 2 has no table. During playback, lane *n* sends its value to `set(table[n], value)`. `tools/session.py` holds these tables as `LANE_IDS`, and `lane_param(track, lane)` names them.

The ids index the firmware parameter table at `0x0802A62A`, which has 76 records of 8 bytes:

| byte | content |
|------|---------|
| 0 | patch body offset (+9 = 136-byte message offset) |
| 1 | 0 |
| 2 | kind: 0 enum, 1 unsigned, 2 signed, 5 bool |
| 3 | centre |
| 4 | min |
| 5 | max |
| 6 | MIDI: `FF` = plain CC, `FC` = CC plus a +32 LSB CC, other values = NRPN MSB |
| 7 | CC number or NRPN LSB |

The MIDI-out encoder `FUN_08018B90` reads bytes 6–7. Ids 0 and 1 are category (max 14) and genre (max 9), which confirms the offset column. In order, the ids cover the 136-byte fields at offsets 25–134, and `tools/session.py` keeps them as `PARAM_OFFSETS`.

Track 0 lanes 0–41: osc1 coarse, fine, pw; mix osc1, sub, noise, audio in, ring mod; filter overdrive, reso, cutoff; dist amount; lfo rate, lfo sync rate; env attack, decay, sustain, release; then every mod-matrix routing (env, lfo, seq and vel sources) that does not target osc 2.

Track 1 lanes 0–11: osc2 coarse, fine, pw; mix osc2; then the osc 2 pitch and pw routings for env, lfo, seq and vel.

This fits the YBC data. The most-automated parameters there are filter cutoff (79 lanes), reso (78), env release (40) and sustain (38), mix sub, mix osc1 and osc 2 level.

The firmware CC numbers differ from the Components `kD` schema in places. For example, filter reso is CC 71 here but CC 82 in `kD`, and osc1 pw is CC 39 here but CC 74 in `kD`. Check this against hardware MIDI output before relying on either.

## Open

- Patch bytes 27, 49, 58 and 60 (ids 2, 9, 16, 18: NRPN 0:73, CC 5, CC 37, CC 7): not named by the PDF.
- Footer byte 3, header byte `0x42`, and the 7 bytes at `0x155D8`.
- The CC differences between firmware and Components `kD`.
