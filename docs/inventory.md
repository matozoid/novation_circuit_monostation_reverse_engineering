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
| Grid pads (velocity-sensitive, RGB-lit) | 32 (4 × 8) | [4] | analogue pressure sensors on the ADC (raw u16 per pad at `0x2000155E`) → per-pad state machine in the main loop (~`0x0800DC00`) → `button_event` (`0x0801E398`) → `control_dispatch` ids `0x00`–`0x1F` (low byte = velocity) → callback `0x20002654` → active view slot 0/1 (`ui_root_pad_down`) | known. Velocity: once raw > `0x95`, the peak over 8 frames is mapped 0…`0xF00` → 0…255 and looked up in the 256-byte curve at `0x200000E8` (default from `.data`, ramp to 127; uploadable with the "MDLITH" SysEx, `docs/sysex-handlers.md`). Note-off when raw drops to ≤ 2. Held pads also send pressure updates (id bit 15 set when the pressure moves by more than `0x13`), but `control_dispatch` drops them, so aftertouch is measured and thrown away. |
| Rotary pots | 16: Volume [1], Tempo/Swing [2], Coarse [18], Fine/PW [19], LFO Rate [20], mixer Osc 1/Osc 2/Sub/Noise/Audio In/Ring [23–28], Frequency [32], Resonance [33], Overdrive [34], Distortion Level [37], Mod Depth [40] | | ADC1 + DMA → `adc_filter_push` (moving average) → `pots_scan` (threshold, `map_range`) → `control_dispatch` ids `0x20`–`0x2D` (pots 0–13, callback `0x20002F34`) → `ui_root_pot` → section view → `param_set` | known (ids in *Control ids* below, mapped on the unit 2026-10-28). `pots_scan` descriptors: `0x08027A98` (16 entries: ids `0x20`, `0x21`, `0x24`–`0x31`, so 12 pots + 4 faders), `0x080274FC` (Osc Coarse and Fine/PW, ids `0x22`, `0x23`, own curve `0x08027988`). Tempo/Swing and Mod Depth are endless dual-wiper encoders: ids `0x32`/`0x33` and `0x34`/`0x35` (descriptors `0x08026ECC`) give signed deltas (`input_poll_task` `0x0801BE20`). **Volume is digital:** pot `0x20` has no patch param; `control_dispatch` writes it to the master-volume target (`0x200028C4` byte 0), and `volume_cv_step` slews it to the VCA CV. Pot pickup (setting byte `0x09` bit 3) and the Osc coarse/fine ±`0x14` pickup window are known. |
| Envelope faders (A, D, S, R) | 4 | [29] | `control_dispatch` ids `0x2E`–`0x31` (callback `0x20003124`) → `ui_root_fader` → envelope view → params `0x20`–`0x23` | known |
| Buttons | 34 lit buttons: Tap [3], Osc 1 / Osc 2 / Mod Seq [5], Note / Velocity / Gate [6], Pattern Settings [7], Scales [8], Patterns [9], Patches [10], Play, Record [11], Oct down / up [12], Clear [13], Save, Sessions [14], Shift [15], Range [16], Waveform [17], LFO Wave [21], LFO Sync [22], Filter Shape [30], Slope [31], Bypass [35], Distortion Type [36], Mod Source [38], 6 Mod Destination buttons [39] | | `ui_button_event` (`0x08011FBC`) routes to the active view or global handlers; long/short press via held-button timers (`ui_root_slot4`); Shift functions per view | known; ids in *Control ids* below (`0x00`–`0x21`, mapped on the unit). The main loop's button scan maps matrix bits to ids with the table at `0x08026E8A` (64 bits, `0x22` = no button) |
| Power switch (soft, rear) | 1 | [10] | `power_calib_task` (`0x0801C208`, 1 kHz soft timer): long-press detect; shutdown fades the master volume out, raises GPIOC pin 9 (mute), runs `bootloader_handover` (`0x08024940`: stops peripherals and masks every NVIC IRQ; despite the name it's a shared quiesce routine), drops the GPIOE pin 0 power hold, then loops on IWDG refresh + WFI in stop mode | known |
| Boot key combos | | p. 90 | Shift at power-on → System Settings view; Scales + Note + Velocity → bootloader (checked by the bootloader, `docs/bootloader.md`) | known |

#### Control ids

Read on the unit with `tools/ctllog.py map`, which turns on the stock firmware's debug reports (`docs/sysex-handlers.md` *Debug reports*). Pads and pots/faders/encoders share the `control_dispatch` id space; buttons have their own.

| Id | Control | Id | Control |
|----|---------|----|---------|
| `0x00`–`0x1F` | pads, row by row from the top left (`0x00`–`0x07` top row, `0x18`–`0x1F` bottom row) | `0x28` | mixer Audio In |
| `0x20` | Volume | `0x29` | mixer Ring |
| `0x21` | LFO Rate | `0x2A` | Overdrive |
| `0x22` | Osc Coarse | `0x2B` | filter Resonance |
| `0x23` | Osc Fine/PW | `0x2C` | filter Frequency |
| `0x24` | mixer Osc 1 | `0x2D` | Distortion Level |
| `0x25` | mixer Osc 2 | `0x2E`–`0x31` | faders A, D, S, R |
| `0x26` | mixer Sub | `0x32`/`0x33` | Tempo/Swing encoder |
| `0x27` | mixer Noise | `0x34`/`0x35` | Mod Depth encoder |

| Button id | Button | Button id | Button |
|-----------|--------|-----------|--------|
| `0x00` | Tap | `0x11` | Osc 1 |
| `0x01` | LFO Wave | `0x12` | Osc 2 |
| `0x02` | LFO Sync | `0x13` | Mod Seq |
| `0x03` | Mod Source | `0x14` | Oct down |
| `0x04` | Mod Destination Pitch | `0x15` | Oct up |
| `0x05` | Mod Destination PWM | `0x16` | Clear |
| `0x06` | Mod Destination Amp | `0x17` | Save |
| `0x07` | Mod Destination Filter | `0x18` | Sessions |
| `0x08` | Mod Destination Dist | `0x19` | Shift |
| `0x09` | Mod Destination Aux CV | `0x1A` | Note |
| `0x0A` | Range | `0x1B` | Velocity |
| `0x0B` | Waveform | `0x1C` | Gate |
| `0x0C` | Filter Shape | `0x1D` | Pattern Settings |
| `0x0D` | Slope | `0x1E` | Patterns |
| `0x0E` | Bypass | `0x1F` | Patches |
| `0x0F` | Distortion Type | `0x20` | Record |
| `0x10` | Scales | `0x21` | Play |

### 1.2 Panel outputs (LEDs)

All LEDs are driven over SPI with DMA refresh (`DMA1_Stream4_IRQHandler` re-arms the scan on TIM10). Each tick `led_frame_compose` (`0x08017A74`) resolves colour indices, `leds_update` builds the frame buffers at `0x200004A4`/`0x200005E0`. (`volume_cv_step`, once thought to ramp the LED brightness, slews the master volume.) There is no separate 34-LED group: `container_view_ctor` (`0x08011628`) passes 34 (`0x22`) to `view_base_ctor` as the size of its first input port, which is the count of button ids `0x00`–`0x21`, not a count of LEDs.

| LEDs | Count | Firmware path | Status |
|------|------:|---------------|--------|
| RGB LEDs: 32 pads + RGB-lit buttons | 82 | `leds_render_rgb` (`0x0801EA30`) from colour table `0x080290D4`; views fill them via slot 8 (`view_rgb_leds`). Palette copy at `0x200002A0`; colour indices `0x40`+ scaled by params (brightness follows e.g. pot values). | known: map in *LED map* below (32 pads, 18 buttons, 6 Mod Destination buttons, Range 4 and Waveform 4, and LEDs by 14 knobs and the 4 faders) |
| Single-colour LEDs: parameter indicators (osc range/waveform, LFO wave and rate blink, filter shape/slope/bypass, distortion type, …) | 21 | `leds_render_mono` (`0x0801EB68`); views fill them via slot 9, gathered by `mono_leds_compose` through table `0x0802A0BE` | known: map in *LED map* below |
| Control pins | 3 | GPIOE pin 0: power hold, set high early in boot and low at shutdown just before stop mode. GPIOC pin 9: set high at boot and at shutdown, driven low 200 ms after boot by the one-shot `gpioc9_low_timer`, so most likely the audio mute. GPIOE pin 11: toggled by a 1 s soft timer (`heartbeat_toggle`) and written nowhere else, so most likely a heartbeat or debug LED. Both timers are static soft-timer records in `.data` (`0x2000026C`, `0x20000284`). | partial: PE0 is known; PC9 and PE11 are inferred and not checked on the board |

There is no display.

#### LED map

Mapped on the unit 2026-10-28 with `overlays/ledtest` and `tools/ledmap.py`. All LEDs are levels in the 352-byte output buffer at `0x200021F8` (`0x200005E0` +8), which `leds_update` turns into the SPI frame. An RGB LED takes three channels; its index is the order of `leds_render_rgb`'s table at `0x08029DCA`.

| RGB LED | Physical |
|---------|----------|
| 0–31 | pads, row by row from the top left (same order as pad ids `0x00`–`0x1F`) |
| 32–49 | buttons Scales, Osc 1, Osc 2, Mod Seq, Oct down, Oct up, Clear, Save, Sessions, Shift, Note, Velocity, Gate, Pattern Settings, Patterns, Patches, Record, Play |
| 50–55 | LFO Rate knob, faders A, D, S, R, Mod Depth knob |
| 56–61 | Mod Destination buttons Pitch, PWM, Amp, Filter, Dist, Aux CV |
| 62–63 | Osc Coarse, Osc Fine knobs |
| 64–67 | Range 16', 8', 4', 2' |
| 68–71 | Waveform sine, triangle, saw, pulse |
| 72–77 | mixer Osc 1, Osc 2, Sub, Noise, Audio In, Ring 1+2 knobs |
| 78–81 | Overdrive, Resonance, Frequency, Distortion Level knobs |

| Mono channel | Physical | Mono channel | Physical |
|--------------|----------|--------------|----------|
| 0 | Tempo (orange) | 12 | Mod Source Seq |
| 1 | LFO Sync | 13 | Mod Source LFO |
| 2 | LFO wave S&H | 14 | Mod Source Env |
| 3 | LFO wave square | 16 | Filter Shape LP |
| 4 | LFO wave saw | 17 | Filter Shape BP |
| 5 | LFO wave triangle | 18 | Filter Shape HP |
| 6 | Tempo (white) | 19 | Slope 24 dB |
| 8 | Distortion Type 3 | 20 | Slope 12 dB |
| 9 | Distortion Type 2 | 21 | Bypass Noise |
| 10 | Distortion Type 1 | 22 | Bypass Osc 2 |
| 11 | Mod Source Velocity | | |

Channels 7 and 15 aren't used, and channels 224, 238, 239, 312, 326 and 327 are driven but light nothing (unconnected driver outputs).

### 1.3 Synth engine (analogue voice, digitally controlled)

The voice is analogue; the firmware computes every control voltage at 2 kHz (`engine_tick_2khz` → `synth_engine_update` `0x0801D208`) and sends it out four ways:

- **Modulation sample-and-hold (internal DAC channel 1).** `TIM6_DAC_IRQHandler` walks a 29-entry table at `0x20000880` (`{u16 *value, u8 sel[2]}`, in two halves of 15 and 14 entries; the engine restarts each half). Per entry it writes `*value` to `DAC_DHR12R1`, sets the 3-bit address on PB6–PB8 (pin group `0x20000980`) and then pulls one of three active-low mux inhibits PE7/PE8/PE9 (pin group `0x20000AA8`). That gives 3 chips × 8 = 24 held outputs. Chip 0 address 5 has a further 4-way bank on PC13/PC14 (pin group `0x20000AC8`), stepped once per table pass.
- **Oscillator pitch (internal DAC channel 2).** `osc_pitch_cv_update` writes each core's pitch to `DAC_DHR12R2` and strobes one of three pitch S&H through PD10/PD11/PD12 (pin group `0x20000038`). Each core also gets a timer compare: core A TIM1_CH1 on PA8, core B TIM3_CH2 on PB5, core C TIM4_CH2 on PD13 (tables `0x08026F10`/`2C`/`40`).
- **SPI3 (PC10–12).** It carries a 24-bit switch shift register (`synth_update_modes` packs the word at `0x20002F80`) and a dual 12-bit DAC for the rear CV outputs (`spi_dac_write`; bit 7 of the first byte picks the channel). `spi_dac_send` sends the 3 transfers, and `DMA1_Stream2_IRQHandler` sequences the chip selects.
- **GPIO.** The gate output and the PC9 mute.

| S&H value | Chip/addr | Meaning (writer) |
|-----------|-----------|------------------|
| `0x2000354A` | 2/0 | Osc 1 mixer level (`synth_update_mixer`, param `0x13`) |
| `0x2000338E` | 2/7 | Osc 2 level (`0x14`) |
| `0x20003324` | 2/5 | Sub level (`0x15`) |
| `0x2000335A` | 2/3 | Noise level (`0x16`) |
| `0x20003358` | 2/2 | Audio In level (`0x17`) |
| `0x20003500` | 2/4 | Ring mod level (`0x18`) |
| `0x20003378`, `0x2000337A` | 1/2, 0/0 | filter overdrive (`0x19`), two curves |
| `0x20003326`, `0x20003398` | 0/2, 0/4 | filter cutoff, two copies with their own trims (key tracking, 4 mod depths; `synth_engine_update`) |
| `0x20003524` | 1/4 | resonance, slewed from `synth_update_resonance`'s target |
| `0x20003548` | 0/1 | VCA: amp envelope × patch level curve |
| `0x2000337E`, `0x20003380`, `0x20003382` | 2/6, 2/1, 1/1 | distortion: three drive CVs from amount + mods; type I/II/III picks which are driven (`synth_engine_update`, `synth_update_distortion`) |
| `0x20003370`, `0x2000333C`, `0x20003354` | 1/0, 1/7, 1/5 | pulse width for cores A, B, C (core struct `+0x14`) |
| `0x200028C6` | 1/6 | master volume CV: `volume_cv_step` (2 kHz) slews byte 1 of `0x200028C4` one step toward the Volume pot (byte 0) and writes `0x79E + (0x861 − 16·level) · 0x1CB / 512` here, so volume changes don't click; shutdown fades it out |
| `0x200009F6` | 0/3 | calibration constant (`calibration_apply`) |
| `0x200033FE` | 0/5 ×4 banks | banks 0–2: calibration trims; bank 3: filter-shape trim (`synth_update_modes`) |

The chip/addr pairs come from the table's `sel` bytes. Rows that appear twice in the table are refreshed twice per pass.

Switch word `0x20002F80` (24 bits over SPI3): bits 0–1 and 13–15 carry the byte at `0x2000086C` (≤ 31). Bit 8 is bypass bit 1, bit 9 bypass bit 0, bit 10 is "slope = 12 dB", bits 16–17 Osc 1 waveform, bits 18–19 Osc 2 waveform, bits 20–21 param `0x4C` (a global outside the patch; glide-related), bits 22–23 filter shape.

| Block | Controls (patch params, `tools/patchbank.py` `PARAMS`) | How firmware drives it | Status |
|-------|---------------------------------------------------------|------------------------|--------|
| Osc 1, Osc 2 | range 16'–2' (5 steps), waveform (sine, triangle, saw, pulse), coarse ±1 oct, fine ±100 cents, pulse width, octave, per-osc glide | three oscillator cores: A = Osc 1, B = Osc 2, C follows Osc 1's pitch and mods (most likely the sub oscillator). Pitch via DAC 2 + S&H and a timer compare per core, PW on the mux, waveform in the switch word | known (core C = sub is inferred) |
| Osc sync | on/off (Shift + Osc 2) | pin / mode bit (`synth_update_modes`) | partial |
| Sub oscillator, noise, ring mod (1×2), external audio in | mixer levels (pairs, 8-bit) | six mixer level S&H slots (`synth_update_mixer`, params `0x13`–`0x18`; overdrive scales them all). Audio In gain (Shift + Audio In, UG p. 30) isn't a param: `mixer_view_pot` stores pot / 8 (0–15) in an observable cell. `synth_routing_on_cell` (probably misnamed) copies a cell value ≤ 31 to `0x2000086C`, which goes into switch-word bits 0–1/13–15, so that is most likely the gain switch. | partial: gain path needs a hardware check |
| Filter | cutoff, resonance, overdrive (pre-filter), shape LP/BP/HP, slope 12/24 dB, bypass (Osc 2 / noise / both), key tracking 0–7 | two cutoff S&H slots, resonance and two overdrive slots; shape, slope and bypass in the switch word; per-shape trim in S&H bank 3 | known |
| VCA | patch level, envelope or mod amount | one S&H slot (amp envelope × patch level curve) | known |
| Distortion (post-filter) | amount, type I/II/III | three drive S&H slots; the type selects which are driven (no type bits in the switch word) | known |
| Aux CV output | mod-matrix destination | SPI3 DAC channel: clamped sum of the 4 aux mod depths × sources, + `0x800` | known |
| Calibration trims | 32 per-unit u16 values in external flash `0x0000`–`0x0043` (CRC-32) | `calibration_apply` scales them into the engine's trim globals; the factory self-test (`calib_*`, 42 table entries at `0x0802941C`) measures them with the ADC | partial; **never erase this table** (`docs/session-transport.md` *Storage map*) |

### 1.4 Rear and side connectors

| Jack | UG | Firmware path | Status |
|------|----|---------------|--------|
| LINE OUT (¼" TRS, mono) | [1] | analogue; level set by the voice (no digital audio path) | n/a |
| Headphones (3.5 mm) | [11] | analogue | n/a |
| AUDIO IN (line) | [2] | analogue into the mixer; level is a param, gain is a switched setting (see *Sub oscillator…*) | n/a |
| MIDI IN / OUT / THRU (3.5 mm TRS) | [3] | USART1 (`0x40011000`, ISR `0x08023FE1`). THRU is probably hardware. | known (send/receive); the parser's source per port is open |
| USB-B (MIDI class compliant) | [8] | USB OTG_FS (`0x50000000`, ISR `0x0801EEFC`), ST USB device library + USB-MIDI class. The descriptors also name `"AUDIO Interface"`, but the UG says USB carries no audio. OTG_HS isn't used: no code references `0x40040000`, and its vector just branches to the FS handler. | known |
| CLOCK OUT (5 V) | [4] | TIM2 one-shot pulse per tick mask (1/2/4/8/24 PPQN, `docs/sequencer.md` *Sync out*) | known |
| CLOCK IN (2 PPQN) | [5] | edge interrupt (EXTI15_10, ISR `0x08023A79`), 12 ticks per pulse, tempo by least-squares fit | known |
| NOTE OUT CV (1 V/oct) and GATE (5 V) | [6] | from Osc 1's notes, written in `synth_engine_update`: CV on the other SPI3 DAC channel (`(pitch − 0x180000) >> 8 × 0x767 / 10000 + 9`), gate via `output_pin_set` | known |
| AUX CV (±5 V) | [7] | SPI3 DAC channel, source chosen in the mod matrix (see *Aux CV output*) | known |
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
| SPI2, SPI3, SPI4 | SPI2 (PB13–15, AF5, TX on DMA1 Stream 4, struct `0x200005A0`): LEDs. SPI3 (PC10–12, AF6, struct `0x20000A14`, chip select in `DMA1_Stream2_IRQHandler`): synth SPI DAC. SPI4 (PE12–14, AF5, struct `0x2000051C`): NOR flash. SPI1 isn't used; `spi_init` only compares against its base to pick the APB2 clock. | known |
| ADC1 + DMA | pots, faders, calibration measurements | partial |
| DAC `0x40007400` | channel 1: modulation S&H mux (TIM6); channel 2: oscillator pitch S&H (§1.3) | known |
| USART6 | not used: it appears only in the inlined `USART_Init` APB2 clock compare (`0x0800CA28`/`0x0800CA7E`). USART1 (PA9/PA10, AF7, 31,250 baud) is the only UART. | known |
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
| Paraphonic voicing | Osc 1 and Osc 2 play separate notes (Modes 1 and 2, Shift + Scales) | `synth_note_on` / `synth_note_off`, `synth_voice_gate`; mode flag `0x20003204` | known for the engine: a track 0 note goes to Osc 1, and also to Osc 2 while Osc 2 holds no note; a track 1 note goes to Osc 2, and also to Osc 1 while Osc 1 is idle if the mode flag is 1. New notes win (last-note priority). On release of the sounding note the oscillator falls back to the nearest still-held note (the higher one on a tie), with its stored velocity. Which mode sets the flag to 1 isn't traced. |
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
| Mutate (Shift + Pattern Settings) | shuffles the steps between the pattern's start and end point (`pattern_ctl_on_cell`): for each position, `rng_read` picks a step from the remaining range and the two swap whole (note, velocity, gate, glide and the six micro-step cells on every page), so the notes stay but move. `seq_track_position` also uses the RNG for the random playback direction. | known |
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
| `overlays/randomise` | #13 per-field Mutate (gate, glide, velocity, pitch) and #45 patch randomiser (Mutate in the Patches view: a mild random step from the current patch) |
| `overlays/transpose` | #21 live transpose of the Osc sequences by scale degrees |
| `overlays/ledtest` | SysEx `7D` lights chosen LED output channels and blanks the rest (for `tools/ledmap.py`) |
| `tools/ctllog.py` | turns on the stock debug reports and logs or maps control ids |
| `tools/ledmap.py` | steps through every LED channel with `overlays/ledtest` and records what lights |

## Open items collected

- Check on the board that core C is the sub oscillator and that `0x2000086C` is the Audio In gain.
- Confirm on the board that GPIOC pin 9 is the audio mute and GPIOE pin 11 is a heartbeat LED.
- Merge-stage note priority, and which mode sets the paraphonic flag `0x20003204` to 1.
