# Application SysEx handlers (firmware 800)

What the running application does with incoming SysEx, from static analysis of firmware 800. The dispatcher is `FUN_080202a0`. The host side of the update protocol is in `docs/update-protocol.md`.

The dispatcher matches the start of each message against four prefixes, in this order:

| prefix | literal at | handled as |
|--------|-----------|------------|
| `F0 00 20 29 01 61` | `0x08029413` | Mono Station commands (patch, session and test traffic), dispatched on byte 6 |
| `F0 00 20 29 00` | `0x08029EE2` | product-independent commands: reboot and version, dispatched on byte 5 |
| `F0 7E 7F 06 01 F7` or `F0 7E 00 06 01 F7` | `0x08029EE7`, `0x08029EDC` | MIDI Identity Request; answered by `FUN_0801ee24` |
| `F0 4D 44 4C 49 54 48 00` ("MDLITH") | `0x08029ED4` | pad velocity-curve upload (factory tuning): length = byte 8 × 128 + byte 9, payload from byte 13 copied into the 256-byte curve at `0x200000E8`; one branch fills it with an identity ramp instead. Handler in `sysex_dispatch` at `0x080209D8` |

## Image header and version

Each image has a header at offset `0x1AC` from its base: a 4-byte tag, then the version as a little-endian 16-bit BCD number.
- **Application:** the header is at `0x0800C1AC`, `4D 41 49 4E 00 08`, which is tag `MAIN` and version `0x0800` = 800.
- **Bootloader:** the header is at `0x080001AC`, inside the protected region that isn't in the update file. The app reads the bootloader version from there.

`FUN_0801ac7c(n)` converts the BCD to decimal. With `n = 2` it reads the bootloader header, otherwise the application header.

`FUN_0801ac30` treats the application as valid when both of these hold:
- its tag is `TEST`, `MAIN` or `BOOT` (strings at `0x08028F5C`);
- the word at `0x0800C000`, the initial stack pointer, isn't `FFFFFFFF`.

When it isn't valid, every version field reports 0. The bootloader most likely runs the same check before it starts the app, but that code is not in the file.

**For patched images:** the version that Components sees is bytes `0x1B0`–`0x1B1` of the decoded image. The six digits in the container's `0x71` start message are separate. To make Components offer a restore to stock (`docs/update-protocol.md` *Recovery*), a modified image can report a version below 800 by changing those two bytes. Keep the `MAIN` tag, or the app reports version 0 and refuses `0x61`.

## Product-independent commands (`F0 00 20 29 00 …`)

### `0x70`: version inquiry
The request is `F0 00 20 29 00 70 F7`; the app checks only byte 5. The 19-byte reply is:

```
F0 00 20 29 00 70  b b b b b  f f f f f  30 01 F7
```

- `b`: the bootloader version as 5 decimal digits, most significant first;
- `f`: the application version in the same form, or all 0 if the app is invalid;
- `30 01`: constants with unknown meaning.

For firmware 800 the `f` digits are `00 00 08 00 00`. This matches what Components parses (`docs/update-protocol.md`).

### `0x71`: reboot to bootloader
The app accepts the message when byte 6 is `00` and byte 7 is `7F`, the product id. It doesn't look at the version digits or the length, so the 15-byte start message and the 11-byte short form both work. **Command `0x60` does the same with no further checks.**

The handover:
1. `FUN_0801a5ac`, `FUN_0801a830`: stop the USB endpoint, then set the soft-disconnect bit in the OTG device control register (`DCTL.SDIS`), so the host sees the port go away.
2. `FUN_0801e1ec(1000000)`: busy-wait for 1,000,000 SysTick wraps.
3. Write **`2` to the first byte of backup SRAM (`0x40024000`)**.
4. `FUN_0801abec(0x08000000)`:
   - disable and clear all NVIC interrupts;
   - set `VTOR` to `0x08000000`;
   - load the MSP from `0x08000000`;
   - jump to the bootloader's reset vector.

There is no system reset. Backup SRAM survives the jump, and the bootloader reads the `2` as "stay in update mode" (`docs/bootloader.md`). Peripheral state isn't reset either, apart from USB and the NVIC.

