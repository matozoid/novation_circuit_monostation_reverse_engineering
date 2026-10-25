# Sequencer engine (firmware 800)

How the clock, transport, step position and note playback fit together. Function names are Ghidra defaults; addresses are in the 800 image. Pattern and step byte layouts are in `docs/session-format.md`.

```
TIM5 ch3 (1 ms) ──► main loop soft-timer table 0x20003230
                         │ timer 0, callback 0x08017F1C (clock engine)
                         ▼
     clock state: tempo/swing/sync source, 24 PPQN tick, step/micro-step
                         │ listener vtable [0] once per tick
                         ▼
     transport, vtable 0x08027208 [0] FUN_08016E78 (clock tick)
                         │ per track: FUN_0801BA36 (next step / pattern)
                         ▼
     observable cells (step, pattern, tick)  ──commit──►  observers
                         ▼
     track player, vtable 0x08029FA4 [0] FUN_080234AC
            ├─ note sink  (+0x38) → engine noteOn/noteOff (with track index)
            └─ param sink (+0x3c) → automation / step params / patch change
```

## Observable state cells

Almost all sequencer state lives in double-buffered *cells* with observers. Code reads a field, writes it through a write accessor, then *commits*; the commit notifies the observers.

| Function | Role |
|----------|------|
| `FUN_080231F8(cell)` | read field 0 (current buffer) |
| `FUN_08017A40(cell)` | read field 1 |
| `FUN_0802335C(cell)` | read another field (used for settings / previous values) |
| `FUN_08014ABE(cell, field)` | write accessor: marks the field dirty and returns the back-buffer pointer. `FUN_080192E8` = field 0, `FUN_0801C83C` = field 1, `FUN_08016A54`, `FUN_08014BCC` (indexed) are wrappers. |
| `FUN_08014BE4(cell)` | commit: flips the dirty fields' buffer bits (`+4 ^= +8`), calls `observer->vtable[0](observer, cell, dirty_mask)` for each observer at `+0x14` (pairs {object, mask}, count at `+1`) whose mask matches, then clears the dirty mask |
| `FUN_08014C2E(cell, observer, mask)` | subscribe |

So a player's `vtable[0]` is called with the cell that changed and dispatches on which of its cells it was. A patch hook that writes a cell must commit it or nothing reacts.

## Time base

- **TIM5** (`0x40000C00`, ISR `FUN_0801F544`) runs with PSC `0x20CF` (8399), i.e. **10 kHz** from the 84 MHz APB1 timer clock. Its 4 output-compare channels each add a period to their CCR and bump a counter (obj +0x10):

  | Ch | Object | Period | Rate |
  |----|--------|--------|------|
  | 1 | `0x20000778` | 1 | 10 kHz |
  | 2 | `0x200007BC` | 5 | 2 kHz — main loop runs soft-timer list `0x200001E8` (7 entries) when it changes |
  | 3 | `0x200007D4` | 10 | 1 kHz — main loop runs the 8-entry soft-timer table `0x20003230` |
  | 4 | `0x2000075C` | 10 | 1 kHz — the ISR itself runs list `0x2000072C` (2 entries: callbacks `0x0801BCEE`, `0x0801BCEC`) |

- Soft-timer entry (24 bytes): `{reload, remaining, mode (0 off / 1 periodic / 2 one-shot), callback, count, …}`, run by `FUN_08024070`. The `0x20003230` table in the main loop (`FUN_0800C2D0`) is different: callbacks and contexts sit in parallel arrays `0x200031DC[8]` / `0x200032F4[8]`, and the loop subtracts the elapsed ms in one go, so after a slow pass the callback gets `(index, missed_ticks, context)` and catches up.
- **Timer 0** of that table is the sequencer clock: `FUN_080179DC(clock)` sets reload 1 (1 ms), periodic, callback `0x08017F1C`, context = clock state. It is (re)armed at boot and on Start/Continue/Stop. Timer 1 is a 333 ms one-shot (`0x14D`) used by the UI (`FUN_08015390`, `FUN_0802284C`).

The clock engine therefore runs in the **main loop at 1 ms granularity**, not in an interrupt; tick jitter is up to 1 ms plus main-loop latency.

## Clock engine `FUN_08017F1C(index, missed, clock)`

Ghidra had not defined it (now made with `MakeFunc`). One call per ms (plus `missed` catch-up iterations).

Clock-state fields used:

