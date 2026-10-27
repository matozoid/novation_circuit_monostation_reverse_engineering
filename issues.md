# Issues

Goal: reverse engineer the Circuit Mono Station firmware and add new features to it. This file tracks tooling and research; feature work is in GitHub issues (*New features* below).

Established facts:

- Components 1.72.5 bundles the latest Mono Station firmware as `resources/firmwares/monostation-firmware-800.syx` (178,547 bytes) inside `app.asar` (Windows installer → Squirrel `Components-1.72.5-full.nupkg` → `lib/net45/resources/app.asar`). The same file also exists at `resources/mididata/`. Its constants: `MONOSTATION_LATEST_FIRMWARE_VERSION: 800`, upstream URL `https://s3-eu-west-1.amazonaws.com/circuit-content/f53741d5f0a40a808d29ac4f137222a4e0974ce3/monostation-firmware-800.syx`.
- The firmware `.syx` is 4,059 SysEx messages with header `F0 00 20 29 00 <cmd>`: one `0x71` start (15 bytes), 4,056 `0x72` data blocks (44 bytes), one `0x73` last data block (44 bytes) and one `0x76` trailer (24 bytes). Format: `docs/firmware-container.md`.
- Components also ships `session_32.monostationsession` (90,112 bytes), the init session (`docs/session-format.md`).
- Older builds are listed in the Components firmware catalogue (`components.novationmusic.com/api/v2/firmwares/<id>/file`), see `docs/firmware-versions.md`.

---

## Tooling & groundwork

### #1 Set up repo structure and version control — DONE
`tools/`, `docs/`, `tests/`, `patches/`, `overlays/`, `src/`; `firmware/` and `build/` are git-ignored. Tooling is Python 3, stdlib only (recorded in CLAUDE.md).

### #2 Reproducible extraction script for vendor inputs
Write a script that pulls `app.asar` out of both the Windows nupkg and the macOS `.dmg`, then extracts every Mono Station asset (firmware, session, init patch, PNG manuals if useful) into `firmware/`. Record the SHA-256 of each file. It must never execute vendor code.
- **Done when:** running the script from a clean checkout reproduces `monostation-firmware-800.syx` with a known hash.

### #3 Archive all historical Mono Station firmware versions — PARTLY DONE (list and features; only 1.2 archived)
`docs/firmware-versions.md` lists the three versions (1.0 launch, 1.1 build 745, 1.2 build 800) with the features each added and the hashes of the 1.2 files. The catalogue has two Mono Station entries: id 8 (build 800, identical to the bundled file) and id 9 (build 745, `url: null`; `/firmwares/9/file` returns HTTP 500).
- **Still open:** a 1.0 or 1.1 binary, and the 1.0 build number. Look in older Components releases (extracted statically), the S3 `circuit-content` bucket or the community.

