# Issues

Goal: reverse engineer the Circuit Mono Station firmware and add new features to it.

Facts already established (2026-10-24), referenced by the issues below:

- Components 1.72.5 bundles the latest Mono Station firmware as `resources/firmwares/monostation-firmware-800.syx` (178,547 bytes) inside `app.asar` (Windows installer → Squirrel `Components-1.72.5-full.nupkg` → `lib/net45/resources/app.asar`). The same file also exists at `resources/mididata/`. Its constants: `MONOSTATION_LATEST_FIRMWARE_VERSION: 800`, upstream URL `https://s3-eu-west-1.amazonaws.com/circuit-content/f53741d5f0a40a808d29ac4f137222a4e0974ce3/monostation-firmware-800.syx`.
- The firmware `.syx` is 4,059 SysEx messages, all using header `F0 00 20 29 00 <cmd>`:
  - `0x71` × 1 (15 bytes): start/erase
  - `0x72` × 4,056 (44 bytes): data blocks
  - `0x73` × 1 (44 bytes): last data block
  - `0x76` × 1 (24 bytes): trailer containing ASCII `"Firmware"` followed by `02 07 09 0a 05 02 04`
  - All body bytes are 7-bit. Identical consecutive blocks appear, which suggests the payload is packed but not encrypted.
- Components also ships `session_32.monostationsession` (90,112 bytes, the init session). Its JS (`chunkMonostationPatches` / `chunkMonostationSessions`) reads 64 patches at a 512-byte stride with 340 bytes used per patch, and 33 sessions of 90,112 bytes each. The in-session patch format is therefore larger than the 136-byte single-patch SysEx in the PDF.
- Older Mono Station firmware builds are listed in the Components firmware catalogue (`components.novationmusic.com/api/v2/firmwares/<id>/file`; for example id 9 is v1.1 build 745).

---

## Tooling & groundwork

### #1 Set up repo structure and version control
`git init`. Add directories for `tools/`, `docs/`, `firmware/` (git-ignored for vendor blobs) and `patches/`. Pick the implementation language for tooling and record it in CLAUDE.md.
- **Done when:** repo is initialised and vendor binaries are git-ignored.

### #2 Reproducible extraction script for vendor inputs
Write a script that pulls `app.asar` out of both the Windows nupkg and the macOS `.dmg`, then extracts every Mono Station asset (firmware, session, init patch, PNG manuals if useful) into `firmware/`. Record the SHA-256 of each file. It must never execute vendor code.
- **Done when:** running the script from a clean checkout reproduces `monostation-firmware-800.syx` with a known hash.

### #3 Archive all historical Mono Station firmware versions
Parse the firmware catalogue embedded in `app.asar` and list every `product: "monostation"` entry (id, build, version, release notes). Download each `.syx`. Older builds are useful for diffing and for rollback.
- **Done when:** a `docs/firmware-versions.md` table exists and the blobs sit in `firmware/` with hashes.

Found so far: the catalogue has two Mono Station entries. Id 8 is build 800 (v1.2), identical to the bundled file. Id 9 is build 745 (v1.1) with `url: null`, and `/firmwares/9/file` returns HTTP 500. An older build would have to come from another source: older Components versions, the S3 `circuit-content` bucket, or the community.

### #4 Reverse the firmware-update protocol from Components JS
Find the Mono Station updater in `app.asar`. Relevant markers: `WAITING_FOR_PACKET_REQUEST`, `FIRMWARE_TRANSFER_IN_PROGRESS`, `deviceIsInBootloader`, `sendToMainApplication`, `maxWaitForResetTimeout`. Document:
- how the device enters the bootloader
- whether the host streams messages or the device requests packets (flow control)
- timing requirements
- how success and failure are reported
- **Done when:** `docs/update-protocol.md` describes the full exchange, enough to re-implement it.

Found during #6:
- Mono Station device class: productId `[0,127]`, `firmwareUpdateConfig` `{sendToMainApplication: false, waitForDeviceConfirmation: false}`, so firmware is sent only to the bootloader port. The bootloader port is detected by a MIDI port name containing `"Circuit Mono Station Bootloader"` or `"Circuit Mono Station BL"`.
- `rebootToBootloader` sends `F0 00 20 29 00 71 00 7f 00 00 F7`.
- `0x70` is the version inquiry.
- The generic updater lives near the `WAITING_FOR_PACKET_REQUEST` state enum.

---

## Firmware container format

### #5 Decode the `0x72`/`0x73` data block encoding — DONE
37 septets per block are an MSB-first bitstream: 32 payload bytes plus 3 look-ahead bits (the top bits of the next block in image order, not a checksum). There is no address field. `0x73` holds image offset 0 and is sent last. The decoded image is 129,824 bytes of Cortex-M code, loading at `0x0800C000`. See `docs/firmware-container.md`, `tools/fwsyx.py`, `tools/syx2bin.py` and `tests/test_fwsyx.py`.

