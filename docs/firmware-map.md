# Firmware subsystem map

Addresses are from a **static** scan of the decoded application image (`firmware/monostation-firmware-800.bin`, load base `0x0800C000`): the vector table, word-aligned peripheral-register literals, debug format strings and their literal-pool cross-references, plus spot Thumb-2 disassembly. Entries below are handler entry points or the tightest xref cluster for each subsystem; exact function boundaries are in `docs/source-map.md`. See `docs/hardware.md` for the memory map and MCU.

## Active interrupt vectors

66 of 98 slots point at the default handler `0x08025EA9` (`b .`). The live vectors tie hardware to subsystems:

| Vector | Handler | Subsystem |
|--------|---------|-----------|
| Reset | `0x08025E61` | CMSIS startup → clock/GPIO init |
| SysTick | `0x0802403D` | 1 kHz tick / scheduler |
| OTG_FS | `0x0801EEFC` | USB (main port) |
| OTG_HS | `0x0801F4D9` | USB (second core, also serviced) |
| USART1 | `0x08023FE1` | DIN MIDI in/out |
| EXTI15_10 | `0x08023A79` | GPIO edge (button/encoder/ext sync) |
| TIM6_DAC | `0x08023B99` | DAC sample clock |
| TIM5 | `0x0801F545` | 32-bit 10 kHz time base (soft timers, sequencer clock; `docs/sequencer.md`) |
| TIM1_UP_TIM10 | `0x08023D89` | timer |
| TIM1_TRG_COM_TIM11 | `0x08023D39` | timer |
| TIM8_TRG_COM_TIM14 | `0x0801F61D` | timer |
| DMA1 Str2/3/4/5 | `0x08023B19 / F89 / F41 / AF1` | peripheral DMA (audio/ADC/serial) |
| DMA2 Str0/1/4 | `0x0801F505 / F4DD / 08023CA9` | peripheral DMA |

NMI/HardFault/.../PendSV form a contiguous fault-handler block at `0x08024061`–`0x0802406F`.

## Subsystems

### Startup / clock / GPIO init — `0x0800C000`–~`0x0800D600`
Reset handler `0x08025E61` does the standard `.data` copy / `.bss` zero, then the init block clustered here configures RCC (`0x40023800`, 15 refs), all of GPIOA–E, and enables DAC/ADC1/SPI/USART/TIM clocks. Backup-SRAM (`0x40024000`, 7 refs) is read here for persisted state.

### USB — OTG_FS `0x0801EEFC`, OTG_HS `0x0801F4D9`
OTG_FS (`0x50000000`) is the primary device port; its ISR reads `GINTSTS` and `ubfx`-dispatches interrupt bits (confirmed by disassembly). OTG_HS (`0x40040000`) is also serviced. Enumeration/descriptor code near `0x08019DFC`–`0x08019E38` references the USB string descriptors at `0x08028EA8`+ (`"AUDIO Interface"`, `"AUDIO Config"`, `"Focusrite A.E"`, `"Circuit Mono Station"`) — a composite USB-MIDI + USB-audio class device. The USB-MIDI path carries patch/firmware SysEx.

### DIN MIDI — USART1 `0x40011000`, ISR `0x08023FE1`
Serial MIDI in/out. USART6 (`0x40011400`) is referenced once in init; its role is not traced.

### UI input (pads / buttons / pots / sliders / encoders) — dispatcher `0x08017930`–`0x080179D8`
This tight cluster emits the debug format strings and is the input-event dispatcher:

| Site | String (`0x08028…`) |
|------|---------------------|
| `0x08017930` | `Slider %d moved to %d` |
| `0x08017954` | `Continuous pot %d moved with delta %d` |
| `0x08017978` | `Pot %d value changed to %d` |
| `0x080179A0` | `Button %d changed state to %d` |
| `0x080179D8` | `Pad %d changed state to %d with velocity %d` |

Each handler tail-calls `printf` with its string (`FUN_08017910`–`0x080179D8`). The output goes nowhere: the `_write` stub returns the length without sending anything (*Runtime libraries*). Pointing `_write` at a SysEx sender would turn these into an input-event log.

Hardware: GPIOA–E matrix scan for pads/buttons (12 refs GPIOE, 11 GPIOC, 10 GPIOD), ADC1 (`0x40012000`) for pots/sliders, and EXTI15_10 (ISR `0x08023A79`) for asynchronous edges (encoder detents / external sync input).

### UI output (LEDs / RGB pads) — SPI
Driven over SPI (`spi_init` enables SPI1 `0x40013000` and SPI4 `0x40013400`) to the LED/pad drivers, refreshed by DMA (`DMA1_Stream4_IRQHandler` re-arms the LED scan on TIM10). `led_frame_compose` resolves the 82 RGB LED colours each tick.

