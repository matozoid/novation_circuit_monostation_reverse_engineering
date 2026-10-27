# Hardware & memory map

Most of this comes from the decoded application image (`tools/syx2bin.py`). The *read from a unit* notes come from memory dumps taken with patch memread (#24).

## MCU

**STM32F42x/43x** (ARM Cortex-M4F, Thumb-2), read from a unit with patch memread v803 (#24): `DBGMCU_IDCODE` (`0xE0042000`) = `0x20016419`, i.e. DEV_ID **`0x419`** (STM32F42x/43x) and REV_ID `0x2001` (silicon revision 3). `FLASHSIZE` (`0x1FFF7A22`) = **512 KB**, which fits an STM32F429xE/F439xE-class part. The exact part and package (GPIO A–E only suggests 100 pins) are still open; a teardown photo would settle them.

What the code uses (the F42x also has SAI, LTDC, Ethernet and GPIO F–K, which the code leaves unused):

- Initial SP `0x10010000` = top of the 64 KB **CCM** RAM at `0x10000000` (F40x/41x/42x/43x only).
- **DAC** (`0x40007400`), which fits a synth that drives analogue oscillator/filter CV.
- USB **OTG_HS** (`0x40040000`) and OTG_FS (`0x50000000`).
- The heap runs into **SRAM3** (`0x20020000`+), which exists only on F42x/43x (*Free RAM*).

Option bytes: **RDP level 0** (no read-out protection) and no write-protected sectors. Flash past the app is erased up to `0x0807FFFF`. ST system-ROM bootloader ID (`0x1FFF76DE`) = `0x91`.

## Flash

Base `0x08000000`. STM32F4 sector layout: sectors 0–3 = 16 KB, sector 4 = 64 KB, sectors 5+ = 128 KB.

| Region | Range | Notes |
|--------|-------|-------|
| Bootloader | `0x08000000`–`0x0800BFFF` (48 KB, sectors 0–2) | Not in the firmware file, and an app update never writes it. Dumped from a unit: `firmware/bootloader-740.bin` (#24). Code ends at `0x08007003`; the rest is erased. |
| Application | `0x0800C000`–`0x0802BB20` (129,824 bytes) | The decoded image. Occupies sector 3 (16 KB @ `0x0800C000`), sector 4 (64 KB @ `0x08010000`) and part of sector 5 (128 KB @ `0x08020000`). |
| Free | `0x0802BB20`–`0x0807FFFF` | Erased (`FF`) on a unit (read from a unit). Patch code goes here (`docs/patching.md`). |

The application load base `0x0800C000` is confirmed: the reset vector (`0x08025E61`) disassembles to the standard CMSIS `.data`-copy + `.bss`-zero startup, and 39 vector-table entries share one default handler at `0x08025EA9` (`b .`), which lands exactly on a branch-to-self at this base.

## RAM

From the startup routine's literal pool (`0x08025E94`+):

| Region | Range | Use |
|--------|-------|-----|
| CCM RAM | `0x10000000`–`0x1000FFFF` (64 KB) | Stack only (initial SP `0x10010000`). The rest is unused, see *Free RAM*. |
| SRAM1+2 | `0x20000000`–`0x2001FFFF` (128 KB) | `.data` `0x20000000`–`0x20000C18`, `.bss` `0x20000C18`–`0x200035B8`; heap above, filling the rest. |
| SRAM3 | `0x20020000`–`0x2002FFFF` (64 KB) | Heap end up to `0x20020DFC`, the rest unused (*Free RAM*). |
| Backup SRAM | `0x40024000`–`0x40024FFF` (4 KB) | Byte 0 is the boot flag (`docs/bootloader.md`). |

### Free RAM (#17)

From the code plus the RAM dumps of a running unit (patch memread, `tools/p40dump.py`, `~/ms-dumps/`):

- **SRAM has no free space.**
  - Code loads RAM addresses only up to the `.bss` end `0x200035B8` (capstone scan of `ldr` literals and `movw`/`movt` over every function). Everything above is reached through pointers.
  - Above `.bss` is the newlib-nano heap. `_sbrk` is `FUN_0801eebc`, its pointer is `.data` `0x20000774` (initial value `0x200035B8`), and its limit is `0x200235B8`, which is the heap start + 128 KB rather than the end of SRAM.
  - `operator new` is `FUN_08025ec0`, with 241 call sites, mostly in the constructors `FUN_0800e1f8` and `FUN_0800fb5c`. `free` is called only by stdio, so the heap only grows.
  - The biggest block is 94,056 bytes (`0x16F68`, allocated at `0x0800E672`), at `0x20004078`–`0x2001AFE0` in the dump. The ~70 KB zero run at `0x20004986`–`0x20016511` lies inside it: allocated, just zero at the time of the dump.
  - Walking the heap chunk headers from `0x200035B8` gives 537 chunks that end exactly at `0x20020000`.
  - The heap continues into SRAM3: 678 chunks in total, ending exactly at the live `_sbrk` pointer `0x20020DFC`. That pointer had the same value in two dumps from separate boots (firmware 802 and 803).
- **SRAM3 above the heap is free.** `0x20020DFC`–`0x2002FFFF` holds power-on noise (about 7.95 bits/byte), never written. `_sbrk` would let the heap grow up to `0x200235B8`, but nothing seen so far grows it after boot. Some `new` callers outside the constructors (`FUN_08014c48`, `FUN_08014d88`, `FUN_08015be8`, `FUN_08020ea0`, `FUN_08020fa0`, `FUN_08021128`, `FUN_080214bc`, `FUN_08023324`, `FUN_08025aa0`, `FUN_08025b7c`) aren't traced yet. **`0x200235B8`–`0x2002FFFF` (51,784 bytes) is out of the heap's reach and DMA-accessible.** It isn't cleared at boot either.
- **CCM is free apart from the stack.**
  - Main copies and clears a CCM `.data`/`.bss` pair whose limits are all `0x10000000` (literals at `0x0800C990`–`0x0800C9A0`), so nothing is placed there statically. Main's literal is the only code reference to CCM.
  - In the dump, `0x10000000`–`0x1000FBBF` holds high-entropy power-on noise (about 7.6 bits/byte), which has never been written since power-on. Plausible stack words (`0`, small values, flash/RAM/CCM pointers) start at `0x1000FBC0`.
  - So the deepest stack since power-on is about 1.1 KB. That dump was taken while the unit was idle. Check again after heavy use (sequencer playing, patch and session changes, SysEx) before relying on a margin.
  - DMA can't reach CCM, so it suits CPU-only state.
  - It isn't cleared at boot (power-on contents are random), so patch code must initialise its own state.

**For patch state there are two regions, neither cleared at boot:**

- **CCM from `0x10000000` upward**, for CPU-only state. Keep well clear of the stack at the top: for example, the first 32 KB leaves about 30 KB of margin above the measured 1.1 KB.
- **SRAM3 `0x200235B8`–`0x2002FFFF`**, for buffers that DMA has to reach. It lies past the heap's `_sbrk` limit.

## Peripherals referenced

RCC `0x40023800`, FLASH_IF `0x40023C00`, DMA1/DMA2 `0x40026000/400`, GPIOA–E, TIM1–4/9–11, DAC `0x40007400`, ADC1 `0x40012000`, SPI1/4 + SPI2/3 (I2S), USART1/6, PWR `0x40007000`, SYSCFG `0x40013800`, USB OTG FS `0x50000000`, USB OTG HS `0x40040000`.

(`tools/fwmap.py` ignores `0x4xxxxxxx` values in the bit-band alias range and `0xFxxxxxxx` values: they are instruction halfwords or sign-extended constants, not peripheral pointers.)
