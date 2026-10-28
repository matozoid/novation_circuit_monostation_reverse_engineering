# Hardware and feature inventory

Two lists:

1. **Hardware**: everything on the unit that firmware can read or drive: panel controls, LEDs, synth circuits, jacks, and the MCU peripherals and storage behind them.
2. **Firmware features**: everything the stock firmware (build 800, "firmware 1.2") implements in software, plus what this project's patches and overlays add.

Each entry says how far it is understood and where the details are. Sources:
- **UG**: the user guide (`circuit-mono-station-ug-en-v1-2.pdf`). Bracketed numbers like [16] are its panel callouts (pp. 13–19).
- **FW**: the decoded image (`docs/firmware-map.md`, `docs/source-map.md`).
- **HW**: read from a unit.

Status: **known** = traced in the code (and usually confirmed on hardware); **partial** = located but not fully traced; **open** = not found in the code yet.

## 1. Hardware

### 1.1 Panel inputs

The firmware sees every control as an id in one event stream. `control_dispatch` (`0x0801E310`) routes ids below `0x20` to pads, `0x20`–`0x31` to knobs (18 ids) and `0x32`–`0x35` to signed pairs. Views get them through vtable slots 0–6 (pad down/up, button down/up, encoder, pot, fader; `docs/source-map.md` *Views*). The stock firmware also has `printf` log lines for each kind (`"Pad %d changed state…"` etc.), but `_write` discards them (`docs/firmware-map.md` *UI input*). Which id is which physical control isn't mapped yet; logging the events (point `_write` or `button_event`'s `sysex_send_debug` at SysEx) would map them on a unit.

| Control | Count | UG | Firmware path | Status |
|---------|------:|----|---------------|--------|
| Grid pads (velocity-sensitive, RGB-lit) | 32 (4 × 8) | [4] | GPIO matrix scan → `control_dispatch` ids `0x00`–`0x1F` → active view slot 0/1 (`ui_root_pad_down`) | known; how the velocity is measured is open |
| Rotary pots | 16: Volume [1], Tempo/Swing [2], Coarse [18], Fine/PW [19], LFO Rate [20], mixer Osc 1/Osc 2/Sub/Noise/Audio In/Ring [23–28], Frequency [32], Resonance [33], Overdrive [34], Distortion Level [37], Mod Depth [40] | | ADC1 + DMA → `adc_filter_push` (moving average) → `pots_scan` (threshold, `map_range`) → `ui_root_pot` → section view → `param_set` | partial: per-pot id mapping open. No Volume param exists, so Volume is probably analogue only (unverified). Pot pickup (setting byte `0x09` bit 3) and the Osc coarse/fine ±`0x14` pickup window are known. |
| Envelope faders (A, D, S, R) | 4 | [29] | `ui_root_fader` → envelope view → params `0x20`–`0x23` | known |
| Buttons | 34 lit buttons: Tap [3], Osc 1 / Osc 2 / Mod Seq [5], Note / Velocity / Gate [6], Pattern Settings [7], Scales [8], Patterns [9], Patches [10], Play, Record [11], Oct down / up [12], Clear [13], Save, Sessions [14], Shift [15], Range [16], Waveform [17], LFO Wave [21], LFO Sync [22], Filter Shape [30], Slope [31], Bypass [35], Distortion Type [36], Mod Source [38], 6 Mod Destination buttons [39] | | `ui_button_event` (`0x08011FBC`) routes to the active view or global handlers; long/short press via held-button timers (`ui_root_slot4`); Shift functions per view | known for most; button id table not written down in one place |
| Power switch (soft, rear) | 1 | [10] | `power_calib_task` (`0x0801C208`, 1 kHz soft timer): long-press detect, shutdown (settings save, IWDG refresh + WFI loop) | partial |
| Boot key combos | | p. 90 | Shift at power-on → System Settings view; Scales + Note + Velocity → bootloader (checked by the bootloader, `docs/bootloader.md`) | known |

### 1.2 Panel outputs (LEDs)