| Offset | Meaning |
|--------|---------|
| `+0x08` | listener (the transport); `vtable[0]` is called once per tick |
| `+0x0C` | settings cell; byte 1 & 7 = sync-out rate |
| `+0x14` | tempo cell: field 0 = BPM, field 1 = swing (copied to `+0x4F`), another field = swing sync rate (`+0x50`) |
| `+0x20` | tick within the bar, 0–95 |
| `+0x28`/`+0x2C` | 64-bit phase accumulator |
| `+0x30` / `+0x31` | step 0–15 / micro-step 0–5 (6 ticks per 1/16 step) |
| `+0x34` | tick countdown accumulator |
| `+0x48` | clock-source cell: 0 internal, 1 MIDI clock, 2 analogue sync in |
| `+0x4C` | detected-source bits: bit 1 = MIDI clock seen, bit 2 = analogue pulses seen (interval < 1000 ms) |
| `+0x4E` | sync-mode setting 0–4 (the case switch; 4 = auto with hysteresis `0x25A` ms) |
| `+0x54`, `+0x5C` | incoming MIDI clock / analogue pulse counts since the last call |
| `+0x60` | ms timestamp |
| `+0x68`/`+0x6C` | timestamp rings (`FUN_08023306` pushes); `FUN_0802328C` fits a least-squares slope to estimate the external tempo |
| `+0x80`/`+0x84` | phase increment and its kind (0 BPM byte, 1 measured MIDI, 2 measured analogue ×12) |

**Internal tempo.** The phase increment per ms is `BPM × 0x1A36E3`; a tick needs `2^32` (at 50 % swing). `0x1A36E3 = 2^32 × 24 / 60000`, so BPM × 24 ticks per minute = **24 PPQN**.

**Swing.** Each tick's threshold is `swing_table[s]` (64-bit entries at `0x080283E0`, entry `s` = `s/50 × 2^32`), where `s` = swing for even swing periods and `100 − swing` for odd ones. The period is `tick / ticks_per_step[swing_rate]` (table `0x08029F6C`). Swing therefore stretches and shrinks alternate ticks; the total over two periods stays constant.

**External sync.** With MIDI clock (source 1) the transport is not ticked from here; presumably the MIDI clock handler ticks it directly. With analogue sync in (source 2) every pulse adds 12 ticks (the first one `6 − micro + 6×(step even)` to realign), i.e. **2 PPQN** in, and the tick rate follows the measured pulse period. `FUN_0801AECC` publishes the measured tempo (×2500 fixed point) for display.

**Sync out.** On each tick, if the sync-out setting (settings cell byte 1 & 7) is non-zero and bit `(micro + (step & 3) × 6)` of mask table `0x08029F48[setting − 1]` is set, TIM2 (`0x40000000`) fires a one-shot pulse (CNT = 0, ARR = 20, CEN). Masks: `0x1` (1 PPQN), `0x1001` (2), `0x41041` (4), `0x249249` (8), `0xFFFFFF` (24), `0x80810` (6th setting, uneven — unknown), `0` (end). The main loop polls TIM2 SR for the pulse end.

## Transport (vtable `0x08027208`)

The transport object holds the sequencer state (`+0x0C` = clock state, `+0x10` = listener for Start/Continue/Stop/SPP events). Secondary vtables at `0x0802722C` (`this − 4`) and `0x08027240` (`this − 8`) are thunks for its other base classes; `0x08027248` = `FUN_08016E6E`, the clock-state observer (source change / stop).

| Slot | Function | Meaning |
|------|----------|---------|
| 0 | `FUN_08016E78(this, src)` | **clock tick**: increments the clock cell `+0x44`, advances each of the 3 tracks via `FUN_0801BA36`, handles pattern-chain switching / count-in (`+0x51`/`+0x52`/`+0x54`) |
| 1 | `FUN_08016B52(this, id, on)` | button / flag ids at `+0x60`/`+0x64`/`+0x68` |
| 2 | `FUN_08016C5E(this, ext)` | **Start** (reset to 0) |
| 3 | `FUN_08016BD6` | **Continue** |
| 4 | `FUN_08016D1C` | **Stop**; recomputes the clock source and tempo |
| 5 | `FUN_08016AD2(this, spp, ext)` | **Song Position Pointer**: clock = spp × 6; step = spp, micro = 0 |
| 6 | `FUN_08016A8E` | forwards to the listener |

Sequencer fields:

