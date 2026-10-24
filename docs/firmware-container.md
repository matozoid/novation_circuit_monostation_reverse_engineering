# Firmware .syx container

Reference file: `monostation-firmware-800.syx` (178,547 bytes, SHA-256 `960db2d0950123fca9aea13cac52844a8da26cc90e047faae5d9a589ec616a71`). Implementation: `tools/fwsyx.py`.

## Messages

All messages are `F0 00 20 29 00 <cmd> <body…> F7`.

| cmd | count | length | meaning |
|-----|-------|--------|---------|
| `0x71` | 1 | 15 | start; body `00 7f 00 00 00 08 00 00` — not yet decoded (#6) |
| `0x72` | 4056 | 44 | data block, image offsets 32, 64, … in order |
| `0x73` | 1 | 44 | data block for image offset **0**, sent after all `0x72` blocks |
| `0x76` | 1 | 24 | trailer: `00` `"Firmware"` `00` `02 07 09 0a 05 02 04` — not yet decoded (#6) |

Sending offset 0, which holds the vector table, last most likely means an interrupted flash leaves no valid reset vector, so the bootloader stays in control. This is unverified.

## Data block encoding

Body = 37 septets (7-bit bytes). Concatenate them MSB-first into a 259-bit integer:

- the top 256 bits are 32 payload bytes, big-endian;
- the low 3 bits are "spare". In every block they equal the top 3 bits of the next block's first payload byte, in transmission order, and they are `0` for the final block. This looks like an encoder artifact (it reads 259 bits and advances 256), not a checksum.

Encoding `0xFF` fill produces `7f` × 37. No per-block address or checksum has been found. Ordering is implicit.

## Decoded image

`tools/syx2bin.py firmware/monostation-firmware-800.syx out.bin` → 129,824 bytes.

- ARM Cortex-M Thumb-2. Vector table at offset 0: initial SP `0x10010000` (top of 64 KB CCM RAM at `0x10000000`, which points to an STM32F4-class part), Reset `0x08025e61`.
- **Load address `0x0800C000`.** Thumb function pointers in the image land on `push` prologues for this base (317 hits; the next best candidate base scores 79). The image therefore spans `0x0800C000–0x0802BB20`, and `0x08000000–0x0800BFFF` (48 KB, which would be STM32F4 sectors 0–2) is presumably the bootloader.
- Contains debug format strings (`Pad %d changed state to %d with velocity %d`, `Pot %d value changed to %d`, …), USB descriptor strings (`Focusrite A.E`, `Circuit Mono Station`, `AUDIO Interface`), and default names (`Initial Patch`, `Initial Session`, `Classic Mono`).
