# Firmware subsystem map

Addresses are from a **static** scan of the decoded application image (`firmware/monostation-firmware-800.bin`, load base `0x0800C000`): the vector table, word-aligned peripheral-register literals, debug format strings and their literal-pool cross-references, plus spot Thumb-2 disassembly. Function boundaries are approximate until Ghidra auto-analysis (#9) runs; entries below are handler entry points or the tightest xref cluster for each subsystem. See `docs/hardware.md` for the memory map and MCU.

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
| TIM5 | `0x0801F545` | 32-bit timer (sequencer/tempo clock) |
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
Serial MIDI in/out. (USART6 `0x40011400` is referenced once in init; role unconfirmed.)

### UI input (pads / buttons / pots / sliders / encoders) — dispatcher `0x08017930`–`0x080179D8`
This tight cluster emits the debug format strings and is the input-event dispatcher:

| Site | String (`0x08028…`) |
|------|---------------------|
| `0x08017930` | `Slider %d moved to %d` |
| `0x08017954` | `Continuous pot %d moved with delta %d` |
| `0x08017978` | `Pot %d value changed to %d` |
| `0x080179A0` | `Button %d changed state to %d` |
| `0x080179D8` | `Pad %d changed state to %d with velocity %d` |

Hardware: GPIOA–E matrix scan for pads/buttons (12 refs GPIOE, 11 GPIOC, 10 GPIOD), ADC1 (`0x40012000`) for pots/sliders, and EXTI15_10 (ISR `0x08023A79`) for asynchronous edges (encoder detents / external sync input).

### UI output (LEDs / RGB pads) — SPI
Driven over SPI (SPI4 `0x40013400` 3 refs, SPI1 `0x40013000` 1 ref) to LED/pad drivers; TIM1/TIM8 PWM may set brightness. Exact SPI-port → device split unconfirmed (SPI4 vs SPI1, and vs the audio DAC below).

### Analogue engine (CV / gate out) — DAC `0x40007400`, TIM6_DAC ISR `0x08023B99`
Internal 2-channel DAC streamed at audio/control rate via TIM6 trigger + DMA. The synth needs many CV targets (osc1/osc2 pitch, pulse width, filter cutoff, VCA, distortion, aux CV), so an **external** multi-channel DAC over SPI/I2S is likely in addition to the internal DAC; SPI2/I2S2 (`0x40003800`) and SPI3/I2S3 (`0x40003C00`) are referenced. DMA1/DMA2 streams feed these converters.

### ADC — ADC1 `0x40012000` (+ DMA)
Pot/slider reads and CV/gate inputs.

### Timers
TIM5 (32-bit, ISR `0x0801F545`) is the likely sequencer/tempo master clock; TIM6 is the DAC sample clock; TIM1/TIM8/TIM9/TIM10/TIM11 serve PWM/LED/auxiliary timing.

### Persistent storage — FLASH_IF `0x40023C00`, Backup SRAM `0x40024000`
On-chip flash program/erase (FLASH_IF, 2 refs at `0x0801A930`, `0x08028890`) saves patches/sessions; Backup SRAM (7 refs) holds state across power cycles. Default blobs `"Initial Patch"`, `"Classic Mono"`, `"Initial Session"`, `"User Session"` are referenced from `0x0800EEF8`–`0x0801B5C0` (patch/session init + storage layer).

## Still to isolate (needs Ghidra function graph — #9/#10 follow-up)
- **Sequencer engine**: no dedicated peripheral signature; driven by a timer ISR (TIM5?). Find via the tempo/step state it mutates.
- **SysEx parser/dispatcher**: shared by USB-MIDI and USART paths; locate the `F0 00 20 29` header match and the per-command (`0x00` patch replace, firmware `0x71`/`0x72`/`0x73`/`0x76`) jump.
- **Patch/session (de)serialiser**: the 136-byte patch and `.monostationsession` layout handlers (#12/#13) live in the storage region above.
- **SPI device assignment**: which SPI port drives LEDs vs an external DAC.

## Ghidra auto-analysis (#9)

`tools/ghidra/import_firmware.sh` imports the image, runs `MapMemory.java` (memory map + reset-handler seed) and leaves the DB in `ghidra-project/` (git-ignored). `ExportMap.java` writes the full function listing to `docs/ghidra-functions.txt`; `DecompileFunc.java <addr>…` prints decompiled C for investigation. (Ghidra 12.x headless has no Jython, so these are Java GhidraScripts, not Python.)

Auto-analysis finds **467 functions**. Confirmed so far:

| Function | Role |
|----------|------|
| `FUN_08025E60` | Reset handler (CMSIS startup) |
| `FUN_08025F3C` | `memset(dst, val, len)` |
| `FUN_08026752` | `memcpy(dst, src, len)` |
| `FUN_08018CA4` | **Session initialiser** — builds the "Initial Session" struct in RAM out to offset `0x155DE` (≈87.9 KB, matching the 90,112-byte `.monostationsession`); drives #13. |
| `FUN_0800E1F8`, `FUN_0801B4E0` | reference the "Initial Patch" default — patch load/init; drive #12. |
| `FUN_0800C2D0` (7142 B), `FUN_0800FB5C` (6288 B) | largest routines — likely the main loop / scan or the sequencer; not yet identified. |

Note: the UI debug-string pool words (`0x08017930`+) sit in a code region auto-analysis did not promote to a function (reached by tail call); revisit by disassembling from the enclosing block.

## Data tables noted
- `0x08027F29`: symmetric ramp `~}||{…ghh…||}~` — waveform/curve LUT.
- `0x0802ABDC`: ASCII-ordered `@ABC…` run — likely a character/segment table for a display or name entry.