### #6 Decode the `0x71` start and `0x76` trailer messages — DONE
`0x71` = product id `00 7f` followed by the version as 6 decimal digits. `0x76` = `00`, `"Firmware"`, then 8 checksum nibbles. The checksum is the negated sum of the image's 32-bit little-endian words. This was confirmed on stock 800 and on Peak 352. See `docs/firmware-container.md` and `checksum` / `build_start` / `build_trailer` in `tools/fwsyx.py`. Open question: whether the bootloader actually verifies the checksum (needs a bootloader dump).

### #7 Round-trip tool: bin → syx — DONE
`tools/bin2syx.py` (`encode_firmware` in `tools/fwsyx.py`) re-encodes Mono Station 800 and Peak 352 byte-for-byte. While doing this, the #5 spare-bit rule was corrected: the look-ahead follows image order, not transmission order. Mono 800 is the only Mono Station build available (#3). Re-run the round-trip test when an older build turns up.

---

## Hardware & static analysis

### #8 Identify the MCU and memory map — DONE (pending hardware confirmation)
STM32F405/407-class Cortex-M4F, most likely an STM32F405VG (LQFP100): 64 KB CCM, DAC present, no SAI, no Ethernet refs, OTG_HS+FS, GPIO A–E only. Load base `0x0800C000` confirmed by the reset handler disassembling to CMSIS startup and 39 vector entries sharing a `b .` default handler at `0x08025EA9`. Memory map (flash sectors, CCM/SRAM/backup-SRAM, peripherals) in `docs/hardware.md`. Remaining: confirm exact part/flash size against a unit (read `FLASHSIZE` `0x1FFF7A22`, or a teardown photo).

### #9 Load firmware into Ghidra with correct memory map — DONE
`tools/ghidra/import_firmware.sh` (headless `analyzeHeadless`, processor `ARM:LE:32:Cortex`, base `0x0800C000`) plus the pre-script `MapMemory.java`, which creates the bootloader/CCM/SRAM/peripheral blocks and seeds disassembly at the reset handler so auto-analysis walks the vector table. `ExportMap.java` dumps the function listing (`docs/ghidra-functions.txt`); `DecompileFunc.java <addr>` prints decompiled C. The Ghidra DB is git-ignored (`ghidra-project/`); only the scripts + listing are committed. **Run end-to-end on Ghidra 12.2 / Java 25: 467 functions, memory map applied, decompiler working.** Scripts are Java, not Jython/Python — Ghidra 12.x headless dropped the bundled Jython and PyGhidra needs a separate Python env.
- **Done.** Follow-up labelling continues under #10.

### #10 Map firmware subsystems — DONE (static pass; Ghidra refines)
`docs/firmware-map.md` locates each subsystem by interrupt vector, peripheral-register literal and debug-string xref: USB (OTG_FS `0x0801EEFC` primary + OTG_HS, with the USB-MIDI/audio descriptors), DIN MIDI (USART1 ISR `0x08023FE1`), UI input dispatcher (`0x08017930`–`0x080179D8`, emits the pad/pot/button/slider debug strings), UI output (SPI LED drivers), analogue CV engine (internal DAC + TIM6 + DMA, likely an external SPI DAC too), ADC, timers (TIM5 = sequencer/tempo clock), and persistent storage (FLASH_IF + Backup SRAM). `tools/fwmap.py` reproduces the scan. Still to isolate with Ghidra function graph: the sequencer engine, the SysEx dispatcher, the patch/session (de)serialiser, and the SPI-port→device split — carried as the follow-up list in the doc.
- **Done when:** `docs/firmware-map.md` gives addresses and short descriptions for each subsystem. ✓

