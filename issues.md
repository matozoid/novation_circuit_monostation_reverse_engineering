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

### #4 Reverse the firmware-update protocol from Components JS — DONE (host side; hardware capture pending)
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

**Done:** `docs/update-protocol.md`.
- Entry: Components sends the 15-byte `0x71` start message to the running application, then waits up to 8 s for the bootloader port.
- Flow control is device-driven: each packet waits up to 10 s for a `F0 00 20 29 00 72 a b c F7` ack with an increasing counter.
- The loop stops after the `0x73` (offset 0) packet, so the `0x76` checksum trailer is never sent.
- Success: the unit reboots and reports the new version via `0x70`. Status codes 124/126 mean failure.
- Open: the manual bootloader entry, the real ack timing, and what the bootloader validates (#25).

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

**Mostly DONE (vendor-sourced; hardware validation pending).** Extracted the authoritative schema from Components 1.72.5 (`app.asar` array `kD`): the modern message is **154 bytes** (9-byte header incl. protocol version 6 / message type / slot; 144-byte payload; `F7`), not the PDF's 136. The payload is a 7→8-bit MIDI bit stream (`RX`/`OX`) unpacking to 126 data bytes; parameters sit at bytes 7–99. `docs/sysex-spec.md` documents framing, the 7↔8 codec, the value types (`UX`), and all **85 parameters** with names/offsets/ranges/CC+NRPN — closing every PDF unknown (25–30 mixer, 31–34 filter, 35–46 env, 47–58 LFO, etc.). In the 154-byte wire the "pseudo-MSB 0x40" was an artifact of not unpacking. `tools/monostation_patch_params.tsv` is the machine-readable schema; `tools/patch.py` decodes/encodes with a codec that matches Components' `OX` byte-exact (tests in `tests/test_patch.py`).

**Format landscape clarified (3 encodings).** There are three distinct patch encodings — do not conflate (see spec):
- **136-byte bank/pack** (`F0 00 20 29 01 61 …`): the real-world storage format (firmware memory, `.monostationpack`, the PDF). `.monostationpack` = a ZIP holding `patches.syx`; a full bank = 64 × 136 = 8704 bytes. Name@9, category@25 / genre@26 (enums resolved in #28), params@41 in the split LSB+`0x00`/`0x40` encoding — here the `0x40` bytes are **real** (PDF correct). `tools/patchbank.py` reads packs/banks (framing, name, category, genre, slot; tests `tests/test_patchbank.py`). Corroborated by the real bank `Roboo.monostationpack` and two firmware-embedded defaults (`Classic Mono`@`0x08028864`, `Initial Patch`@`0x08028F6B`).
- **154-byte modern wire** (above).
- **122-byte legacy wire** (up-converted to 154 by Components).

**Resolved PDF/open unknowns:** device id (bytes 4–5) = **`01 61`** (`yte` validator; Circuit is `01 60`). Message type (byte 6/7): `0`=current patch, `1`=slot write/dump, `0x40`=request patch (`F0 00 20 29 01 61 40 <slot> 00 F7`). Offset 25 = category, 26 = genre.

**136-byte split-pair decode — RESOLVED.** Cracked by correlating the YBC soundpack banks against the per-patch Components screenshots shipped in the pack. Two flag meanings, both LSB + `0x00`/`0x40` one byte later: (a) **genuine +128** for unsigned 0–255 params (mixer, cutoff, LFO rate) → `value = LSB + 128·(MSB==0x40)`; (b) **redundant sign flag** for bipolar mod-matrix pitch/cutoff depths → `value = LSB − 64`, flag mirrors the sign and must be ignored. Validated byte-exact (YBC "Acid" etc.). Implemented as `PARAMS` + `decode_params` in `tools/patchbank.py` (`show` subcommand); tests in `tests/test_patchbank.py`. Editor display curves (cutoff/release, ×2 pitch/cutoff mods) noted — cosmetic, not in the raw bytes.

**Caveat found:** byte 25 (`category`) in the vendor soundpack banks does **not** track patch names (lead patches tagged Bass/Drum) — either the enum differs from the Roboo pack or packs don't set it meaningfully. Category enum still TBD.

**Also in scope — pack overlays and soundpack bundles:** packs carry a per-parameter *overlay* map (`overlayIndex` 1–80 in the schema; `hasOverlay` / `clearOverlay` / `initOverlaysFromCurrentPatch` in Components) marking which parameters a pack overrides. Its storage format is undocumented. Vendor soundpacks (e.g. the YBC zip) bundle a `.monostationpack`, a raw `*Patches*.syx` bank and a `*Sessions*.syx` dump together; document where the overlay lives and how the bundle pieces relate.

**Remaining for full close:** osc coarse/fine centre+scale semantics, the overlay format (above), a real 154-byte dump to confirm that path, and the name/category split in the 154 format's bytes 100–125. See spec *Open items*.

### #26 Investigate what the 154-byte patch added over the 122-byte legacy
Components up-converts the legacy **122-byte** wire message (`LEGACY_SYSEX_SIZE=122`, `LEGACY_PATCH_SIZE=112`) to the modern **154-byte** message (protocol version 6, 126 unpacked bytes) before parsing (`JX`). Work out the delta: which parameter(s) or bytes the newer format added, and how the up-converter maps/defaults them. This pins down what a firmware revision introduced and whether any parameter is modern-only.
- **Approach:** read `JX` / the legacy path in Components' `main-*.js` (the `fromPatchSysex` neighbourhood), diff the legacy 112-byte payload layout against the 126-byte modern one, and cross-check against the 136-byte storage map.
- **Depends on:** #12.

### #28 Enumerate patch category and genre values — DONE
**Resolved:** Components' Circuit-family librarian supplies the tables (`mP`/`gP`; the Mono Station model `kte` maps category@16 and genre@17 after the name, which are bytes 25/26). Category: 0 None, 1 Arp, 2 Bass, 3 Bell, 4 Classic, 5 Drum, 6 Keyboard, 7 Lead, 8 Movement, 9 Pad, 10 Poly, 11 SFX, 12 String, 13 User, 14 Voc/Tune. Genre: 0 None, 1 Classic, 2 D&B/Breaks, 3 House, 4 Industrial, 5 Jazz, 6 R&B/HHop, 7 Rock/Pop, 8 Techno, 9 Dubstep. These are `patchbank.CATEGORIES`/`GENRES`, and the details are in `docs/sysex-spec.md`. The earlier guesses `1` = Bass and `3` = Drum were wrong: Roboo's author tagged basses Arp and drums Bell. Real packs treat the byte as a loose user tag. Not checked: whether the firmware itself reads these bytes, for example for on-device filtering (a quick look found no category strings).

Original note: bytes 25 (category) and 26 (genre) of the 136-byte patch are identified but their value enums are not. Observed so far: category `1` = Bass and `3` = Drum/Percussion in `Roboo.monostationpack`, but in the YBC soundpack banks the values don't track patch names (leads tagged Bass/Drum), and genre is `0` everywhere.
- **Approach:** find the category/genre name tables in Components' `main-*.js` (librarian filter UI) and/or the firmware's string table; confirm against packs whose category is set deliberately.
- **Depends on:** #12.

### #13 Document session format — MOSTLY DONE
**Resolved** (`docs/session-format.md`, `tools/session.py`, `tests/test_session.py`). The layout comes from the firmware initialiser `FUN_08018CA4` and is checked against 48 real YBC sessions.
- Header: magic, struct size `0x15FFC`, flags, pad colour, 32-byte name. Globals: tempo, swing, per-track chain start/end.
- **Three tracks:** 16, 8 and 8 patterns of 140 bytes each (16 × 8-byte steps plus a 12-byte footer).
- Automation lanes are 108 bytes: a 96-bit mask plus 96 values (16 steps × 6 micro-steps). Track 0 has 42 lanes per pattern at `0x918`, track 1 has 12 at `0x128F8`, and track 2 has none.
- **One** 126-byte patch per session at `0x15F5C`; every YBC session's patch matches its bank. The "64 patches at a 512-byte stride" idea is the separate patch store `MS_ALL_PATCHES`, not part of the session.
- **Tracks:** 0 = synth/osc 1, 1 = osc 2, 2 = mod sequence.
- **Step bytes**, from the playback routine `FUN_080234AC`:
  - 0 = note (0 = empty);
  - 1 = velocity (0 plays as 96); on track 2 it carries the mod value;
  - 2 = gate in 1/6-step units, clamped 1–96;
  - 3 = value for a per-track parameter id (`0x09`/`0x10`/`0x4C`);
  - 4 = patch change: slot 0–63, sent as id `0x50`; the session controller (`FUN_08019AE4`) loads it from the patch store at `0x2000 + slot × 0x200`.
- **Lane → parameter map:** resolved from the firmware tables `0x08027588` and `0x0802775C` plus the 76-entry parameter-id table at `0x0802A62A`, which gives each id's patch offset, range and CC/NRPN. In `tools/session.py` these are `LANE_IDS`, `PARAM_OFFSETS` and `lane_param`, checked against the firmware by `FirmwareTablesTest`.
- Footer: byte 0 = start step << 4 | end step (may wrap), byte 1 = sync rate index into ticks-per-step `[2,3,4,6,8,12,16,24]` (3 = 1/16), byte 2 = direction (forward/reverse/ping-pong/random); from song position `FUN_0801B698`. Byte 3 = mod-sequence smooth (linear crossfade between step values, `FUN_08023998`).
- Patch bytes the PDF leaves out, named from the setter's parameter groups, MIDI numbers and the YBC screenshots: 27 amp-env re-trigger (NRPN 0:73), 49/58 osc 1/2 glide (CC 5/37; so step byte 3 on the note tracks is per-step glide), 60 patch level (CC 7). These are in `tools/patchbank.py` `PARAMS`.
- `0x155DA` is the current patch slot (`session.header()["patch_slot"]`), confirmed against every YBC session whose patch is in its pack. `0x155DD`/`0x155DE` are slots 6/0 of a third parameter group (ids `0x4C`/`0x4D`).
- The CC differences with Components are explained: `kD` describes a different parameter set (sub osc, LFO 2, mod env, aftertouch) and matches firmware 800 only on a subset of CCs.
- Still open: `0x42` (third field of the tempo/swing observable), `0x155D8`/`0x155D9`, `0x155DB`/`0x155DC`, and what ids `0x4C`/`0x4D` control (`docs/session-format.md`).

Original note: decode the 90,112-byte `.monostationsession` layout: the sequencer patterns, step automation, 64 patches at a 512-byte stride, and global settings. Use `session_32.monostationsession` as the reference vector.
- **New data (#27):** `tools/sessionsyx.py split <Sessions.syx> outdir/` yields 32 populated `USER` session images per YBC dump (sessions 0–15 differ between the main and Alt packs), giving real, non-init vectors to diff against `session_32`. The init image is sparse: `INIT` magic, the name at `0x20`, repeating `00 60 00 ff` 16-byte step records at `0x800` and `0x12800`, and patch data around `0x15800` (`Classic Mono` at `0x15F5C`). Only one patch name appears in the init image, so the "64 patches at 512-byte stride" assumption is unverified.
- **Depends on:** #12 (patch part)

### #27 Document the session SysEx transport protocol — DONE
**Resolved** (`docs/session-transport.md`, `tools/sessionsyx.py`, `tests/test_sessionsyx.py`). Each `0x79` body is 293 septets in groups of 8: a high-bit septet (bit *i* → byte *i*) followed by 7 bytes, giving 256 bytes per block. The supposed "flag" byte is just the first high-bit septet. The header carries an unknown field `0x00012000` and the stream length as 8 nibbles each. The trailer is the **CRC-32 (zlib)** of the unpacked stream. A full dump holds 33 × 90,112-byte images: 32 `USER` sessions plus an `INIT` image that's byte-identical to `session_32.monostationsession`. Both YBC dumps re-encode byte-exact. **Update (Components `k0` + decode worker):** the header's `0x12000` is the **storage start address**; patches are at `0x2000`, 64 slots of 512 bytes. A dump is requested with `F0 00 20 29 00 78 <start> <length> F7`. An upload is flow-controlled: the device sends `0x7B` or `0x75` to ask for each block, then `0x7A` with its CRC. See `docs/session-transport.md`. Remaining hardware test: whether the device accepts other start/length values, such as a single session.

Original notes:
Sessions travel over SysEx in a framing distinct from the on-device `.monostationsession` file (#13). Observed in the YBC `*Sessions*.syx` (3,484,838 bytes, 11,618 messages):
- `F0 00 20 29 00 77 …` (23 bytes) — transfer header / metadata (`00 00 00 01 02 00 00 00 00 00 02 0D 06 00 …`).
- `F0 00 20 29 00 79 <flag> …` (300 bytes, ×11,616) — body blocks; payload 7-bit clean. `<flag>` is mostly `00`, sometimes `01`/`08`/`10`/`18`/`7F` (meaning TBD, likely block/section markers). The first block's payload begins `55 53 45 52` ("USER"; the init file starts `INIT`).
- `F0 00 20 29 00 7A …` (15 bytes) — trailer (`01 08 08 01 03 0C 05 01`).

One `0x77`…`0x7A` transfer carries **all 32 sessions** (~2.9 MB unpacked ≈ 32 × 90,112). Plain Components `OX` 8→7 unpacking does **not** recover the `USER`/`INIT` magic, so the packing differs — try the firmware container's 37-septet→32-byte scheme (`docs/firmware-container.md`).
- **Goals:** packing scheme, header/trailer fields, `<flag>` meaning, per-session boundaries; then a `tools/` reader that turns the dump into 32 `.monostationsession` images.
- **Depends on:** #13 (to validate the unpacked images). Feeds #19.

### #14 Document full MIDI implementation (CC/NRPN)
Cross-check the official MIDI parameter guide against the firmware's CC/NRPN dispatch tables. List parameters that are reachable by SysEx but not by CC; these are candidates for #20.
- **Depends on:** #10

---

## Safe flashing

### #15 Bootloader recovery procedure
Before flashing anything modified, confirm the bootloader lives in a protected region that application updates never overwrite. Then confirm that a stock image can always be restored, using Components or our own tool (#16) from the bootloader button combo. Test this with stock firmware only.
- **Depends on:** #4, #8
- **Done when:** `docs/recovery.md` describes a tested way to get back to stock firmware.

### #29 Find the manual bootloader entry
Components has no hint of how to start the unit in its bootloader without the application's help; it only reboots a running unit with `0x71` (`docs/update-protocol.md`). Recovery from a modified image that won't boot, or won't answer `0x71`, needs a way in that doesn't depend on the application, probably a button combination held at power-on. Look in the user manual, Novation support articles, and the application's boot path (it may check a key before jumping to its own code, or the bootloader may check one before jumping to the app).
- **Feeds:** #15
- **Done when:** the entry method is documented and tried on a unit with stock firmware.

### #30 Trace the application's update and version handlers
Find the SysEx handlers in firmware 800 that serve the update protocol from the application side:
- `0x71`: does the app check the product id and version digits before rebooting, and how does it hand over to the bootloader (a flag in backup SRAM or RAM, then a reset)?
- `0x70`: where the version reply is built, and where the bootloader version it reports comes from.

This tells us what a patched image must keep intact so that Components and our flasher can still reboot it into the bootloader. It also shows where the reported version lives, which matters because Components only offers a restore when the version is below 800.
- **Feeds:** #15, #16, #18

### #16 Command-line firmware flasher
Implement the update protocol (#4) on a host MIDI library so that flashing does not depend on Components. First test: flash stock 800 and confirm the version shown on the device.
- **Decision needed:** the tooling is stdlib-only, but Python's stdlib has no MIDI I/O. Options: a library such as `mido` + `python-rtmidi`, raw ALSA sequencer access through `ctypes`, or writing SysEx to the raw `/dev/snd/midiC*D*` device (Linux only, no extra dependency).
- Follow `docs/update-protocol.md`: wait for each `0x72` ack, stop after the `0x73` block, then poll `0x70` for the new version.
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
**Existing support found (#27):** the stock firmware already serves bulk dumps on request: `F0 00 20 29 00 78 <start: 8 nibbles> <length: 8 nibbles> F7`. All sessions are `0x12000`/`0x2D6000` and all patches are `0x2000`/`0x8000` (`sessionsyx.request()`). The current-patch request is message type `0x40` (#12). What remains here is anything finer-grained, such as a single session, if the firmware refuses arbitrary ranges.

Original note: if the firmware does not already support it (see #12), add a command that dumps the current patch or session on request, so editors can sync without Components.

### #20 Expose SysEx-only parameters via CC/NRPN
Map parameters missing from the CC table (#14) to free CC or NRPN numbers.

### #21 Additional LFO waveforms / modulation options
The LFO is digital, so new shapes (S&H variants, random, ramp) are firmware-only additions. Feasibility depends on the LFO code (#10).

### #22 Sequencer enhancements
Candidates: per-step probability, ratchets, extra scales, and longer or chained patterns. Before deciding scope, measure the remaining RAM and flash headroom and check the session format constraints (#13).

### #23 Feature request intake
Collect the features the user actually wants and turn each one into an issue once #10 shows what is feasible.
