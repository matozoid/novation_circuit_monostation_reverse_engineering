# Firmware .syx container

Reference file: `monostation-firmware-800.syx` (178,547 bytes, SHA-256 `960db2d0950123fca9aea13cac52844a8da26cc90e047faae5d9a589ec616a71`). Implementation: `tools/fwsyx.py`.

## Messages

All messages are `F0 00 20 29 00 <cmd> <body…> F7`.

| cmd | count | length | meaning |
|-----|-------|--------|---------|
| `0x71` | 1 | 15 | start: product id `00 7f`, then the version as 6 decimal digits, most significant first (`00 00 00 08 00 00` = 800) |
| `0x72` | 4056 | 44 | data block, image offsets 32, 64, … in order |
| `0x73` | 1 | 44 | data block for image offset **0**, sent after all `0x72` blocks |
| `0x76` | 1 | 24 | trailer: `00`, `"Firmware"`, then the image checksum as 8 nibbles, most significant first (`00 02 07 09 0a 05 02 04` = `0x0279A524`) |

Components stops sending after the `0x73` block, so the bootloader never receives the `0x76` trailer from it (`docs/update-protocol.md`). Sending offset 0, which holds the vector table, last most likely means an interrupted flash leaves no valid reset vector, so the bootloader stays in control. This is unverified.

Components also uses `0x71` outside the firmware file. `rebootToBootloader()` sends `F0 00 20 29 00 71 <pid0> <pid1> 00 00 F7` to the running application. `0x70` (`F0 00 20 29 00 70 F7`) is a version inquiry. Its reply carries the bootloader version in bytes 6–10 and the firmware version in bytes 11–15, both as decimal digits.

## Checksum

`checksum = -(sum of the image as little-endian uint32 words) mod 2**32`, so all words plus the checksum sum to 0. The image length is a multiple of 4. The stock image matches: the stored value is `0x0279A524`. Whether the bootloader actually verifies it can't be checked until the bootloader has been dumped, but modified images should always carry a correct value.

Cross-checked on Novation Peak firmware build 352 (Components catalogue id 10, pid `00 7e`), which uses the same container. Its start digits are `000352` and its trailer `0b 08 04 07 0c 09 0f 0f` equals its image's negated word sum. Still unexplained: the constant `00` byte after the `0x76` command.

## Data block encoding

Body = 37 septets (7-bit bytes). Concatenate them MSB-first into a 259-bit integer:

- the top 256 bits are 32 payload bytes, big-endian;
- the low 3 bits are "spare". They equal the top 3 bits of the byte that follows the block in **image order** (offset + 32). For the block at the image end they match the top 3 bits of the image's last byte: Mono 800 ends in `00` fill and gives `0`, Peak 352 ends in `FF` fill and gives `7`. This is presumably the original packer reading 259 bits and advancing 256 through a buffer that continues with the fill. It is not a checksum, and the bootloader most likely ignores it. `encode_firmware` reproduces it so stock files re-encode byte-exactly.

Encoding `0xFF` fill produces `7f` × 37. No per-block address or checksum has been found. Ordering is implicit.

## Decoded image

`tools/syx2bin.py firmware/monostation-firmware-800.syx out.bin` → 129,824 bytes.
`tools/bin2syx.py out.bin out.syx --version 800` reverses it. The output is byte-identical to the stock file, and the same holds for Peak 352 with `--product-id "00 7e"`. Images that aren't a multiple of 32 bytes are padded with `FF` (erased flash), and the padding is included in the checksum.

- ARM Cortex-M Thumb-2. Vector table at offset 0: initial SP `0x10010000` (top of 64 KB CCM RAM at `0x10000000`, which points to an STM32F4-class part), Reset `0x08025e61`.
- **Load address `0x0800C000`.** Thumb function pointers in the image land on `push` prologues for this base (317 hits; the next best candidate base scores 79). The image therefore spans `0x0800C000–0x0802BB20`, and `0x08000000–0x0800BFFF` (48 KB, which would be STM32F4 sectors 0–2) is presumably the bootloader.
- Contains debug format strings (`Pad %d changed state to %d with velocity %d`, `Pot %d value changed to %d`, …), USB descriptor strings (`Focusrite A.E`, `Circuit Mono Station`, `AUDIO Interface`), and default names (`Initial Patch`, `Initial Session`, `Classic Mono`).