All LEDs are driven over SPI with DMA refresh (`DMA1_Stream4_IRQHandler` re-arms the scan on TIM10). Each tick `led_frame_compose` (`0x08017A74`) resolves colour indices, `leds_update` builds the frame buffers at `0x200004A4`/`0x200005E0`, and `led_brightness_step` ramps brightness at 2 kHz.

| LEDs | Count | Firmware path | Status |
|------|------:|---------------|--------|
| RGB LEDs: 32 pads + RGB-lit buttons | 82 | `leds_render_rgb` (`0x0801EA30`) from colour table `0x080290D4`; views fill them via slot 8 (`view_rgb_leds`). Palette copy at `0x200002A0`; colour indices `0x40`+ scaled by params (brightness follows e.g. pot values). | partial: index → physical LED map open (82 = 32 pads + 50, not yet split into buttons) |
| Single-colour LEDs: parameter indicators (osc range/waveform, LFO wave and rate blink, filter shape/slope/bypass, distortion type, …) | 21 | `leds_render_mono` (`0x0801EB68`); views fill them via slot 9, gathered by `mono_leds_compose` through table `0x0802A0BE` | partial: index map open |
| 34-LED button group | 34 | `container_view_ctor` (`0x08011628`): a pure-render view over 34 LEDs, matching the 34 lit buttons | partial |
| Status pins | 2 known | GPIOE pin 11 toggled by a 1 s soft timer (`heartbeat_toggle`); GPIOC pin 9 driven low by a 200 ms one-shot (`gpioc9_low_timer`) | open: what they connect to |

There is no display.

### 1.3 Synth engine (analogue voice, digitally controlled)

The voice is analogue; the firmware computes every control voltage at 2 kHz (`engine_tick_2khz` → `synth_engine_update` `0x0801D208`) and writes them out through an **external SPI DAC** (`spi_dac_write`/`spi_dac_send`, 3 words per update) and the **internal DAC** with a sample-and-hold multiplexer stepped by `TIM6_DAC_IRQHandler`. Oscillator pitch also goes through TIM1/TIM3 values (`osc_pitch_cv_update`, per-unit calibration). Switched settings (waveform, filter shape/slope/bypass, distortion type) presumably go out on GPIO pin groups (`pin_group_write` writes table patterns to pin groups), but that isn't traced. Which DAC channel or pin feeds which circuit isn't mapped yet.

| Block | Controls (patch params, `tools/patchbank.py` `PARAMS`) | How firmware drives it | Status |
|-------|---------------------------------------------------------|------------------------|--------|
| Osc 1, Osc 2 | range 16'–2' (5 steps), waveform (sine, triangle, saw, pulse), coarse ±1 oct, fine ±100 cents, pulse width, octave, per-osc glide | pitch CV + TIM1/TIM3 (`osc_pitch_cv_update`), PW CV, waveform pin groups (`synth_update_osc1/2`, `synth_update_modes`) | partial |
| Osc sync | on/off (Shift + Osc 2) | pin / mode bit (`synth_update_modes`) | partial |
| Sub oscillator, noise, ring mod (1×2), external audio in | mixer levels (pairs, 8-bit) | mixer CVs (`synth_update_mixer`, params `0x13`–`0x19`). Audio In gain (Shift + Audio In) is not in the param table; where it lives is open. | partial |
| Filter | cutoff, resonance, overdrive (pre-filter), shape LP/BP/HP, slope 12/24 dB, bypass (Osc 2 / noise / both), key tracking 0–7 | cutoff CV with key tracking and 4 mod depths (`synth_update_cutoff`), resonance CV, mode pins | partial |
| VCA | patch level, envelope or mod amount | VCA CV | partial |
| Distortion (post-filter) | amount, type I/II/III | amount CV + type pins (`synth_update_distortion`) | partial |
| Aux CV output | mod-matrix destination | CV via DAC | partial |
| Calibration trims | 32 per-unit u16 values in external flash `0x0000`–`0x0043` (CRC-32) | `calibration_apply` scales them into the engine's trim globals; the factory self-test (`calib_*`, 42 table entries at `0x0802941C`) measures them with the ADC | partial; **never erase this table** (`docs/session-transport.md` *Storage map*) |