| Offset | Meaning |
|--------|---------|
| `+0x18` | per-track pattern arrays (140-byte patterns) |
| `+0x20[3]` / `+0x2C[3]` / `+0x38[3]` | per-track state cells (current pattern, step, …) |
| `+0x44` | clock-tick cell |
| `+0x48[3]` | per-track ping-pong direction / random counter |
| `+0x4C` | chain cell, per track: byte 0 = first pattern, byte 1 = last pattern of the chain |
| `+0x50` | position valid flag |

`FUN_0801B698(seq, track)` computes the absolute position from the clock (used after Start/SPP/chain edits; `docs/session-format.md`). `FUN_0801B880(seq)` calls it for tracks 0–2; `FUN_0801B8B8(seq, clock)` sets the clock and recomputes.

### Step advance `FUN_0801BA36(seq, pattern_cell, step_cell, track, jump, dry, force_random)`

Called by the tick for each track when its step is due. With the pattern footer byte 0 (`range`: start = high nibble, end = low nibble) and byte 2 (direction):

- **forward:** step + 1 until `end`, then next pattern at `start`;
- **reverse:** step − 1 until `start`, then next pattern at `end`;
- **ping-pong:** direction flag in `+0x48[track]` flips at the ends; the pattern advances after the backward half;
- **random:** `+0x48[track]` counts steps; the pattern advances after `length − 1` steps; each step is `start + rng % length` (`FUN_08016A5A` = length, `FUN_0801275C` = "step in range", which handles wrapped ranges).
- Next pattern = chain first if the current is the chain's last, else current + 1 (or chain first directly when `jump` is set).

It writes the new pattern/step through the cells and returns 1 when the pattern changed.