### Analogue engine (CV / gate out) — DAC `0x40007400`, TIM6_DAC ISR `0x08023B99`
Two converters drive the analogue CV targets (osc 1/2 pitch, pulse width, filter cutoff, VCA, distortion, aux CV):
- An **external SPI DAC**: `spi_dac_write` (`0x0802528C`) puts 2-byte words in the buffer at `0x20000970`, `spi_dac_send` (`0x08025260`) sends 3 words over SPI DMA, and `DMA1_Stream2_IRQHandler` runs the chip-select sequence.
- The **internal DAC** with a sample-and-hold multiplexer: `TIM6_DAC_IRQHandler` steps the multiplexer pin groups.

**Control signals are computed digitally at 2 kHz.** `engine_tick_2khz` (`0x0801BDC4`) calls `synth_engine_update` (`0x0801D208`), which glides pitch (`osc_glide_step`), runs the envelope and sums modulation into CV values; `osc_pitch_cv_update` (`0x080252D4`) converts pitch to DAC and TIM1/TIM3 values with the unit's calibration.
- **Envelope (ADSR):** digital. `synth_update_envelope` (`0x08021CA4`) turns params `0x20`–`0x23` into coefficients in a struct at `0x200006E0`: attack rate `+0xC`, decay rate `+0x10`, sustain level `+0x12`, release rate `+0x14`. `synth_engine_update` steps it: `+2` is the gate request (1 = note on → attack, 2 = note off → release, then cleared), `+1` the stage (0 attack, 1 decay, 2 sustain, 3 release), `+0x18` the 32-bit level, and `+0x1E`/`+0x20`/`+0x22` its scaled outputs. `synth_release_start` (`0x080218EC`) requests release.

### ADC — ADC1 `0x40012000` (+ DMA)
Pot/slider reads and CV/gate inputs.

### Timers
TIM5 (32-bit, ISR `0x0801F545`, 10 kHz) is the system time base: its 1 ms channel drives the main-loop soft timers, one of which is the sequencer clock engine `0x08017F1C` (`docs/sequencer.md`). TIM2 makes the analogue sync-out pulse. TIM6 is the DAC sample clock; TIM1/TIM8/TIM9/TIM10/TIM11 serve PWM/LED/auxiliary timing.

### Persistent storage — external NOR flash, Backup SRAM `0x40024000`
Patches, sessions, the settings record and the calibration table live in an external SPI NOR flash (`ext_flash_read`, `ext_flash_read_async`; map in `docs/session-transport.md` *Storage map*). The app doesn't program the MCU flash; only the bootloader does (`docs/bootloader.md`). Backup SRAM (7 refs) holds the boot flag across resets (`docs/sysex-handlers.md`). Default blobs `"Initial Patch"`, `"Classic Mono"`, `"Initial Session"`, `"User Session"` are referenced from `0x0800EEF8`–`0x0801B5C0` (patch/session init + storage layer).

## Runtime libraries

The toolchain is arm-none-eabi-gcc with **newlib-nano** and libstdc++. Their code fills `0x08025EAC`–`0x08026D94`, right after the startup file. Their constant data sits at the end of `.rodata` (`0x0802AE30`–`0x0802AEC8`), and their `.data` sits at the end of the `.data` image. The functions are identified by code shape, call graph and the data they touch. The image has no symbols. `src/files.tsv` gives one source file per library object, and `src/symbols.tsv` has the names.