### 1.4 Rear and side connectors

| Jack | UG | Firmware path | Status |
|------|----|---------------|--------|
| LINE OUT (¼" TRS, mono) | [1] | analogue; level set by the voice (no digital audio path) | n/a |
| Headphones (3.5 mm) | [11] | analogue | n/a |
| AUDIO IN (line) | [2] | analogue into the mixer; level and gain are params | n/a |
| MIDI IN / OUT / THRU (3.5 mm TRS) | [3] | USART1 (`0x40011000`, ISR `0x08023FE1`). THRU is probably hardware. | known (send/receive); the parser's source per port is open |
| USB-B (MIDI class compliant) | [8] | USB OTG_FS (`0x50000000`, ISR `0x0801EEFC`), ST USB device library + USB-MIDI class. The descriptors also name `"AUDIO Interface"`, but the UG says USB carries no audio. OTG_HS is also initialised and serviced (role open). | known |
| CLOCK OUT (5 V) | [4] | TIM2 one-shot pulse per tick mask (1/2/4/8/24 PPQN, `docs/sequencer.md` *Sync out*) | known |
| CLOCK IN (2 PPQN) | [5] | edge interrupt (EXTI15_10, ISR `0x08023A79`), 12 ticks per pulse, tempo by least-squares fit | known |
| NOTE OUT CV (1 V/oct) and GATE (5 V) | [6] | from Osc 1's notes, written in `synth_engine_update` (CV via DAC, gate via `output_pin_set`) | partial |
| AUX CV (±5 V) | [7] | DAC, source chosen in the mod matrix | partial |
| DC power 12 V | [9] | — | n/a |

### 1.5 MCU and board resources

| Resource | Use | Status |
|----------|-----|--------|
| STM32F42x/43x (DEV_ID `0x419`, 512 KB flash, Cortex-M4F) | everything | known (`docs/hardware.md`); exact part open (#58) |
| Internal flash | bootloader `0x08000000`–`0x0800BFFF`, app `0x0800C000`+, free from `0x0802BB20` | known; only the bootloader programs it |
| RAM | stack in CCM, heap fills SRAM1/2 and part of SRAM3; free: CCM below the stack, SRAM3 `0x200235B8`–`0x2002FFFF` | known (`docs/hardware.md` *Free RAM*) |
| Backup SRAM `0x40024000` | byte 0 = boot flag (stay in bootloader) | known |
| External SPI NOR flash (8 MiB) | calibration, settings record, 128 patch slots × 512 B (64 used), 33 sessions × 90,112 B | known (`docs/session-transport.md` *Storage map*); async DMA driver `nor_async_*` |
| Hardware RNG `0x50060800` | random steps, Mutate | known (`rng_read`) |
| Timers | TIM5 10 kHz time base (soft timers, sequencer, 2 kHz engine); TIM2 clock out; TIM6 DAC clock; TIM10 LED scan; TIM1/TIM3 osc pitch; TIM8/9/11/14 misc | partial |
| SPI1, SPI4 (+ SPI2/3) | LED drivers, SPI DAC, NOR flash | partial: which port drives which device is open |
| ADC1 + DMA | pots, faders, calibration measurements | partial |
| DAC `0x40007400` | CV via sample-and-hold multiplexer | partial |
| USART6 | initialised once; role open | open |
| IWDG | started by the bootloader (~6 s); the app refreshes it | known (`docs/bootloader.md`) |

## 2. Firmware features

### 2.1 Synth control

| Feature | What it does | Where | Status |
|---------|--------------|-------|--------|
| Parameter table | 74 synth params with CC / CC(8) / NRPN numbers, ranges and centres; matches Novation's MIDI PDF | table `0x0802A62A`, `PARAMS`, `docs/sysex-spec.md` | known |
| `param_set` | store a param, mark the patch edited, run its group's update function, send it to MIDI out | update table `0x0802A028`; `docs/source-map.md` *Synth parameters* | known |
| Digital ADSR envelope | rates from 7-bit times (`env_time_to_rate`), stages attack/decay/sustain/release, retrigger option | `synth_update_envelope`, `synth_engine_update`; `docs/firmware-map.md` *Analogue engine* | known |
| LFO | triangle, saw, square, sample-and-hold; free rate or 35 tempo-synced rates; key sync | `synth_update_lfo` (params `0x24`–`0x28`), `sine_lookup` | partial |
| Modulation matrix | 4 sources (Env, LFO, Mod Seq, Velocity) × 8 destinations (Osc 1/2 pitch, Osc 1/2 PW, Amp, Filter, Dist, Aux CV), each with its own depth, all additive | 32 `mod_*` params, `engine_bind_buffers`, `synth_mod_sources_init`, `modmatrix_view_*` | partial |
| Glide | per oscillator and per step; pitch slews toward the target in 16.16 fixed point | `osc_glide_step`, `synth_update_glide` | known |
| Paraphonic voicing | Osc 1 and Osc 2 play separate notes (Modes 1 and 2, Shift + Scales) | `synth_note_on` / `synth_note_off` (last-note priority), `synth_voice_gate` | partial: voice assignment open (`docs/sequencer.md` *Open*) |
| Key tracking | filter cutoff follows pitch, 8 amounts | `keytrack_view_*`, param `0x1F` | known |
| Patches | 64 user slots (2 pages of 32), load, save, preview, Init Patch (Shift + Patches), program change in | `patch_view_pads`, `patch_store_param`, `midi_ctl_program_change`; templates `Initial Patch` / `Classic Mono` | known |
| Pot pickup | knob only takes over once it passes the stored value | setting byte `0x09` bit 3, `knob_flag_leds` | partial |
| Calibration and factory self-test | 42-step test table, trims saved to NOR with CRC, report over SysEx `70 60 5A` | `power_calib_task`, `calib_*` | partial |

### 2.2 Playing

| Feature | What it does | Where | Status |
|---------|--------------|-------|--------|
| Note view, Dual view (Osc 1 + Osc 2), Expand view (Shift + Note) | grid as a keyboard, split or doubled | `keyboard_view_*` | known |
| Scales | 16 scales + root note; pads mapped to the scale | `scale_mask`, `scale_map_note`, `scale_snap_note`, `scales_view_*` | known |
| Octave shift | per oscillator, −6…+5 octaves (Oct buttons), both = reset | `octave_view_*` | known |
| Fixed velocity (Shift + Velocity) | pads play a fixed velocity (probably the router's forced 96) | router channels 0–2, settings bit | partial |
| Note routing and merging | keys, sequencer and MIDI in merged per track, then to synth, MIDI out and recorder | router + merger (`docs/sequencer.md` *Note routing*) | known |

### 2.3 Sequencer

Detail: `docs/sequencer.md`, `docs/session-format.md`.

| Feature | What it does | Status |
|---------|--------------|--------|
| 3 tracks | Osc 1 (16 patterns), Osc 2 (8), Mod Seq (8); 16 steps × 8 bytes per pattern | known |
| Step data | note, velocity (or mod value), gate in 1/6 steps (ties up to 96), glide, patch change per step | known |
| Clock engine | 24 PPQN, main loop at 1 ms, phase accumulator, catches up after slow passes | known |
| Tempo and Tap tempo | BPM byte per session; tap fits a tempo from timestamps (`clock_tempo_fit`) | known |
| Swing and swing sync | alternate ticks stretched; swing period selectable | known |
| External sync | MIDI clock in (USB/DIN, with `F8` thru), analogue clock in (2 PPQN), tempo estimate by least squares; MIDI Start/Continue/Stop/SPP/Song Select | known; the analogue-first priority rule isn't traced |
| Clock out | MIDI `F8` + transport out; analogue 1/2/4/8/24 PPQN | known (6th mask `0x80810` unexplained) |
| Pattern settings | start/end step, length, direction (forward, reverse, ping-pong, random), sync rate per pattern | known |
| Pattern chains | first…last pattern per track; cued or instant switching (Shift) | known |
| Mod Seq track | per-step mod value as a matrix source; Smooth (Shift + Mod Seq) interpolates | partial |
| Real-time recording | notes quantised to the nearest step (half-step rule), gate measured in micro-steps, velocity on Osc tracks | known (`docs/sequencer.md` *Recording*) |
| Knob automation | 42 lanes on track 0, 12 on track 1, 96 values per lane per pattern; interpolation at fast rates | known |
| Step editing | enter, delete, modify steps in Note / Velocity / Gate / Glide views | partial: which handlers write step cells (#8) |
| Clear, Duplicate (Shift + Clear) | steps, patterns, patches, sessions, automation | partial (`pattern_ctl_on_cell`) |
| Mutate (Shift + Pattern Settings) | shuffles steps of the current pattern (RNG) | partial |
| Pattern octave (Shift + Oct) | transpose a recorded pattern | known |

### 2.4 Sessions and storage

| Feature | What it does | Where | Status |
|---------|--------------|-------|--------|
| Sessions | 33 session images in flash, each 90,112 B: tempo, swing, chains, all patterns and automation, one patch | `docs/session-format.md` | known |
| Session switching | cued at pattern end, or instant with Shift | session control observers | partial |
| Save | session save state machine, patch save | `session_ctl_save`, `settings_save` | known |
| System Settings view (Shift at power-on) | Note/CC/Clock Rx/Tx, Osc 1/Osc 2 MIDI channels, clock-out rate, pot pickup; saved only at power-off | `docs/session-transport.md` *Settings record* | known |
| External flash driver | async erase/program/read over SPI DMA | `nor_async_*`, `nor_*` | known |

### 2.5 MIDI and SysEx

| Feature | What it does | Where | Status |
|---------|--------------|-------|--------|
| MIDI in | notes on the Osc 1/2 channels, CC, NRPN, program change, realtime, song select; filters from settings | `midi_in_parse`, `docs/sequencer.md` *MIDI* | known |
| MIDI out | notes (Osc 1/2 channels), params as CC / CC pair / NRPN, clock and transport; tx queue | `midiout_*`, `midi_tx_pump` | known |
| Patch SysEx | receive current patch / slot write, send on request (136-byte format) | `docs/sysex-spec.md` | known |
| Device commands | version `0x70`, reboot to bootloader `0x71`, restart `0x61`, external flash `p 0x40` read/write/erase, universal identity reply | `docs/sysex-handlers.md` | known |
| Session/bank dump and firmware update | served by the **bootloader**, not the app | `docs/session-transport.md`, `docs/update-protocol.md` | known |

### 2.6 System

| Feature | Where | Status |
|---------|-------|--------|
| C++ object graph built once at boot; observable cells with subscribers; views with a view stack | `docs/source-map.md` *Object model* | known |
| Soft timers: 1 kHz main-loop table, 2 kHz list, 10 kHz ISR list | `docs/sequencer.md` *Time base* | known |
| Power on/off and shutdown | `power_calib_task` | partial |
| newlib-nano + libstdc++ runtime | `docs/firmware-map.md` *Runtime libraries* | known |

### 2.7 Added by this project

| Name | What it does |
|------|--------------|
| `patches/hello`, `overlays/hello`, `overlays/hello_c` | test patches: report version 801 |
| `patches/memread` | `p 0x40` op 2 also reads MCU memory (bootloader dump, `DBGMCU_IDCODE`, SRAM3) |
| `overlays/randomise` | #13 per-field Mutate (gate, glide, velocity, pitch) and #45 patch randomiser |
| `overlays/transpose` | #21 live transpose of the Osc sequences by scale degrees |

## Open items collected

- Control id → physical control map (pads, 18 knob ids, 4 signed pairs, buttons).
- RGB (82) and mono (21) LED index → physical LED map.
- Synth DAC channel / multiplexer slot / pin group → circuit map.
- Which SPI port drives LEDs, DAC and NOR; role of USART6 and OTG_HS; GPIOC9 and GPIOE11.
- Paraphonic voice assignment and merge-stage note priority.
- Pad velocity measurement.