### `0x61`: restart the application
This does the same as `0x71`, with two differences:
- it writes `1` to backup SRAM;
- it calls `FUN_08024940`, which does further peripheral shutdown, then jumps to the application's own vectors at `0x0800C000`.

It runs only when `FUN_0801ac30` reports a valid app. Otherwise it returns with USB already disconnected.

### Backup SRAM flag
The only other write to `0x40024000` is in the main loop `FUN_0801c208`, which writes `0` once. So the flag is a one-shot message to whatever runs next. The meaning of the values to the bootloader is inferred: `2` = enter update mode, `1` = boot the app. A patched image that keeps this path (`FUN_080202a0` → `FUN_0801abec`) still answers Components' reboot.

## MIDI Identity Reply
`FUN_0801ee24` answers the Identity Request with a 17-byte reply:

```
F0 7E 00 06 02  00 20 29  7F 00  00 00  v v v v  F7
```

- `00 20 29`: manufacturer;
- `7F 00`: family;
- `00 00`: member;
- `v`: the application version as 4 decimal digits (`00 08 00 00` for 800).

## Mono Station commands (`F0 00 20 29 01 61 …`)
These are dispatched on byte 6. The patch and session traffic (`docs/sysex-spec.md`, `docs/session-transport.md`) goes through here. Byte 6 = `'p'` (`0x70`) selects a family of low-level commands, dispatched on byte 7:

| byte 7 | effect |
|--------|--------|
| `0x40` | external SPI flash access, see below |
| `0x00`, `0x01` | write 1 or 3 bytes into a parameter buffer via the index table at `DAT_08020584`, then mark it dirty |
| `0x10` | read a 16-bit value from one of three RAM tables (`0x20000EDA`, `0x20000ED2`, `0x20001784`) by index; 14-byte reply |
| `0x50` | byte 8 = 0 or 1 calls `FUN_0801d0d4` or `FUN_080222ac`; 10-byte reply |
| `0x52`, `0x53`, `0x54`, `0x5A` | write into RAM state (LED or display buffers, likely); `0x5A` has sub-ops 0–5 |
| `0x60` | store byte 8 (0 or 1) at `0x20002EC0`; 10-byte reply |
| `0x70` / `0x71` | set / clear GPIO PE0 (`GPIOE_BSRR` at `0x40021018` / `0x4002101A`) |

These look like factory-test commands. Only `0x40` has been decoded fully.

### `p 0x40`: external flash read, write and erase
Request layout:
- bytes 0–7: `F0 00 20 29 01 61 70 40`;
- byte 8: must be `00`;
- byte 9: the operation;
- bytes 10–17: the address as 8 nibbles, most significant first;
- bytes 18 onward: the data, for writes only.

The reply echoes the first 10 bytes of the request.

| op | action | reply |
|----|--------|-------|
| `0` | erase the 4 KiB sector that holds the address (SPI command `0x20`) | 11 bytes |
| `1` | program the data, sent as nibble pairs, high nibble first (SPI page program `0x02`) | 11 bytes |
| `2` | read 256 bytes (SPI fast read `0x0B`) | 523 bytes: 10-byte header, 512 nibbles high-first, `F7` |

The addresses are in the external SPI NOR flash that holds patches and sessions, the same address space as the session dump's storage start addresses (patches `0x2000`, sessions `0x12000`). **This is not MCU flash, so it can't dump the bootloader on stock firmware.** Patch `patches/memread` adds MCU memory to op 2 (`docs/bootloader.md`). It can dump or restore the whole patch and session store, and op `0` erases user data, so treat writes and erases as destructive.

**Op 2 on hardware (2026-10-25):** reads at `0x0`, `0x1000`, `0x2000`, `0xA000`, `0x11F00`, `0x12000`, `0x2E7F00`, `0x2E8000`, `0x400000` and `0x7FFF00` match the bootloader's `0x78` dumps byte for byte. From **`0x800000`** up, the reply is still 523 bytes but doesn't read flash. It changes from one request to the next and echoes the previous reply's nibbles (`0F`, `F7`), so the app sends its stale reply buffer. The bound check isn't traced yet. Ops 0 and 1 are untested.

## Not yet known
- The meaning of `30 01` in the `0x70` reply. The bootloader writes the same two constants (`docs/bootloader.md`).
- The meaning of the other `'p'` commands.
