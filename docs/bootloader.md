# Bootloader 740

Static analysis of the bootloader dumped from a unit with patch memread (`firmware/bootloader-740.bin`, 48 KiB at `0x08000000`, SHA-256 `3df50875dd055853c5e154a397bb43a17d0427417273ff8b4cd54b77cb5a12af`). Code and data end at `0x08007003`; the rest is `FF`.

Ghidra: `GHIDRA_HOME=… tools/ghidra/import_firmware.sh firmware/bootloader-740.bin ghidra-project 0x08000000` adds it to the same project as program `bootloader-740.bin`. The post-scripts work on it with `-process bootloader-740.bin`. Function names below are Ghidra's defaults.

## Image

| Address | Content |
|---------|---------|
| `0x08000000` | Vectors: SP `0x10010000` (top of CCM), reset `0x08005F45` |
| `0x080001AC` | Header `BOOT` + BCD `40 07` = 740. The app has `MAIN` at the same offset (`0x0800C1AC`). |
| `0x080061BC` | USB strings: `Circuit Mono Station BL`, `AUDIO Interface`, `AUDIO Config`, `Focusrite A.E` |
| `0x080068E8` | `.data` initialiser → `0x20000000`–`0x2000051C`; `.bss` `0x2000051C`–`0x20002580` |

## Boot decision (`FUN_08000220`, main)

1. Clocks, GPIO, SPI and the external flash, then the **independent watchdog**: `IWDG` (`0x40003000`) PR = 5 (÷128), RLR = 1500, which is about 6 s on the ~32 kHz LSI. The IWDG can't be stopped once started, so the app runs with it too and refreshes it (`0x40003000` is referenced at `0x0800C9BC`, `0x0800E1F0` and `0x0801C478`). **Patch code that blocks for seconds gets the unit reset.**
2. Scans the button matrix 10000 times.
3. If buttons `0x0D`, `0x0F` and `0x1D` (Scales, Note, Velocity) are **not** all held, and backup SRAM byte `0x40024000` is not `2`, it calls the app jump (`FUN_080019fc`). The jump returns when the app is invalid, and the bootloader then stays.
4. Otherwise (or after a failed jump) it starts USB MIDI as `Circuit Mono Station BL` and loops: it polls buttons, handles SysEx and refreshes the IWDG.

### App validity and jump

`FUN_08001974` accepts the app when the 4 bytes at `0x0800C1AC` are `TEST`, `MAIN` or `BOOT`, and the word at `0x0800C000` (initial SP) is not `FFFFFFFF`. **There is no checksum or CRC over the image.** A `BOOT` tag suggests Novation can ship a bootloader updater as an app image; `TEST` is presumably factory test firmware.

`FUN_080019fc`, if the app is valid, does the following:
1. Writes **`1`** to backup SRAM `0x40024000`.
2. Shuts USB down.
3. Disables and clears all NVIC interrupts.
4. Sets `VTOR = 0x0800C000`.
5. Loads MSP from `0x0800C000` and jumps to the reset vector.

There is no system reset, so peripheral state carries over. That includes the running IWDG.

### Backup SRAM flag

| Value | Written by | Meaning to the bootloader |
|-------|-----------|---------------------------|
| `1` | bootloader, just before it jumps to the app | ordinary boot (anything except `2` boots the app) |
| `2` | app, on SysEx `0x71`/`0x60` (`docs/sysex-handlers.md`) | stay in the bootloader for an update |

The bootloader never clears `2` itself. It goes back to `1` on the next jump to the app (after `0x73` or `0x61`; the Play button presumably takes the same path, which isn't traced).

## SysEx (`FUN_08003af8`)

Messages starting `F0 00 20 29 01 61` (Mono Station patch traffic) are ignored. A universal identity request (`F0 7E 7F 06 01 F7` or `F0 7E 00 06 01 F7`) gets a 17-byte identity reply with the bootloader version digits (`FUN_08002bbc`). Everything starting `F0 00 20 29 00` is switched on byte 5:

| Cmd | Action |
|-----|--------|
| `0x61` | Leave the bootloader: jump to the app (if it is valid). |
| `0x70` | Version reply: bootloader version (header at `0x080001AC`), app version (`0x0800C1AC`; `0` if the app is invalid), then constant bytes `30 01`. |
| `0x71` | Requires bytes 6–7 = `00 7F`; **no version or other check**. Unlocks flash and **erases at once** every listed sector inside `0x0800C000`–`0x0807FFFF` (sectors 3–7). It also resets the block index to 1 and the word sum to 0, enters state 2 and acks. |
| `0x72` | In state 2, and only if the message is exactly 44 bytes: unpacks 32 bytes and programs them as 8 words at `0x0800C000 + index × 32`. It adds them to a word sum (`0x20001F00`), increments the index and acks with the 10-byte `0x72` counter. Other lengths are dropped silently. |
| `0x73` | In state 2: index = 0, so this block is written to `0x0800C000` (the vector table). It then locks flash and falls through to `0x61`, i.e. it **boots the new app straight away**. |
| `0x74`, `0x77`, `0x79`, `0x7A` | External-flash write path (session/patch uploads, `docs/session-transport.md`): header, 300-byte blocks, CRC. |
| `0x76` | Only 24-byte messages: something with an external-flash checksum, not the firmware trailer. The `0x76` trailer of a `.syx` (a shorter message) is ignored. |
| `0x78` | External-flash dump: `0x77` header, `0x79` blocks, `0x7A` CRC-32 (`FUN_08004a30`, reflected polynomial `EDB88320`). |

The word sum at `0x20001F00` is written only by `0x71` and `0x72`; nothing reads it. **So the firmware checksum is never verified**, and that is why our flasher (`tools/flash.py`) can skip the trailer.

## Flash driver (object at `0x200003DC`)

| Field | Value |
|-------|-------|
| `+0x08` base / `+0x0C` end | `0x0800C000` / `0x0807FFFF` (512 KB part, matching `FLASHSIZE`) |
| `+0x18` / `+0x1C` | sector table `0x080063B0`: 9 entries, `0x0800C000` (sector 3) … `0x080E0000` (sector 11) |
| `+0x20` erase `0x08004BDD` | bounds-checked against base/end, so sectors 8–11 (`0x08080000`+) are refused |
| `+0x24` program word `0x08004B95` | no bounds check; 32-bit parallelism. Addresses only grow from `0x0800C000`, so the bootloader can't be hit. |
| `+0x2C` unlock `0x08004B55` / `+0x30` lock `0x08004B41` | KEY1/KEY2, `LOCK` bit |

Program errors aren't reported back, and nothing reads the flash back to verify it. Read the image back afterwards (patch memread + `tools/p40dump.py`) when that matters.

## Consequences

- **Maximum app image: `0x0800C000`–`0x0807FFFF` = 475,136 bytes (464 KiB).** Anything past that is programmed into unerased flash and fails silently. `tools/flash.py` and `tools/fwpatch.py` enforce the same limit (`FLASH_END = 0x08080000`).
- The bootloader checks only the `00 7F` bytes in `0x71`, the 44-byte length of each block and the header tag; it **never checks a checksum**. `tools/flash.py`'s checks are the only real ones.
- Recovery is always possible while the bootloader is intact. The button check runs before the app is touched, and an erased or invalid app keeps the unit in the bootloader. An app that is valid but crashes still lets you hold the buttons at power-on.
- An interrupted upload leaves sectors 3–7 erased or partly written. The SP word at `0x0800C000` is written last (`0x73`), so it stays `FFFFFFFF`, the app counts as invalid, and the unit stays in the bootloader. Reflash to recover.
