# Sequencer engine (firmware 800)

How the clock, transport, step position and note playback fit together. Function names are Ghidra defaults; addresses are in the 800 image. Pattern and step byte layouts are in `docs/session-format.md`.

```
TIM5 ch3 (1 ms) ──► main loop soft-timer table 0x20003230
                         │ timer 0, callback 0x08017F1C (clock engine)
                         ▼
     clock state: tempo/swing/sync source, 24 PPQN tick, step/micro-step
                         │ commits tick cell +0x18 once per tick
                         │ (and sends MIDI clock out through +0x08)
                         ▼
     transport, vtable 0x08027208 [0] FUN_08016E78 (tick-cell observer)
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
| `+0x08` | MIDI clock out: the MIDI-out object's clock interface (vtable `0x080270E0`, `[0] FUN_08018A5C` sends `F8`); called once per tick, except with MIDI clock as source, where the `F8` handler forwards each incoming clock instead |
| `+0x0C` | MIDI settings cell (*MIDI settings* below); byte 1 & 7 = sync-out rate |
| `+0x18` | tick cell: committed once per tick; the transport and the track players observe it |
| `+0x14` | tempo cell: field 0 = BPM, field 1 = swing (copied to `+0x4F`), another field = swing sync rate (`+0x50`) |
| `+0x20` | tick within the bar, 0–95 |
| `+0x28`/`+0x2C` | 64-bit phase accumulator |
| `+0x30` / `+0x31` | step 0–15 / micro-step 0–5 (6 ticks per 1/16 step) |
| `+0x34` | tick countdown accumulator |
| `+0x48` | clock-source cell: 0 internal, 1 MIDI clock, 2 analogue sync in |
| `+0x4C` | detected-source bits: bit 1 = MIDI clock seen, bit 2 = analogue pulses seen (interval < 1000 ms) |
| `+0x4E` | run state: 0 stopped, 1 start pending (internal), 2 start pending (MIDI, waits for the first `F8`), 3 start pending (analogue), 4 running. `FUN_08021790` (first tick after a start) sets 4. The engine trusts the external tempo estimate only once state 4 has lasted `0x25A` ms (MIDI) or 1000 ms (analogue). |
| `+0x54`, `+0x5C` | incoming MIDI clock / analogue pulse counts since the last call |
| `+0x58` | external ticks still to play |
| `+0x60` | ms timestamp |
| `+0x68`/`+0x6C` | timestamp rings (`FUN_08023306` pushes); `FUN_0802328C` fits a least-squares slope to estimate the external tempo |
| `+0x80`/`+0x84` | phase increment and its kind (0 BPM byte, 1 measured MIDI, 2 measured analogue ×12) |

**Internal tempo.** The phase increment per ms is `BPM × 0x1A36E3`; a tick needs `2^32` (at 50 % swing). `0x1A36E3 = 2^32 × 24 / 60000`, so BPM × 24 ticks per minute = **24 PPQN**.

**Swing.** Each tick's threshold is `swing_table[s]` (64-bit entries at `0x080283E0`, entry `s` = `s/50 × 2^32`), where `s` = swing for even swing periods and `100 − swing` for odd ones. The period is `tick / ticks_per_step[swing_rate]` (table `0x08029F6C`). Swing therefore stretches and shrinks alternate ticks; the total over two periods stays constant.

**External sync.** With MIDI clock (source 1) each `F8` counted in `+0x54` adds one tick to `+0x58`, and the engine plays them out as ticks (*MIDI clock and transport* below). With analogue sync in (source 2) every pulse adds 12 ticks (the first one `6 − micro + 6×(step even)` to realign), i.e. **2 PPQN** in, and the tick rate follows the measured pulse period. `FUN_0801AECC` publishes the measured tempo (×2500 fixed point) for display.

**Sync out.** On each tick, if the sync-out setting (settings cell byte 1 & 7) is non-zero and bit `(micro + (step & 3) × 6)` of mask table `0x08029F48[setting − 1]` is set, TIM2 (`0x40000000`) fires a one-shot pulse (CNT = 0, ARR = 20, CEN). Masks: `0x1` (1 PPQN), `0x1001` (2), `0x41041` (4), `0x249249` (8), `0xFFFFFF` (24), `0x80810` (6th setting, uneven — unknown), `0` (end). The main loop polls TIM2 SR for the pulse end.

## Transport (vtable `0x08027208`)

The transport object holds the sequencer state (`+0x0C` = clock state, `+0x10` = MIDI transport out, `+0x14` = MIDI settings cell). Secondary vtables at `0x0802722C` (`this − 4`) and `0x08027240` (`this − 8`) are thunks for its other base classes. The `this + 8` interface (`0x08027240`) is the transport-event input: `[0]` Start, `[1]` Continue, `[2]` Stop, `[3]` SPP, `[4]` Song Select, each with a source argument (*MIDI clock and transport* below).

| Slot | Function | Meaning |
|------|----------|---------|
| 0 | `FUN_08016E78(this, cell)` | cell observer. On the clock state's tick cell (`+0x18`) it is the **clock tick**: increments the clock cell `+0x44`, advances each of the 3 tracks via `FUN_0801BA36`, handles pattern-chain switching / count-in (`+0x51`/`+0x52`/`+0x54`) |
| 1 | `FUN_08016B52(this, id, on)` | button / flag ids at `+0x60`/`+0x64`/`+0x68` |
| 2 | `FUN_08016C5E(this, src)` | **Start** (reset to 0) |
| 3 | `FUN_08016BD6` | **Continue** |
| 4 | `FUN_08016D1C` | **Stop**; recomputes the clock source and tempo |
| 5 | `FUN_08016AD2(this, spp, ext)` | **Song Position Pointer**: clock = spp × 6; step = spp, micro = 0 |
| 6 | `FUN_08016A8E(this, song, src)` | **Song Select**: forwards to MIDI out (`+0x10` `[4]`) |

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

## MIDI clock and transport

All objects are built in `FUN_0800E1F8`.

**MIDI settings cell** (clock `+0x0C` = transport `+0x14`), holds bytes `0x08`/`0x09` of the System Settings record (`docs/session-transport.md` *Settings record*). Its readers (`FUN_0801CA18`, `FUN_08017EA8`, the transport handlers) agree with the bits mapped on hardware. Byte 0:

| Bit | Meaning |
|-----|---------|
| 2 | note Tx on |
| 3 | note Rx on |
| 4 | clock Rx **off** (incoming `F8` and transport messages are ignored) |
| 5 | clock Tx **off** |
| 6 | CC Tx off |
| 7 | CC Rx off |

Byte 1 & 7 is the analogue clock-out rate 1–5 (pads 17–21 = 1, 2, 4, 8, 24 PPQN; see *Sync out*). `FUN_0801CA18` copies these bits into the MIDI-in filter (`+0x0D`: bit 0 notes, bit 1 CCs, bit 2 realtime, always set) and into the MIDI-out flags (`+0x18`: bit 0 notes, bit 1 CCs, bit 2 clock and transport). It also copies the Osc 1/Osc 2 channels into both.

**MIDI in.** `FUN_080186B8(?, msg, ctx)` parses each incoming message. Its pointer is stored at `0x20002658`, and `ctx` is a 16-byte filter: `+0` = the MIDI controller, `+4` = the NRPN parser, `+8`/`+9` = the Osc 1/Osc 2 note channels, `+0x0B`/`+0x0C` = program-change channels, and `+0x0D` = the filter bits. The controller is a 0x84-byte object with vtable `0x08026FE4`, which has interfaces at `+4`, `+8`, `+0x0C` and `+0x10`. The parser routes:

| Message | Handler | Goes to |
|---------|---------|---------|
| `F8` clock | controller `+0x0C` `[0] FUN_0801C610` | clock state `vtable[0]` = `FUN_08017EA8` |
| `FA` / `FB` / `FC` / `F2` | controller `+8` `[0..3]` | `+0x24`: a source adapter (vtable `0x08027190`, id 0), which calls the transport's event interface (`this + 8`) with source 0 |
| `F3` song select | controller `[12] FUN_0801C658` | writes cell `+0x2C`, then the transport's Song Select |
| `9n`/`8n` on the Osc channels | controller `[0] FUN_08018618` / `[1] FUN_080185E8` | `+0x1C`: a source adapter (vtable `0x080271AC`) into the note router (source 2, router channels 6–7). It also keeps a held-note bitmap at `+0x48`. Velocity 0 counts as a note-off. |
| CC, NRPN | controller `[7] FUN_080185A8` etc. | parameter object `+0x20` |

`FUN_08017EA8` (clock in) does nothing while clock Rx is off. In run state 2 (MIDI Start received, waiting) the first `F8` resets the timestamp rings and counters, re-arms the 1 ms timer and calls `FUN_08021790`, which plays tick 0, sets state 4 and sends the first `F8` out. After that, each `F8` increments `+0x54`. When the clock source is MIDI, the handler also forwards the `F8` to MIDI out (`+0x08`), so in slave mode clock out is a **thru** of the incoming clock. Every ms the engine turns `+0x54` into pending ticks (`+0x58`), timestamps them for the least-squares tempo estimate (`+0x68`/`+0x6C`, published with `FUN_0801AECC`) and plays the ticks.

**Transport events** (`this + 8` interface; source 0 = MIDI in, 1 = the second adapter at transport `+0x5C`, presumably the panel):

- With source 0, all events are ignored while clock Rx is off. Start is also ignored while the clock source is analogue.
- **Start** while already running is only forwarded to MIDI out, and only when slaved to MIDI.
- A MIDI **Start** sends `FA` out, sets the clock source to 1 (MIDI) and the run state to 2. A local Start sends `FA` and goes to run state 1 (internal) or 3 (analogue).
- **SPP** while stopped sets step = `spp & 15`, tick = step × 6, and clock = `spp × 6` (`FUN_0801B8B8`).
- **Song Select** is forwarded to MIDI out while stopped or slaved.

**MIDI out** (0x1C-byte object, vtable `0x0802709C`, channel table at `+0x0C`, flags at `+0x18`, all via `FUN_0801AEE8(3, msg, …)`):

| Slot | Function | Sends | Gate |
|------|----------|-------|------|
| 0 | `FUN_08018B48` | `9n` note on (`n` = channel[track]) | flags bit 0 |
| 1 | `FUN_08018B20` | `8n` note off | flags bit 0 |
| 2 | `FUN_08018AF2` | `FA` Start | flags bit 2 |
| 3 | `FUN_08018A08` | `FB` Continue | flags bit 2 |
| 4 | `FUN_08018AC4` | `FC` Stop | flags bit 2 |
| 5 | `FUN_08018A92` | `F2` SPP | flags bit 2 |
| 6 | `FUN_08018A64` | `F3` Song Select | flags bit 2 |
| 7 | `FUN_08018A36` | `F8` clock | flags bit 2 |

The `this + 4` interface (vtable `0x080270C4`, slots 2–6) is the transport's `+0x10`. The `this + 8` interface (`0x080270E0`, slot 7) is the clock state's `+0x08`.

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

## Recording (#33)

Live input reaches the recorder hub through merger stage 3 (above). The hub (0x18 bytes, vtable `0x08027154`) is:

| Slot | Function | Role |
|------|----------|------|
| `[0]` | `FUN_0802345C(note, vel, track)` | note on → per-track recorder `[0]` |
| `[1]` | `FUN_0802341C(note, track)` | note off → recorder `[1]` |
| `[2]` | `FUN_08023370(param_id, value)` | knob move: table `DAT_08023410` (2 bytes per id: recorder index, lane; `FF` = not recordable) picks the recorder and lane. It sets the lane's bit in the recorder's touched-lanes bitmap cell `+0x2C` and stores the value in `+0x28[lane]`. |

All three do nothing unless the transport cell (hub `+8`, transport `+0x1C`) has field 0 ≠ 0, field 1 ≠ 0 and the third field = 0. That is presumably record armed, playing, and not some pending state. The order isn't confirmed.

**Per-track recorders** (hub `+0x0C`/`+0x10`/`+0x14`) are 0x288-byte objects built by `FUN_08021128` (base vtable `0x08029F14`). They differ in lane count `+0x24`: track 0 has 42 lanes (vtable `0x080270FC`), track 1 has 12 and track 2 has 0. Byte `+0x284` enables velocity recording; it is 0 on track 2. Fields:

| Offset | Content |
|--------|---------|
| `+0x0C` / `+0x10` | current and next (pattern, step, tick-in-step) position cells |
| `+0x14` | pattern cell compared on a write (probably the pattern on screen) |
| `+0x18` / `+0x20` | transport and tick cells, subscribed with masks 7 and 1 |
| `+0x1C` | running tick counter cell: the 16-bit note start and release times |
| `+0x28` | `lanes + 1` pending values (`FFFF` = none). The extra last entry is the **patch change** lane. |
| `+0x2C` / `+0x30` / `+0x34` | touched-lanes bitmap cell, last-tick-recorded bitmap, last values |
| `+0x38`–`+0x3B` | previous rate class, pattern, step, micro-step (for interpolation) |
| `+0x3C` | "in the first half of a step" flag |
| `+0x40` / `+0x44` | pattern data (140 bytes each) / automation lanes (`(pattern × lanes + lane) × 0x6C`: a 12-byte mask + 96 values) |
| `+0x48` | cell committed after a write ("pattern edited") |
| `+0x50`–`+0x57` | pending note: note (0 = none), velocity, released flag, start time `+0x54`, release time `+0x56` |
| `+0x54 + note × 4` | per-note slot, notes 1–139: bit 0 held, bits 1–4 step, bits 5–8 pattern, bits 9–15 velocity, then the 16-bit start time |

Recorder vtable `0x080270FC`:

| Slot | Function | Role |
|------|----------|------|
| `[0]` | `FUN_08023208` | note on: reads the position (`FUN_08021844`) and the start time, then calls `[3]` |
| `[1]` | `FUN_08016A72` | note off → `[4]` with the release time |
| `[2]` | `FUN_08017248` | cell observer (also via the `+8` thunk `FUN_080175C8`): step placement and automation, below |
| `[3]` | `FUN_08023042` | makes the note pending. An earlier pending note that hasn't been placed yet is dropped. |
| `[4]` | `FUN_080231B8` | note off: for the pending note it only stores the release time. For a placed note it writes the step (`FUN_08021334`). |
| `[5]` | `FUN_080234A0` | sync rate of the current pattern (field 1 of cell `+0x4C`) |
| `[6]` | `FUN_0802314E(pattern, step)` | places the pending note at that step. If it has already been released, it writes it at once. |
| `[7]` | `FUN_080230C2(pattern, step, time)` | the same, then writes every note still held (1–139) with `time` as the release (record stop) |
| `[8]` | `FUN_080230AA(lane, pattern)` | automation lane pointer |
| `[9]` | `FUN_08023098(pattern, step, v)` | writes step byte 4 (patch change) |

**Note placement is quantised to the nearest step**, but not when the key is hit. `FUN_08021844` compares the tick-in-step with half the step period, `(ticks[rate] + 1) >> 1` (table `0x08029F6C`). In the first half it returns the current step, in the second half the next one. A note-on only makes the note pending. The observer places it when the clock next crosses a half-step (flag `+0x3C` goes from 1 to 0), at the step that is current then. So a note hit early in step *n* lands on *n*, and one hit late in *n* lands on *n + 1*. Only one note per track can be pending, so of two notes inside the same half-step only the second is kept.

**Step write** `FUN_08021334(rec, note, release_time)`, for a held note:
- byte 0 = note;
- byte 1 = velocity if `+0x284` is set;
- byte 2 = gate = `(release − start)` in micro-steps (`6 × whole steps + remainder × 6 / ticks_per_step`), capped at 96 and at least 1. `ticks_per_step` comes from the pattern's own sync rate (footer byte 1, pattern `+0x81`).

It then clears the held bit. When the note's pattern matches cell `+0x14`, the caller commits `+0x48`. The raw start time stays in the slot, so **the off-grid offset (`start − step start`) is known at write time and just discarded**. That is the hook point for #33.

**Automation**: on each tick the observer walks the lanes whose bit is set in `+0x2C`. Once a knob has moved during recording, its lane stays selected until recording stops. The micro-step comes from the tick-in-step: it is used directly at 1/16 (6 ticks per step), looked up in table `DAT_080175C0` for the faster rates, and is `tick × 6 / ticks` for the slower ones. With a new value, `FUN_08023266(lane, step, micro, v)` writes it and sets the mask bit. Without one, `FUN_0802323C` clears the micro-step, so only change points stay. When the clock skipped one or two micro-steps since the last write (rates faster than 1/16), the gap is filled by linear interpolation (`(a + b) / 2`, or `(2a + b) / 3` and `(a + 2b) / 3`). At slower rates nothing is interpolated.

**Record stop** (transport cell change, mask 7): the observer writes any pending patch change, calls `[7]` at the rounded position with the tick counter as release, and clears the touched-lanes and last-tick bitmaps.

## Open

- Inside the synth engine `FUN_080226BC` (Osc 1/Osc 2 voice assignment), and the merge stages' note priority.
- Confirm sources 0 and 2 (keys, MIDI in) from their callers.
- Which transport (USB / DIN) each `FUN_080186B8` call comes from, and how the first clock source wins; the source-detection bits `+0x4C` against the guide's priority rule (analogue first).
- What source 1 of the transport events is (panel Play?), and the `+0x60`… adapters.
- Recording: the order of the transport-cell fields that gate the hub, what sets `+0x284` on tracks 0 and 1, and the track-1 recorder vtable (`DAT_0800EEEC`). Step entry (#34): which UI handlers write step cells.
- The 6th sync-out mask `0x80810` (the guide lists only 5 rates). Bits 0–1 of the MIDI settings byte: no reader found yet.