### #11 Diff firmware versions to locate features
Use release notes plus binary diffs between versions (#3) to pin down code tied to known features (for example the step automation added in v1.1). This is a cheap way to anchor subsystem identification.
- **Depends on:** #3, #9

---

## SysEx / MIDI protocol

### #12 Complete the patch SysEx spec
The community PDF (rev. 2020-06-30) leaves gaps:
- unknown offsets: 25 (category?), 26, 28–40, 49, 58, 60 (init value 100)
- untried commands other than `0x00` (replace current patch), such as dump request and write to slot
- the 340-byte in-session patch format versus the 136-byte single-patch message

Method: compare the firmware SysEx handler (#10) with Components JS (patch editor/librarian code) and confirm against live hardware by changing one parameter and diffing dumps. Produce `docs/sysex-spec.md` as the repo's authoritative spec, superseding the PDF, covering every command, every offset, value ranges, encodings and the init values.
- **Done when:** no patch byte is undocumented, all commands the firmware accepts are listed, and a parser/encoder in `tools/` round-trips real dumps.

**Mostly DONE (vendor-sourced; hardware validation pending).** Extracted the authoritative schema from Components 1.72.5 (`app.asar` array `kD`): the modern message is **154 bytes** (9-byte header incl. protocol version 6 / message type / slot; 144-byte payload; `F7`), not the PDF's 136. The payload is a 7→8-bit MIDI bit stream (`RX`/`OX`) unpacking to 126 data bytes; parameters sit at bytes 7–99. `docs/sysex-spec.md` documents framing, the 7↔8 codec, the value types (`UX`), and all **85 parameters** with names/offsets/ranges/CC+NRPN — closing every PDF unknown (25–30 mixer, 31–34 filter, 35–46 env, 47–58 LFO, etc.). The PDF's "pseudo-MSB 0x40" was an artifact of not unpacking. `tools/monostation_patch_params.tsv` is the machine-readable schema; `tools/patch.py` decodes/encodes with a codec that matches Components' `OX` byte-exact (tests in `tests/test_patch.py`). **Remaining for full close:** a real 154-byte dump from a unit to confirm byte-exact, the message-type enum at byte 7, header bytes 4–5, and the name/category split in bytes 100–125 — all listed in the spec's *Open items*.

### #13 Document session format
Decode the 90,112-byte `.monostationsession` layout: the sequencer patterns, step automation, 64 patches at a 512-byte stride, and global settings. Use `session_32.monostationsession` as the reference vector.
- **Depends on:** #12 (patch part)

### #14 Document full MIDI implementation (CC/NRPN)
Cross-check the official MIDI parameter guide against the firmware's CC/NRPN dispatch tables. List parameters that are reachable by SysEx but not by CC; these are candidates for #20.
- **Depends on:** #10

---

## Safe flashing

### #15 Bootloader recovery procedure
Before flashing anything modified, confirm the bootloader lives in a protected region that application updates never overwrite. Then confirm that a stock image can always be restored, using Components or our own tool (#16) from the bootloader button combo. Test this with stock firmware only.
- **Depends on:** #4, #8
- **Done when:** `docs/recovery.md` describes a tested way to get back to stock firmware.

### #16 Command-line firmware flasher
Implement the update protocol (#4) on a host MIDI library so that flashing does not depend on Components. First test: flash stock 800 and confirm the version shown on the device.
- **Depends on:** #4, #15

---

## Patching framework

### #17 Binary patching infrastructure
Build tooling to apply code patches to the decoded image:
- find free flash space (padding at the end of the image, or unused regions)
- inject compiled code (a cross-compiler toolchain for the identified arch)
- hook existing functions with branch patches
- re-pack with #7

Patches should live as source plus a declarative hook list, not as hex blobs.
- **Depends on:** #7, #9

### #18 Hello-world patch
Make the smallest visible change to prove the whole pipeline works: alter a UI string, an LED pattern at boot, or the version number reported over SysEx. Flash it, verify it, then restore stock.
- **Depends on:** #15, #16, #17

### #24 Dump the bootloader via a patched application
The bootloader (`0x08000000–0x0800BFFF`, 48 KB) is not distributed by Novation — the catalogue has only `firmware` and `fpga` images — so it must be read off a real unit. Code running on the MCU can read that flash region regardless of the debug read-out-protection (RDP) level, so the route is: patch the application to send `0x08000000–0x0800BFFF` out as SysEx, flash it, capture the dump. This is a natural first real patch and the same tooling (#16, #17) dumps RAM, option bytes and user patches too.

Before this, check #10 for an existing debug/memory-read SysEx command (the firmware keeps debug strings, so one may exist) — that would need no patching.

Do not attempt to lower RDP via SWD to read the bootloader: dropping from RDP level 1 to 0 mass-erases flash including the bootloader and bricks the unit with no way back.
- **Depends on:** #16, #17 (or #10 alone if a memory-read command exists)

### #25 Probe bootloader acceptance checks
With a tested recovery path in place, send malformed firmware (wrong checksum, wrong product id, truncated) to learn what the bootloader validates — answers the open "is the checksum verified?" question without a dump. Safe because the bootloader region stays intact.
- **Depends on:** #15, #16

---

## New features (candidates — scope after #10)

### #19 Patch dump request / bulk backup over SysEx
If the firmware does not already support it (see #12), add a command that dumps the current patch or session on request, so editors can sync without Components.

### #20 Expose SysEx-only parameters via CC/NRPN
Map parameters missing from the CC table (#14) to free CC or NRPN numbers.

### #21 Additional LFO waveforms / modulation options
The LFO is digital, so new shapes (S&H variants, random, ramp) are firmware-only additions. Feasibility depends on the LFO code (#10).

### #22 Sequencer enhancements
Candidates: per-step probability, ratchets, extra scales, and longer or chained patterns. Before deciding scope, measure the remaining RAM and flash headroom and check the session format constraints (#13).

### #23 Feature request intake
Collect the features the user actually wants and turn each one into an issue once #10 shows what is feasible.