| Range | Object | Functions |
|-------|--------|-----------|
| `08025EAC`–`08025EE0` | libstdc++ `new_handler`, `new_opv`, `new_op` | `get_new_handler` (`__new_handler` `0x200035A8`), `operator new[]`, `operator new` (`malloc`, retried through the handler) |
| `08025EE0`–`08025F3C` | `init`, `malloc` | `__libc_init_array` (empty `.preinit_array`, `_init`, `.init_array`), `malloc` → `_malloc_r(_impure_ptr, n)` |
| `08025F3C`–`08026080` | `memset`, `nano-freer`, `nano-mallocr` | byte-loop `memset`, `_free_r`, `_malloc_r` (first fit on the free list at `0x200035B0`, else `_sbrk_r`) |
| `08026080`–`080260E4` | `sbrkr`, `writer`, `closer` | `_sbrk_r`, `_write_r`, `_close_r` (errno at `0x200035B4`) |
| `080260E4`–`08026300` | `findfp`, `fwalk`, `stdio` | `_cleanup_r`, `std`, `__sfmoreglue`, `__sinit`, `__sfp`; `_fwalk_reent`; `__sread`/`__swrite`/`__sseek`/`__sclose` |
| `08026300`–`080266CC` | `wbuf`, `wsetup`, `fflush`, `lseekr`, `makebuf` | `__swbuf_r`, `__swsetup_r`, `__sflush_r`, `_fflush_r`, `_lseek_r`, `__swhatbuf_r`, `__smakebuf_r` |
| `080266CC`–`080267A0` | `readr`, `fstatr`, `isattyr`, `memcmp`, `memcpy`, `memmove` | byte-loop string functions |
| `080267A0`–`08026D94` | `printf`, `nano-vfprintf`, `nano-vfprintf_i` | `printf`, `__sfputc_r`, `__sfputs_r`, `_vfprintf_r`, `_printf_common`, `_printf_i` |
| `0802AE30`–`0802AEC8` (rodata) | `impure`, `findfp`, `nano-vfprintf*` | `_global_impure_ptr`, `__sf_fake_stdin/stdout/stderr`, `"#-0+ "`, `"hlL"`, `"efgEFG"`, the hex digit strings |
| `0802BAA0`–`0802BB04` (`.data` load) | `impure` | `impure_data` (`0x20000BB4`, 0x60-byte nano `_reent`: fake FILEs, locale `"C"`) and `_impure_ptr` (`0x20000C14`) |

The syscall stubs are app code, `0x0801EEAC`–`0x0801EEFC`, just before the USB code:
- `_read` returns 0. `_fstat` reports `S_IFCHR`.
- `_sbrk` grows the heap up to `0x200235B8`, and hits `bkpt #1` when it runs out (`docs/hardware.md` *Free RAM*).
- `_write` returns the length and sends nothing. `_lseek` returns 0, `_isatty` returns 1, `_close` returns -1.

App code calls only `operator new`, `operator new[]`, `memcpy`, `memset`, `memcmp`, `memmove` and the five input-event `printf`s, plus `__libc_init_array` from `Reset_Handler`. Everything else in the table is pulled in by `printf`.

The 28 bytes after the `.data` image (`0x0802BB04`–`0x0802BB20`, `8 × FF` then zeros) are unexplained.

## Where the details are
- Every controllable piece of hardware and every firmware feature, with status: `docs/inventory.md`.
- Sequencer, note routing, recording, MIDI clock and transport: `docs/sequencer.md`.
- SysEx dispatcher `FUN_080202a0` and its commands: `docs/sysex-handlers.md`.
- Patch store `FUN_080176e8` and session initialiser `FUN_08018CA4`: `docs/sysex-spec.md`, `docs/session-format.md`.
- Every function by address: `docs/source-map.md`.

Still open: which SPI port drives the LEDs and which the DAC, the role of USART6, the synth engine internals beyond the envelope, and which USB/DIN parser feeds the MIDI-in parser `FUN_080186B8`.

## Ghidra auto-analysis

`tools/ghidra/import_firmware.sh` imports the image, runs `MapMemory.java` (memory map + reset-handler seed) and leaves the DB in `ghidra-project/` (git-ignored). `ExportMap.java` writes the full function listing to `docs/ghidra-functions.txt`; `DecompileFunc.java <addr>…` prints decompiled C for investigation. (Ghidra 12.x headless has no Jython, so these are Java GhidraScripts, not Python.)

Auto-analysis finds 467 functions; with the vtable targets and push-prologue functions added via `MakeFunc.java`, the listing has 828. `src/symbols.tsv` names them all (`docs/source-map.md`). The first ones identified:

| Function | Role |
|----------|------|
| `FUN_08025E60` | Reset handler (CMSIS startup) |
| `FUN_08025F3C` | `memset(dst, val, len)` |
| `FUN_08026752` | `memcpy(dst, src, len)` |
| `FUN_08018CA4` | **Session initialiser** — builds the "Initial Session" struct in RAM out to offset `0x155DE` (≈87.9 KB, matching the 90,112-byte `.monostationsession`); layout decoded in `docs/session-format.md`. |
| `FUN_0800E1F8`, `FUN_0801B4E0` | reference the "Initial Patch" default — patch load/init (`docs/sysex-spec.md`). |
| `FUN_0800C2D0` | `main`: the main loop and its soft-timer table at `0x20003230`. |
| `FUN_0800FB5C` | `ui_construct`: builds the views and the UI root. |

Note: the UI debug-string pool words (`0x08017930`+) sit in a code region auto-analysis did not promote to a function (reached by tail call); revisit by disassembling from the enclosing block.

## Data tables noted
- `0x08027F29`: symmetric ramp `~}||{…ghh…||}~` — waveform/curve LUT.
- `0x0802ABDC`: ASCII-ordered `@ABC…` run — likely a character/segment table for a display or name entry.