**Random source:** `FUN_0801AB9C` reads the **hardware RNG** (`0x50060800`, waits for DRDY, rejects repeats). Usable as-is for probability / mutation features (#35, #39).

## Track player (vtable `0x08029FA4`)

Three players, 0x10C bytes each, built in `FUN_0800E1F8` by `FUN_08020EA0`. Player fields:

| Offset | Meaning |
|--------|---------|
| `+0x04` | pattern array of the track |
| `+0x0C`, `+0x10`, `+0x14`, `+0x18`, `+0x1C`, `+0x20`, `+0x2C`, `+0x30`, `+0x34` | observed / read cells: `+0x18` = tick, `+0x1C` = step, `+0x20`/`+0x34` = transport state, `+0x14` = sync rate |
| `+0x24`, `+0x28` | per-lane last values (`0xFFFF` = none) |
| `+0x38` | **note sink** |
| `+0x3C` | **param sink** (shared) |
| `+0x40` | step-parameter id: `0x09` (track 0), `0x10` (track 1), `0x4C` (track 2) |
| `+0x44`, `+0x48` | automation lane id table (`0x08027588` with 42 lanes, `0x0802775C` with 12, none for track 2) |
| `+0x49`… `+0x5E`… | pending note-offs: 8 slots in a delta-sorted list, each a 16-bit tick countdown (`+0x5E + 4i`) and note (`+0x60 + 4i`) |
| `+0x7D + note` | slot of the sounding note (`0xFF` = none) |

`FUN_080234AC(player, cell)` dispatches on the cell:

- **tick cell** (`+0x18`): decrements the head countdown and fires due note-offs (`FUN_080211F4` → `note_sink->vtable[1](note)`).
- **step cell** (`+0x1C`): plays the step: patch change (step byte 4, param id `0x50`), the step parameter (byte 3), the automation lanes (with the smoothing table at `0x08023930`) through `param_sink->vtable[0](id, value, smooth)`, then the note: `note_sink->vtable[0](note, velocity)` (velocity 0 → 96) and schedules its note-off after `gate × ticks_per_step / 6` ticks (gate clamped to 1–96).
- **transport cells** (`+0x34`, or `+0x20` going to 0): all notes off, lanes reset.

Notes are therefore only on the tick grid; the gate already has micro-step (tick) resolution. A per-step note offset (#32) could reuse the same delta list to schedule delayed note-ons.

### Note routing

Sequencer notes take this path (all objects built in `FUN_0800E1F8`):

1. **Player note sink** (vtable `0x08027980`, 16 bytes, `+0x08` = target, `+0x0C` = id): `[0] 0x080189CE noteOn(note, vel)` → `target->vtable[0](target, note, vel, id)`; `[1] 0x080189BA noteOff(note)` → `target->vtable[1](target, note, id)`. For the players the id is the track (0–2) and the target is a **source adapter** (vtable `0x080270EC`, `+0x04` = router, `+0x08` = base 3), which forwards ids 0–2 as router channel `base + id`.
2. **Router** (`0xA0C`-byte object, vtable `0x08027268`): `[0] FUN_0801C748 noteOn(note, vel, ch)`, `[1] FUN_0801C67E noteOff(note, ch)`. Channels 0–7 are inputs:

   | Channels | Source | Treatment |
   |----------|--------|-----------|
   | 0–2 | 0, probably the pads/keys | velocity forced to 96 when a settings bit (cell `+0x6C` bit 1) is set |
   | 3–5 | 1, **sequencer** tracks 0–2 | |
   | 6–7 | 2, probably MIDI in (tracks 0–1 only) | the transpose byte `*(router+0x0C)` is subtracted first |

   For inputs, `FUN_0801B8DA` / `FUN_0801B058(router, source, track, note, vel)` map the note through the scale (`FUN_08018ECA`, scale object at `+0x08`), remember it per note (so retriggers and note-offs match), and call the sink `router + 0x34 + (track × 3 + source) × 4`, which is input `source` of the track's merger.
3. **Per-track merger** (`FUN_08020FA0`, 0x28 bytes, vtable `0x08029F04`: `[0] FUN_08018940` noteOn, `[1] FUN_080188D8` noteOff, dispatching on the input id). It holds three merge stages (`FUN_08020D7C`, 0x30 bytes, two 5-note input buffers each, output back to the merger as ids 3/4/5) and three outputs `+0x1C`/`+0x20`/`+0x24`:

   ```
   source 0 (keys) ──┬─► stage 1 ─┬─────────────► out A  MIDI out
   source 1 (seq)  ──┘            └─► stage 2 ──► out B  synth engine
   source 2 (MIDI in) ─────────┬──────┘
                               └─► stage 3 ──► out C  recorder
   source 0 (keys) ────────────────────┘
   ```

   (stage 1 = keys + sequencer, stage 2 = stage 1 + MIDI in, stage 3 = keys + MIDI in.)
4. **Outputs** go back through router channels 8–16 (merger outputs are 0x08027980 adapters with ids `0x0B`/`0x08`/`0x0E` for track 0, `0x0C`/`0x09`/`0x0F` for track 1, `0x0D`/`0x0A`/`0x10` for track 2). The router adds the transpose byte, drops notes below 12, and calls:

   | Ids | Router field | Destination |
   |-----|--------------|-------------|
   | 8–10 (out B) | `+0x60` | adapter `0x08027180` (note − 12) → synth engine object (0x90 bytes, vtable `0x08026FB8`, holds the 126-byte patch, init from `Initial Patch`): `[0] FUN_080178F0` → **`FUN_080226BC(track, note, vel)`**. Also sets the note's bit in a held-notes bitmap cell (LEDs). |
   | 11–12 (out A; 13 dropped) | `+0x64` | adapter `0x08027180` (note − 12) → **MIDI out** (vtable `0x0802709C`): `[0] FUN_08018B48` sends `0x90 | channel[track]` (channel table at `+0x0C`, defaults 0 and 1 = MIDI channels 1 and 2), only when `+0x18` bit 0 (note TX enabled) is set, via `FUN_0801AEE8`. |
   | 14–16 (out C) | `+0x68` | **recorder** (vtable `0x08027154`): `[0] FUN_0802345C` forwards to the per-track recorder at `+0x0C + track × 4` only while the transport cell says running and recording |

So the mod track (2) never reaches MIDI out. Sequencer playback is not recorded (stage 3 has no sequencer input), so recording live input over a playing pattern doesn't feed back on itself.

**#38 (track 1 to MIDI only):** the split point is merger stage 1 → stage 2 for track 1. Skipping that forward (merger input 3 on the track-1 merger) sends keys and sequencer notes of track 1 only to MIDI out, while MIDI in still plays Osc 2. A hook in `FUN_080178F0` on `track == 1` would also mute MIDI-in notes on Osc 2. Which behaviour is wanted is a design choice.

## Open

- Inside the synth engine `FUN_080226BC` (Osc 1/Osc 2 voice assignment), and the merge stages' note priority.
- Confirm sources 0 and 2 (keys, MIDI in) from their callers.
- MIDI clock in → transport tick path (USB / DIN parsers), MIDI clock out.
- Real-time record (#33): the per-track recorders behind `FUN_0802345C` (`0x288`-byte objects from `FUN_08021128`). Step entry (#34): which UI handlers write step cells.
- Meaning of sync mode `+0x4E` values 0–4 against the user guide's clock settings, and the 6th sync-out mask.
