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
| `0x00058` | 16 × 140 | track 0 patterns |
| `0x00918` | 16 × 42 × 108 | track 0 automation (42 lanes per pattern) |
| `0x12498` | 8 × 140 | track 1 patterns |
| `0x128F8` | 8 × 12 × 108 | track 1 automation (12 lanes per pattern) |
| `0x15178` | 8 × 140 | track 2 patterns (no automation) |
| `0x155D8` | 7 | further globals. The firmware initialises them to `00 00 00 00 00 00 40`. In real sessions byte 2 varies from 0 to 0x3A, byte 3 is 0 or 1, and bytes 0–1 are rarely set. Meaning TBD. |
| `0x155DF` | 0x97D | zero |
| `0x15F5C` | 126 | the session's patch: bytes 9–134 of the 136-byte bank format (name, category, genre, params) |
| `0x15FDA` | 0x22 | zero in files. The firmware fills this region with `0xFF` in RAM. |
| `0x15FFC` | 4 | `FF FF FF FF`, past the struct |

The firmware's initialiser reads its defaults from these templates:
- magic and size: literal pool `0x08018E80`;
- name `Initial Session`: `0x08028D70`;
- globals `78 32 03 00 …` and pattern counts `10 08 08`: `0x08026D98`;
- default last step `0F`: `0x08029E9C`;
- `Classic Mono` patch body: `0x08028868`.

**Each session holds one patch, not 64.** The 64 × 512-byte patch store is a separate flash area. Components calls it `MS_ALL_PATCHES` (start `0x2000`, length `0x8000`, 512 bytes per slot, 126 bytes used).

## Pattern (140 bytes)

The 16 steps are 8 bytes each, followed by a 12-byte footer.

Step bytes, as the firmware initialises them, and what the data suggests:

| byte | init | observation |
|------|------|-------------|
| 0 | `00` | note number, with 0 meaning an empty step. Seen 0x1F–0x7A. |
| 1 | `60` (tracks 0, 1), `00` (track 2) | velocity on tracks 0/1, default 96. On track 2 this byte carries a 0–127 value centred on `0x40`, even on steps with no note. |
| 2 | `00` | gate length. 6 is common, and values up to `0x20` look like ties. Probably counts micro-steps of 1/6 step. |
| 3 | `FF` | always `FF` |
| 4 | `FF` | `FF`, or a second value 0x11–0x3A on track 0 only (possibly the paraphonic second note) |
| 5–7 | `00` | always 0 |

Footer: byte 0 = last step index (init `0F`; `07` seen for an 8-step pattern). Byte 1 is `03` by default, with `04` and `06` also seen (possibly a sync rate). Byte 3 was `01` once, on track 2. The rest is 0.

The Components `INIT` file stores step byte 4 as `00`, where the firmware and all real sessions use `FF`.

## Automation lane (108 bytes)

A lane has a 12-byte mask followed by 96 values, one per micro-step: 16 steps × 6 micro-steps. Mask bit *i* (LSB first, little-endian) marks micro-step *i* as recorded. Values outside the mask are always 0. The firmware clears each lane through `FUN_0802323c(lane, step, micro)`.

Which parameter each lane automates is not mapped yet. The most-used track-0 lanes in the YBC sessions are 9, 10, 16 and 17; on track 1 it is lane 3.

## Open

- Which instrument or function each of tracks 0, 1 and 2 is. Track 0 has 16 patterns with 42 automation lanes, track 1 has 8 patterns with 12 lanes, and track 2 has 8 patterns with no automation, holding a value per step.
- The step byte semantics above, apart from the defaults. Confirm them in the sequencer code.
- The mapping from lane index to parameter. Compare against the 154-byte patch parameter order and the CC table.
- Footer bytes 1 and 3, header byte `0x42`, and the 7 bytes at `0x155D8`.
