# Hardware & memory map

Most of this comes from the decoded application image (`tools/syx2bin.py`). The *read from a unit* notes come from memory dumps taken with patch memread (#24).

## MCU

STM32F405/407-class (ARM Cortex-M4F, Thumb-2), almost certainly an **STM32F405** in a 100-pin package. Evidence:

- Initial SP `0x10010000` = top of a 64 KB **CCM** RAM block at `0x10000000`. CCM exists only on STM32F40x/41x/42x/43x, not on F401/F411/F446.
- **DAC** referenced (`0x40007400`), which rules out F401/F411 (no DAC) and fits a synth that drives analogue oscillator/filter CV.
- No **SAI** (`0x40015800`) → F40x/41x, not F42x/43x/44x.
- No **Ethernet** (`0x40028000`) references → F405/F415 rather than F407/F417 (an F407 with ETH unused is still possible).
- USB **OTG_HS** register block (`0x40040000`) present in addition to OTG_FS (`0x50000000`), consistent with F405/407.
- GPIO ports A–E referenced (`0x40020000`–`0x40021000`), none above E → 100-pin part (LQFP100), so **STM32F405VG** is the leading candidate.

Read from a unit with patch memread (#24): `FLASHSIZE` (`0x1FFF7A22`) = **512 KB**. That would make it a 512 KB variant (STM32F405xE-class) rather than the 1 MB F405VG, unless the size register understates the die. Flash past the app is erased up to `0x0807FFFF`. Option bytes: **RDP level 0** (no read-out protection) and no write-protected sectors. Still open: the package and the silicon revision (`DBGMCU_IDCODE` `0xE0042000`, which the patch's range table doesn't cover).

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
| CCM RAM | `0x10000000`–`0x1000FFFF` (64 KB) | Stack (initial SP `0x10010000`), likely fast audio/DSP data. |
| SRAM1+2 | `0x20000000`–`0x2001FFFF` (128 KB) | `.data` `0x20000000`–`0x20000C18`, `.bss` `0x20000C18`–`0x200035B8`; heap above. |
| Backup SRAM | `0x40024000`–`0x40024FFF` (4 KB) | Referenced (7×); candidate for persisted global state. |

## Peripherals referenced

RCC `0x40023800`, FLASH_IF `0x40023C00`, DMA1/DMA2 `0x40026000/400`, GPIOA–E, TIM1–4/9–11, DAC `0x40007400`, ADC1 `0x40012000`, SPI1/4 + SPI2/3 (I2S), USART1/6, PWR `0x40007000`, SYSCFG `0x40013800`, USB OTG FS `0x50000000`, USB OTG HS `0x40040000`.

(`0x4xxxxxxx` values in the bit-band alias range and `0xFxxxxxxx` "literals" in the earlier scans are Thumb instruction halfwords / sign-extended constants, not peripheral pointers — ignored.)