### #4 Reverse the firmware-update protocol from Components JS — DONE
`docs/update-protocol.md`:
- Components sends the 15-byte `0x71` start message to the running application, then waits up to 8 s for the bootloader port (`Circuit Mono Station BL`).
- Flow control is device-driven: each packet waits up to 10 s for a `F0 00 20 29 00 72 a b c F7` ack with an increasing counter.
- The loop stops after the `0x73` (offset 0) packet, so the `0x76` checksum trailer is never sent.
- Success: the unit reboots and reports the new version via `0x70`. Status codes 124/126 mean failure.
- Manual entry (#29), real timing (#16) and bootloader checks (#25) are covered in their issues.

---

## Firmware container format

### #5 Decode the `0x72`/`0x73` data block encoding — DONE
37 septets per block are an MSB-first bitstream: 32 payload bytes plus 3 look-ahead bits (the top bits of the next block in image order, not a checksum). There is no address field. `0x73` holds image offset 0 and is sent last. The decoded image is 129,824 bytes of Cortex-M code, loading at `0x0800C000`. See `docs/firmware-container.md`, `tools/fwsyx.py`, `tools/syx2bin.py` and `tests/test_fwsyx.py`.

### #6 Decode the `0x71` start and `0x76` trailer messages — DONE
`0x71` = product id `00 7f` followed by the version as 6 decimal digits. `0x76` = `00`, `"Firmware"`, then 8 checksum nibbles. The checksum is the negated sum of the image's 32-bit little-endian words, confirmed on stock 800 and on Peak 352. The bootloader doesn't verify it (#25). See `docs/firmware-container.md` and `checksum` / `build_start` / `build_trailer` in `tools/fwsyx.py`.

### #7 Round-trip tool: bin → syx — DONE
`tools/bin2syx.py` (`encode_firmware` in `tools/fwsyx.py`) re-encodes Mono Station 800 and Peak 352 byte-for-byte. Re-run the round-trip test when an older build turns up (#3).

---

## Hardware & static analysis

### #8 Identify the MCU and memory map — DONE
Read from a unit with memread v803: **STM32F42x/43x**, `DBGMCU_IDCODE` DEV_ID `0x419`, REV_ID `0x2001`, 512 KB `FLASHSIZE`, 192 KB SRAM (SRAM3 in use). Load base `0x0800C000`. Memory map (flash sectors, CCM/SRAM/backup-SRAM, peripherals) in `docs/hardware.md`. Not known: the exact part and package (needs a teardown photo).

### #9 Load firmware into Ghidra with correct memory map — DONE
`tools/ghidra/import_firmware.sh` (headless `analyzeHeadless`, processor `ARM:LE:32:Cortex`, base `0x0800C000`) plus the pre-script `MapMemory.java`, which creates the bootloader/CCM/SRAM/peripheral blocks and seeds disassembly at the reset handler. `ExportMap.java` dumps the function listing (`docs/ghidra-functions.txt`, 828 functions); `DecompileFunc.java <addr>` prints decompiled C. The Ghidra DB is git-ignored (`ghidra-project/`); only the scripts and listing are committed. Scripts are Java, because Ghidra 12.x headless has no bundled Jython. Labelling continues in `src/symbols.tsv` (#43).

### #10 Map firmware subsystems — DONE
`docs/firmware-map.md` locates each subsystem by interrupt vector, peripheral-register literal and debug-string xref: USB, DIN MIDI, UI input and output, the analogue control engine, ADC, timers and persistent storage. `tools/fwmap.py` reproduces the scan. Function-level detail is in `docs/source-map.md`, `docs/sequencer.md` and `docs/sysex-handlers.md`.

### #11 Diff firmware versions to locate features
Use release notes plus binary diffs between versions (#3) to pin down code tied to known features (for example the step automation added in v1.1). This is a cheap way to anchor subsystem identification.
- **Depends on:** #3, #9

---

## SysEx / MIDI protocol

### #12 Complete the patch SysEx spec — MOSTLY DONE
`docs/sysex-spec.md` is the repo's spec and supersedes the community PDF. The Mono Station has one patch format, the **136-byte** message (`F0 00 20 29 01 61 …`), used on the wire, in `.monostationpack` files (a ZIP holding `patches.syx`, a 64 × 136 = 8704-byte bank) and in firmware memory. `tools/patchbank.py` reads packs and banks (`tests/test_patchbank.py`).
- Device id `01 61` (Circuit is `01 60`). Message type: `0` = current patch, `1` = slot write, `0x40` = request.
- Name@9, category@25 / genre@26 (#28), params@41.
- CC(8) params are split over two bytes: `v = byte[off] << 1 | byte[off+1] >> 6` (the firmware's patch store `FUN_080176e8`). Signed ones subtract the centre from the firmware table at `0x0802A62A`: 128 for coarse and pitch/cutoff mod depths, 100 for fine. This matches the firmware `Initial Patch`, the MIDI PDF defaults and 62 YBC editor screenshots. `tests/test_patchbank.py` checks all 74 params against the firmware table and Novation's MIDI parameter PDF.
- Byte 27 is the envelope trigger (NRPN 0:73): 1 = retrigger, 0 = legato, the panel's Shift + PWM. Bytes 28–40 have no param-table record and are zero in every known patch. 49/58 are osc 1/2 glide, 60 is patch level.
- On hardware: the `0x40` request answers only `40 00`, with the current working patch as a type-0 message, so it can't dump slots (use the bootloader's `0x78`). A type-1 slot write stores the whole 512-byte slot; only the first 126 bytes are read back.
- Components' `resources/mididata/factory-patches.syx` is **Circuit** (`01 60`), not Mono Station.
- **Still open:** coarse tune's unit (range −128…127; fine is −100…100).

### #26 Investigate what the 154-byte patch added over the 122-byte legacy — DONE (not Mono Station)
Neither format belongs to the Mono Station: Components keeps them in its Bass Station II modules (`frontend/constants/bass-station-ii/content-transfer`, `frontend/utils/bass-station-ii/convert-legacy-patch`, `frontend/constants/bass-station-ii/afx-overlay-midi-config`; product id `00 33`). The legacy 122-byte form is the 154-byte layout without the name. The material is in `docs/bass-station-ii.md`, `tools/bassstation2_patch.py`, `tools/bassstation2_params.tsv` and `tests/test_bassstation2_patch.py`.

### #28 Enumerate patch category and genre values — DONE
Components' Circuit-family librarian supplies the tables (`patchbank.CATEGORIES`/`GENRES`, `docs/sysex-spec.md`). Category: 0 None, 1 Arp, 2 Bass, 3 Bell, 4 Classic, 5 Drum, 6 Keyboard, 7 Lead, 8 Movement, 9 Pad, 10 Poly, 11 SFX, 12 String, 13 User, 14 Voc/Tune. Genre: 0 None, 1 Classic, 2 D&B/Breaks, 3 House, 4 Industrial, 5 Jazz, 6 R&B/HHop, 7 Rock/Pop, 8 Techno, 9 Dubstep. Real packs treat the bytes as a loose user tag. Not checked: whether the firmware itself reads them (a quick look found no category strings).

### #13 Document session format — MOSTLY DONE
`docs/session-format.md`, `tools/session.py`, `tests/test_session.py`. The layout comes from the firmware initialiser `FUN_08018CA4` and is checked against 48 real YBC sessions: header, globals, three tracks of 16/8/8 patterns of 140 bytes, automation lanes (lane → parameter map from the firmware tables `0x08027588`, `0x0802775C` and `0x0802A62A`), step bytes, pattern footers and **one** 126-byte patch at `0x15F5C`. The 64 × 512-byte patch store `MS_ALL_PATCHES` is separate from the session.
- **Still open:** `0x42` (third field of the tempo/swing observable), `0x155D8`/`0x155D9`, `0x155DB`/`0x155DC`, and what ids `0x4C`/`0x4D` control.

### #27 Document the session SysEx transport protocol — DONE
`docs/session-transport.md`, `tools/sessionsyx.py`, `tests/test_sessionsyx.py`. `0x77` header (storage start address and length), `0x79` 300-byte blocks (293 septets in groups of 8 → 256 bytes), `0x7A` CRC-32 (zlib) trailer. A full dump holds 33 × 90,112-byte images: 32 `USER` sessions plus the `INIT` image. Both YBC dumps re-encode byte-exact. A dump is requested with `F0 00 20 29 00 78 <start> <length> F7`, served only by the bootloader. Uploads are flow-controlled (`0x7B`/`0x75` block requests, then `0x7A`).
- **Still open:** whether the device accepts other start/length values, such as a single session.

### #14 Document full MIDI implementation (CC/NRPN)
Cross-check the official MIDI parameter guide against the firmware's CC/NRPN dispatch tables. List parameters that are reachable by SysEx but not by CC; these are candidates for GitHub #2.
- **Depends on:** #10
- **System Settings record (hardware):** the MIDI channels, clock out rate, pot pickup and the six Note/CC/Clock Rx/Tx switches are stored at external flash `0x1000` (24 bytes) and saved only at power-off (`docs/session-transport.md` *Settings record*). Open: byte `0x00` (`08`), bit 1 of `0x08`, byte `0x14` (`FF`), and the Osc 2 channel at `0x10`, which is inferred but not yet changed on hardware.

---

## Safe flashing

### #15 Bootloader recovery procedure — DONE
The bootloader lives in `0x08000000`–`0x0800BFFF`, which application updates never erase (#25). Stock 800 was restored with `tools/flash.py` from the manual bootloader entry after running modified builds (#18). Procedure: `docs/update-protocol.md` *Entering the bootloader by hand* and *Recovery*.

### #29 Find the manual bootloader entry — DONE
Hold **Scales + Note + Velocity** while powering on (user guide v1.2 p. 90). Tried on stock firmware: the grid shows a reduced set of buttons, the `Circuit Mono Station BL` port comes up, and **Play** exits. Osc 1 and Osc 2 show the bootloader and firmware versions as BCD in four groups of 8 pads, high byte first: Osc 1 = `00000111 01000000` (740), Osc 2 = `00001000 00000000` (800). The bootloader reads the buttons itself (`docs/bootloader.md`), so the entry survives a broken app image. Details: `docs/update-protocol.md` *Entering the bootloader by hand*.

### #30 Trace the application's update and version handlers — DONE
`docs/sysex-handlers.md`:
- `0x71` checks only `00 7F` at bytes 6–7; bare `0x60` does the same. Handover: USB soft-disconnect, a delay, `2` → backup SRAM `0x40024000`, then a direct jump to `0x08000000` (no reset).
- `0x70` reports the BCD version from the image header at offset `0x1AC` (`MAIN 00 08`); the bootloader version comes from the same header at `0x080001AC`. On hardware the reply reports bootloader 740, firmware 800.
- `F0 00 20 29 01 61 70 40` reads, writes and erases the external SPI flash (patches and sessions), not MCU flash.

### #16 Command-line firmware flasher — DONE
`tools/flash.py` (tests in `tests/test_flash.py`), `mido` + `python-rtmidi`, Linux and Windows. Bootloader entry is manual only. Before sending it checks the `.syx` structure (message types and order, block count, checksum, product id); after the `0x73` block it polls `0x70` for the expected version. On hardware the bootloader acked all 4,058 packets in 7 s, accepts the same version again and doesn't need the `0x76` trailer.

---

## Patching framework

### #17 Binary patching infrastructure — DONE
`tools/fwpatch.py`, spec in `docs/patching.md`, tests in `tests/test_fwpatch.py`. Patches are assembly (GNU binutils) plus a declarative hook list (`bl`, `b`, `word`, `bytes`, each checking the stock bytes it replaces), linked at `0x0802BB20` up to `0x08080000`. An optional `version` sets the image header and the `.syx` start message. Free RAM for patch state: CCM `0x10000000`+ and SRAM3 `0x200235B8`–`0x2002FFFF` (`docs/hardware.md` *Free RAM*). For larger changes use overlays (#43).
- **Still open:** a stack high-water mark after heavy use.

### #18 Hello-world patch — DONE
`patches/hello/` sets the version to 801 and routes the reset handler's call to main through injected code at `0x0802BB20`. Flashed with `tools/flash.py`: the app reported (740, 801), the unit worked normally, and the bootloader showed the patched version on Osc 2. Stock 800 was then restored. The whole pipeline (#17 build → #16 flash → verify → restore) works.

### #24 Dump the bootloader via a patched application — DONE
The bootloader's `0x78` dump doesn't reach MCU memory, so patch `patches/memread` (version 803) hooks the SPI read call of `'p' 0x40` op 2 (`0x0802066A`) and copies MCU addresses from memory instead. `tools/p40dump.py 0x08000000 0x10000` read the bootloader:
- **Bootloader 740:** header `BOOT` + `40 07` at `0x080001AC`, initial SP `0x10010000`, reset vector `0x08005F45`. Code and data end at `0x08007003`; the rest of the 48 KiB is `FF`. Saved as `firmware/bootloader-740.bin` (git-ignored).
- Option bytes `0x1FFFC000`: RDP `0xAA` = **level 0**, no write protection, BOR off, software watchdog. Do not change RDP via SWD: going from level 1 to 0 mass-erases flash, including the bootloader.
- `FLASHSIZE` = 512 KB; flash past the app is `FF`. OTP is blank. Backup SRAM word 0 = `1`. The 96-bit unique id identifies the unit and stays out of the repo.

### #25 Probe bootloader acceptance checks — DONE (static)
From the dump (`docs/bootloader.md`):
- `0x71` checks only `00 7F`, then erases sectors 3–7 at once.
- `0x72` blocks must be exactly 44 bytes; other lengths are dropped silently.
- No checksum is verified, and the version digits are ignored.
- `0x73` writes the vector block and boots the app right away. The app is accepted when its tag is `TEST`/`MAIN`/`BOOT` and its SP word isn't `FFFFFFFF`.
- Maximum image: up to `0x0807FFFF`.
- Backup SRAM `2` = stay in the bootloader, `1` = set before jumping to the app.
- The bootloader starts a ~6 s IWDG.

Malformed-firmware tests on hardware could still confirm these, but aren't needed.

### #43 Firmware source tree
Turn the image into source that can be edited and rebuilt, so features can be written as code rather than patches. Plan: byte-identical reassemblable assembly first, then split it by subsystem and convert functions to C one at a time. The source is Novation's code, so it is generated locally and never checked in; only the generator and metadata are committed (`docs/source-build.md`).
- *Status:* `tools/fwsrc.py` rebuilds firmware 800 byte for byte from generated assembly. Every instruction is text, and branch, literal, adr, jump-table and pointer targets are labels, so the source is relocatable (`generate --relax`, `fwsrc.py shift`).
- `src/symbols.tsv` names every function (purposes in `docs/source-map.md`, checked by `fwsrc.py coverage`). `src/files.tsv` splits the source into about 100 files named for their content: vectors, header, startup, runtime library objects, rodata, `.data`, and the app code split at linker fills.
- Overlays (`overlays/<name>/`, `generate --overlay`) replace stock functions by name with `.s` or C; the stock body stays as `stock_<name>`. `overlays/randomise` and `overlays/transpose` are the shipped mods.
- **Next:** grow the prototypes in `overlays/include/firmware.h`; convert functions to C.

---

## New features

Feature work is tracked in [GitHub issues](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues). File new requests there. The feature issues below were moved there on 2026-10-27, and GitHub gave them new numbers. Commits, docs and the issues above still use the old numbers.

| Old | Feature | GitHub |
|-----|---------|--------|
| #19 | Patch dump request / bulk backup over SysEx | [#1](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/1) |
| #20 | Expose SysEx-only parameters via CC/NRPN | [#2](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/2) |
| #21 | Additional LFO waveforms / modulation options | [#3](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/3) |
| #22 | Sequencer enhancements | [#4](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/4) |
| #31 | Patch slots 64–127 | [#5](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/5) |
| #32 | Micro-step note timing (off-grid notes, trills, ratchets) | [#6](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/6) |
| #33 | Unquantised recording | [#7](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/7) |
| #34 | Step-time recording (SH-101 style) | [#8](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/8) |
| #35 | Per-step probability and mutation (Launchpad Pro style) | [#9](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/9) |
| #36 | Arpeggiator | [#10](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/10) |
| #37 | Roll / looper performance effect | [#11](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/11) |
| #38 | Decouple the Osc 2 track from Osc 2 | [#12](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/12) |
| #39 | Randomise gate, glide and velocity patterns | [#13](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/13) |
| #40 | Per-step modulation shapes on the mod sequencer | [#14](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/14) |
| #41 | AFX mode / per-key overlays | [#15](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/15) |
| #42 | Fast LFO mode (audio-rate) | [#16](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/16) |
| #44 | Sequencer track routing (Osc 2 follows the Osc 1 track) | [#17](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/17) |
| #45 | Play the keys without interrupting the sequence | [#18](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/18) |
| #46 | Edit a pattern while a chain plays | [#19](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/19) |
| #47 | Flexible pattern chains | [#20](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/20) |
| #48 | Live transpose of the sequence | [#21](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/21) |
| #49 | MIDI-in notes outside the scale | [#22](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/22) |
| #50 | Pattern select and program change out over MIDI | [#23](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/23) |
| #51 | Second LFO | [#24](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/24) |
| #52 | Static pulse width | [#25](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/25) |
| #53 | Record patch changes into the sequencer | [#26](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/26) |
| #54 | 128 patches from the panel | [#27](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues/27) |
