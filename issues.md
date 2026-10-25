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

**Decided (2026-10-25):** look for older builds in older Components releases, extracted statically.

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
**Read from a unit (2026-10-25, memread v803): STM32F42x/43x**, `DBGMCU_IDCODE` DEV_ID `0x419`, REV_ID `0x2001`, 512 KB `FLASHSIZE`, 192 KB SRAM (SRAM3 in use). This replaces the earlier static guess of an STM32F405VG, which was based on 64 KB CCM, DAC, no SAI, no Ethernet references, OTG_HS+FS and GPIO A–E only. Load base `0x0800C000` confirmed by the reset handler disassembling to CMSIS startup and 39 vector entries sharing a `b .` default handler at `0x08025EA9`. Memory map (flash sectors, CCM/SRAM/backup-SRAM, peripherals) in `docs/hardware.md`. Remaining: the exact part and package (a teardown photo).

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

**Mostly DONE (hardware validation pending).** The Mono Station has one patch format, the **136-byte** message (`F0 00 20 29 01 61 …`), used on the wire, in `.monostationpack` files and in firmware memory. `.monostationpack` is a ZIP holding `patches.syx`, a 64 × 136 = 8704-byte bank. Name@9, category@25 / genre@26 (enums resolved in #28), params@41, with 8-bit params split over two bytes (high 7 bits, then the low bit as `0x00`/`0x40`). `tools/patchbank.py` reads packs and banks (tests in `tests/test_patchbank.py`). Corroborated by the real bank `Roboo.monostationpack`, the two firmware-embedded defaults (`Classic Mono`@`0x08028868`, `Initial Patch`@`0x08028F6B`) and Components' Mono Station init patch, which equals `Initial Patch` byte for byte.

**Correction (#26):** an earlier status here called a **154-byte** packed message (with `kD`, `RX`/`OX`, a 122-byte legacy form) the "modern" Mono Station format and said it closed every PDF unknown. All of that is Components' **Bass Station II** code. It now lives in `docs/bass-station-ii.md` and `tools/bassstation2_patch.py`. The PDF unknowns were closed instead from the firmware's own tables (#13: offsets 27, 49, 58, 60).

**Resolved PDF/open unknowns:** device id (bytes 4–5) = **`01 61`** (`yte` validator; Circuit is `01 60`). Message type (byte 6/7): `0`=current patch, `1`=slot write/dump, `0x40`=request patch (`F0 00 20 29 01 61 40 <slot> 00 F7`). Offset 25 = category, 26 = genre.

**136-byte split-pair decode — RESOLVED (corrected 2026-10-25).** The firmware's patch store `FUN_080176e8` writes each CC(8) param as `byte[off] = v >> 1`, `byte[off+1] = (v & 1) << 6`, so `v = byte[off] << 1 | byte[off+1] >> 6`. Signed ones subtract the centre from the firmware table at `0x0802A62A`: 128 for coarse and pitch/cutoff mod depths, 100 for fine. The earlier decode (`LSB + 128·flag`, with the flag "redundant" for mod depths) agreed only at 0/255. It invented a "×2 display curve" for mod depths. The new decode matches the firmware `Initial Patch` centres, the MIDI PDF's default LFO rate 68, and all 62 usable YBC "Patch Details" screenshots value for value (Acid and Italo were edited after export; see `docs/sysex-spec.md` *Editor screenshots*). `tests/test_patchbank.py` checks every `PARAMS` kind and MIDI number against the firmware table and Novation's MIDI parameter PDF (`circuit-mono-station-midi-parameters-v1.pdf`); all 74 match. There is no display curve: the editor shows raw values. It never shows glide (bytes 49/58) and swaps the Ext/Ringmod slider labels.

**Bytes 27–40 (2026-10-25):**
- Byte 27 is the envelope Trigger: param id 2, NRPN 0:73, 0–1. The YBC editor never lights Re-Trigger, so the Animals puzzle is an editor quirk.
- Bytes 28–40 have no param-table record (patch offsets 19–31) and are zero in every known patch, so they are unused.
- **Trigger on hardware (2026-10-25): 1 = retrigger, 0 = legato.** It is the panel's Shift + PWM toggle (light on = 1), and NRPN 0:73 sets it too; both were checked by reading the current patch back. Test it with a pluck (attack 0, decay ~70, sustain 0). With sustain at maximum, both settings sound the same, which is why the first test looked like "no effect".
- **`0x40` request on hardware:** only `40 00` answers. The reply is the **current working patch** as a type-0 message (`… 61 00 00 00` + patch), not slot 0's stored data. Slots 1, 40, 63, 64 and 127 get no reply, so the request can't dump slots; use the bootloader's `0x78` for that.
- **Slot write on hardware:** a type-1 write to slot 64 stored the patch in flash (checked with `p 0x40` reads) and left slots 65–71 in the same 4 KiB sector untouched. It writes the whole 512-byte slot, and bytes 126–511 come from a RAM buffer (zeros and stale data, here an old `0x40` reply). Panel-saved slots 7, 8, 24 and 31 show the same. Only the first 126 bytes are read back.
- Still open:
  - coarse tune quantisation (±1 octave over −128…127).
- Components' `resources/mididata/factory-patches.syx` is **Circuit** (`01 60`), not Mono Station factory patches.

**Caveat found:** byte 25 (`category`) in the vendor soundpack banks does **not** track patch names (lead patches tagged Bass/Drum) — either the enum differs from the Roboo pack or packs don't set it meaningfully. Category enum still TBD.

**Also in scope — soundpack bundles:** (the "overlay" map once listed here is Bass Station II's AFX overlay, not a Mono Station feature.) Vendor soundpacks (e.g. the YBC zip) bundle a `.monostationpack`, a raw `*Patches*.syx` bank and a `*Sessions*.syx` dump together; document how the bundle pieces relate.

**Remaining for full close:** coarse tune's unit (range −128…127; fine is −100…100). See spec *Open items*.

### #26 Investigate what the 154-byte patch added over the 122-byte legacy — DONE (not Mono Station)
**Resolved:** neither format belongs to the Mono Station. Components keeps them in its Bass Station II modules:
- the 154/122 constants are `frontend/constants/bass-station-ii/content-transfer`;
- `JX` is `frontend/utils/bass-station-ii/convert-legacy-patch` (its errors are `errors.bass-station-ii.*`);
- `kD` is `frontend/constants/bass-station-ii/afx-overlay-midi-config`;
- the device is product id `00 33`.

`JX` copies the 122-byte message into a zeroed 154-byte buffer, writes `NO NAME` at `length − 17` and ends it with `F7`. So the legacy form is the same layout without the name.

The Mono Station's patch validator accepts only 136 bytes. The old spec's "protocol version = 6" was a misread too: 6 is the byte's index, and its value is `0`.

The material was moved to `docs/bass-station-ii.md`, `tools/bassstation2_patch.py`, `tools/bassstation2_params.tsv` and `tests/test_bassstation2_patch.py`. `docs/sysex-spec.md` now covers only the 136-byte format.

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
- The CC differences with Components are explained: `kD` is Bass Station II's parameter array (#26), so it matches firmware 800 only on Novation's shared CCs.
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
- **System Settings record (hardware, 2026-10-25):** the MIDI channels, clock out rate, pot pickup and the six Note/CC/Clock Rx/Tx switches are stored at external flash `0x1000` (24 bytes) and saved only at power-off. The layout is in `docs/session-transport.md` *Settings record*. Open: byte `0x00` (`08`), bit 1 of `0x08`, byte `0x14` (`FF`), and the Osc 2 channel at `0x10`, which is inferred but not yet changed on hardware.

---

## Safe flashing

### #15 Bootloader recovery procedure
Before flashing anything modified, confirm the bootloader lives in a protected region that application updates never overwrite. Then confirm that a stock image can always be restored, using Components or our own tool (#16) from the bootloader button combo (Scales + Note + Velocity at power-on, #29). Test this with stock firmware only.
- **Depends on:** #4, #8
- **Done when:** `docs/recovery.md` describes a tested way to get back to stock firmware.

### #29 Find the manual bootloader entry
Components has no hint of how to start the unit in its bootloader without the application's help; it only reboots a running unit with `0x71` (`docs/update-protocol.md`). Recovery from a modified image that won't boot, or won't answer `0x71`, needs a way in that doesn't depend on the application, probably a button combination held at power-on. Look in the user manual, Novation support articles, and the application's boot path (it may check a key before jumping to its own code, or the bootloader may check one before jumping to the app).
- **Feeds:** #15
- **Done when:** the entry method is documented and tried on a unit with stock firmware.
- **Status:** documented, not yet tried.
  - **Entry:** hold **Scales + Note + Velocity** while powering on (user guide v1.2 p. 90, and Novation support). The grid shows a reduced set of buttons; Osc 1 and Osc 2 show the firmware versions in binary; **Play** exits.
  - The app has no code for this; its only jump to the bootloader is the SysEx reboot. So the bootloader reads the buttons itself, and the combination should survive a broken app image.
  - Details are in `docs/update-protocol.md` → *Entering the bootloader by hand*.
  - The guide also says the bootloader updates factory patches, which may explain where the factory bank lives (#8).
- **Still open:** try the button combo on a unit with stock firmware and read the version patterns.
- **Button combo tried 2026-10-25 on stock firmware: works.** The bootloader grid appeared and the same `Circuit Mono Station BL` port came up. Its `0x70` reply is identical to the app's: bootloader 740, firmware 800, `30 01`. Version lights as reported, in four groups of 8 pads: Osc 1 = `00000111 01000000 00000000 00000000`, Osc 2 = `00001000 00000000 00000000 00000000`. Osc 2 reads 8,0,0,0 (800), and Osc 1's first group reads 7. The second group is 64 if read MSB-left, where 4 was expected; recheck. The open item above is done apart from that.
- **Also tried 2026-10-25 via the `0x71` reboot:** the bootloader's USB-MIDI port is named **`Circuit Mono Station BL`** (ALSA `Circuit Mono Station BL MIDI 1`), not `…Bootloader`; Components' check accepts both. It answers `0x70`, serves the `0x78` dumps, and **Play** returns to the app. The app's `0x70` reply reports bootloader **740**, firmware 800.

### #30 Trace the application's update and version handlers
Find the SysEx handlers in firmware 800 that serve the update protocol from the application side:
- `0x71`: does the app check the product id and version digits before rebooting, and how does it hand over to the bootloader (a flag in backup SRAM or RAM, then a reset)?
- `0x70`: where the version reply is built, and where the bootloader version it reports comes from.

This tells us what a patched image must keep intact so that Components and our flasher can still reboot it into the bootloader. It also shows where the reported version lives, which matters because Components only offers a restore when the version is below 800.
- **Feeds:** #15, #16, #18
- **Status:** traced statically, see `docs/sysex-handlers.md`.
  - `0x71` checks only `00 7F` at bytes 6–7; bare `0x60` does the same.
  - Handover: USB soft-disconnect, a delay, `2` → backup SRAM `0x40024000`, then a direct jump to `0x08000000` (no reset).
  - `0x70` reports the BCD version from the image header at offset `0x1AC` (`MAIN 00 08`). The bootloader version comes from the same header at `0x080001AC`.
  - A side find: `F0 00 20 29 01 61 70 40` reads, writes and erases the external SPI flash (patches and sessions), not MCU flash.
- **Still open:** confirm on hardware (send `0x70` and check the reply bytes).

### #16 Command-line firmware flasher
Implement the update protocol (#4) on a host MIDI library so that flashing does not depend on Components. First test: flash stock 800 and confirm the version shown on the device.
- **Decided (2026-10-25):**
  - MIDI I/O: `mido` + `python-rtmidi`. This is the one exception to the stdlib-only rule; the import stays inside the flasher so the other tools and tests stay stdlib-only.
  - Platforms: Linux and Windows.
  - Bootloader entry: manual only. The tool asks the user to hold Scales + Note + Velocity at power-on and waits for the bootloader; it does not send the `0x71` reboot.
  - Checks: before sending, decode the `.syx` and verify its structure (message types and order, block count, checksum, product id). After the `0x73` block, poll `0x70` and compare with the expected version.
- Follow `docs/update-protocol.md`: wait for each `0x72` ack, stop after the `0x73` block, then poll `0x70` for the new version.
- **Built (2026-10-25):** `tools/flash.py`, tests in `tests/test_flash.py`. A dry run against a fake bootloader passes.
- **Hardware (2026-10-25): works.** Stock 800 flashed onto a unit running 800 (bootloader 740). Entry was manual. The bootloader answered `0x70` with (740, 800) and acked all 4,058 packets, which took 7 s. After the `0x73` packet the app came back by itself and reported 800. So the bootloader accepts the same version again and doesn't need the `0x76` trailer.
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
- **Decided (2026-10-25):** injected code is assembly only, with GNU binutils (`binutils-arm-none-eabi`) and no C toolchain.
- **Built (2026-10-25):** `tools/fwpatch.py`, spec in `docs/patching.md`, tests in `tests/test_fwpatch.py`. Patch code is linked at the first 32-byte boundary after the stock image (`0x0802BB20`), up to `0x08080000` (raised from `0x08040000` after the bootloader analysis, #25). Hooks are `bl`, `b`, `word` and `bytes`; each one checks the stock bytes it replaces. An optional `version` sets the image header and the `.syx` start message. Branch encoding is checked against the stock image and `ld` output.
- **Open:** the bootloader's maximum image size (#25); free RAM for patch state is **found (2026-10-25)**: CCM `0x10000000`+ (no static data, stack ~1.1 KB at the top) and SRAM3 `0x200235B8`–`0x2002FFFF` (past the heap's `_sbrk` limit; the heap ends at `0x20020DFC`). SRAM1+2 is full (`docs/hardware.md` *Free RAM*). Still open: a stack high-water mark after heavy use; the patch hasn't run on hardware yet (#18).
- **Depends on:** #7, #9

### #18 Hello-world patch
Make the smallest visible change to prove the whole pipeline works: alter a UI string, an LED pattern at boot, or the version number reported over SysEx. Flash it, verify it, then restore stock.
- **Built and flashed (2026-10-25):** `patches/hello/` (`patch.toml` + `hello.s`) sets the version to 801 and sends the reset handler's call to main (`0x08025E8E`) through 4 bytes of injected code at `0x0802BB20`, which tail-calls the stock main. `tools/flash.py firmware/hello-801.syx` sent 4,059 packets in 7 s, and the app came back reporting (740, 801). So the bootloader accepts an image 32 bytes longer than stock (the trailer and its checksum are never sent), and the injected code runs at boot.
- In the bootloader, Osc 2 shows `00001000 00000001 00000000 00000000`: the patched header version `0x0801`, high byte first. So the panel can tell which build is flashed (`docs/update-protocol.md`). Osc 1 shows `00000111 01000000`, the bootloader's 740 in the same format.
- **Done (2026-10-25):** the unit worked normally on 801 (sound, pads, patch change). Stock 800 was then restored with `tools/flash.py`. The whole pipeline (#17 build → #16 flash → verify → restore) works.
- **Depends on:** #15, #16, #17

### #24 Dump the bootloader via a patched application
The bootloader (`0x08000000–0x0800BFFF`, 48 KB) is not distributed by Novation — the catalogue has only `firmware` and `fpga` images — so it must be read off a real unit. Code running on the MCU can read that flash region regardless of the debug read-out-protection (RDP) level, so the route is: patch the application to send `0x08000000–0x0800BFFF` out as SysEx, flash it, capture the dump. This is a natural first real patch and the same tooling (#16, #17) dumps RAM, option bytes and user patches too.

**Tried 2026-10-25 (no):** the bootloader's `0x78` dump doesn't reach MCU memory. Requests at `0x08000000`, `0x0800C000` (control), option bytes, OTP, system memory, SRAM, CCM and backup SRAM all return `00`s (`docs/session-transport.md` *Storage map*). The app's `p 0x40` read is external flash only and stops at `0x800000`.

Before this, check #10 for an existing debug/memory-read SysEx command (the firmware keeps debug strings, so one may exist) — that would need no patching.

Do not change RDP via SWD. Going from level 1 to 0 mass-erases flash, including the bootloader, and bricks the unit. The option-byte dump below shows this unit is at level 0, so no change is needed.
- **Done (2026-10-25):** patch `patches/memread` (version 802) hooks the SPI read call of `'p' 0x40` op 2 (`0x0802066A`). Addresses in MCU memory are copied from memory; everything else still reads external flash. `tools/p40dump.py 0x08000000 0x10000` read 64 KiB:
  - The app part `0x0800C000`–`0x0800FFFF` matches the stock image except for the patched version byte, so the dump is faithful.
  - **Bootloader 740:** header `BOOT` + `40 07` at `0x080001AC`. Initial SP `0x10010000`, reset vector `0x08005F45`. Code and data end at `0x08007003` (about 28 KB); the rest of the 48 KiB is `FF`. Strings include `Circuit Mono Station BL` and `Focusrite A.E`.
  - Saved as `firmware/bootloader-740.bin` (git-ignored, like the stock image).
  - **Other reads with the same patch (2026-10-25, files in `~/ms-dumps/`, not in the repo):**
    - Option bytes `0x1FFFC000`: `EF AA 10 55`, then `FF 3F 00 C0`. RDP `0xAA` = **level 0**, so there is no read-out protection and SWD could read and debug the chip. nWRP `0xFFF` = no sector is write-protected, including the bootloader. USER `0xEF`: BOR off, software watchdog.
    - `FLASHSIZE` (`0x1FFF7A22`) = `0x0200` = **512 KB**, not the 1 MB of an F405VG (`docs/hardware.md`). OTP `0x1FFF7800`–`0x1FFF7A0F` is blank. The 96-bit unique id reads fine, but it identifies this unit, so it stays out of the repo.
    - Flash `0x0800C000`–`0x0807FFFF`: the running 802 image byte for byte, then `FF` to the 512 KB end. Nothing is stored past the app. `0x08080000`+ was not read, because it may fault on a 512 KB part.
    - ST system ROM `0x1FFF0000`–`0x1FFF77FF`: vectors SP `0x20002D40`, reset `0x1FFF5163`. This is ST's own USB DFU/UART bootloader, kept for reference only.
    - Backup SRAM: word 0 = `1` (the app's "normal" flag, `docs/sysex-handlers.md`); `0x200` and up looks random, i.e. never written.
    - SRAM snapshot while running: a zero run at `0x20004986`–`0x20016511` (about 70 KB). It is a candidate for patch RAM, but a snapshot doesn't prove the area is unused.
  - Next: disassemble the bootloader (#25 acceptance checks, backup SRAM flag values, the button check).
- **Depends on:** #16, #17 (or #10 alone if a memory-read command exists)

### #25 Probe bootloader acceptance checks
**Answered statically (2026-10-25) from the dump, `docs/bootloader.md`:**
- `0x71` checks only `00 7F`, then erases sectors 3–7 at once.
- `0x72` blocks must be exactly 44 bytes; other lengths are dropped silently.
- No checksum is verified, and the version digits are ignored.
- `0x73` writes the vector block and boots the app right away. The app is accepted when its tag is `TEST`/`MAIN`/`BOOT` and its SP word isn't `FFFFFFFF`.
- Maximum image: up to `0x0807FFFF`.
- Backup SRAM `2` = stay in the bootloader, `1` = set before jumping to the app.
- The bootloader starts a ~6 s IWDG.

The hardware probing below is no longer needed to answer these questions. It could still confirm them.
With a tested recovery path in place, send malformed firmware (wrong checksum, wrong product id, truncated) to learn what the bootloader validates — answers the open "is the checksum verified?" question without a dump. Safe because the bootloader region stays intact.
- **Depends on:** #15, #16

---

## New features (candidates — scope after #10)

### #19 Patch dump request / bulk backup over SysEx
**Existing support found (#27):** the stock firmware already serves bulk dumps on request: `F0 00 20 29 00 78 <start: 8 nibbles> <length: 8 nibbles> F7`. All sessions are `0x12000`/`0x2D6000` and all patches are `0x2000`/`0x8000` (`sessionsyx.request()`). The current-patch request is message type `0x40` (#12). What remains here is anything finer-grained, such as a single session, if the firmware refuses arbitrary ranges.

Original note: if the firmware does not already support it (see #12), add a command that dumps the current patch or session on request, so editors can sync without Components.

### #31 Patch slots 64–127
The firmware's patch store has 128 slots (`0x2000`–`0x12000`, 512 bytes each). The boot scan, the save path, the load handler and the "used slot" mask all accept `< 0x80`. Only the patch view limits slots to 64: Oct ↓/↑ set page 0/1 via `FUN_08022fdc`. Details: `docs/sysex-spec.md` *Patch store*.
- **Hardware checks** (step-by-step plan: `docs/hw-test-patch-slots.md`):
  - dump `0x2000`/`0x10000` (cmd `0x78`) to see whether slots 64–127 hold data on a stock unit;
  - try a slot write, then a `0x40` request, to slot 64.
- **Feature candidate:** patch the page setter, the Oct LED logic and pad rendering to allow 4 pages, giving 128 patches. Depends on #17.
- **Program change 64–127 on the Osc 1 channel was expected to select these slots** (no 0–63 clamp found in the setter or the load handler; `docs/sysex-spec.md` *Patch store*). Hardware says otherwise: see test 4 below.
- **Hardware results (2026-10-25, stock unit, bootloader 740 / firmware 800; tests 0–3 of the plan, `~/ms-dumps/report.json`):**
  - The `0x78` dumps are served by the **bootloader** only; the app ignores them. `tools/hwtest_slots.py` reboots with `0x71` first, like Components.
  - The bootloader accepts `0x2000`/`0x10000`: a full 128-slot dump, header echoing that range, CRC ok. Slots 0–63 match the 64-slot backup.
  - **Slots 64–127 all hold `Initial Patch`, byte for byte.** There is no hidden factory or test bank (#8), and no `FF` slots, so the boot scan counts them as unused.
  - ~~PC 64 on ch 1 loads slot 64~~ **Corrected by test 4:** PC 64 gave the init sound and lit no pad, but slot 64 held `Initial Patch` too, so that didn't prove where it loaded from. PC 63 (control) loaded slot 63 and lit page 2's last pad.
  - **Test 4 (slot write, 2026-10-25):** a type-1 write put `Min 3rd` (a copy of slot 32) into flash slot 64, confirmed with `p 0x40` reads. Afterwards PC 64 still loaded `Initial Patch`, also after a power cycle, and so did PC 96, 104 and 127. PC 32 loaded `Min 3rd`, so the readback method works. **PC ≥ 64 loads the built-in `Initial Patch` template and doesn't read flash.** PC 96 and 104 rule out a wrap to 0–63 (slots 32 and 40 hold data). Slot 64 was restored to `Initial Patch` afterwards.
  - Where the template comes from isn't traced. The load handler `FUN_08019AE4` reads flash for any slot `< 0x80` on its port `+0x18`, but its port `+0x34` copies `Initial Patch` with no slot argument. The setter `FUN_0801C842` posts the program through a port chain (`FUN_08014D2E`) that wasn't resolved statically. So hidden slots can be written and dumped, but not loaded without a patch (#17).
  - Test 5: the `0x40` request answers only `40 00`, with the current patch (#12).
  - Second run: dumps of one slot, one session and `0x0`/`0x2000` all work, so the range is free-form. External flash above the sessions (`0x2E8000`–`0x1000000`) is all `FF`, so the factory patches aren't stored there (#8). The storage map is in `docs/session-transport.md`.

### #20 Expose SysEx-only parameters via CC/NRPN
Map parameters missing from the CC table (#14) to free CC or NRPN numbers.

### #21 Additional LFO waveforms / modulation options
The LFO is digital, so new shapes (S&H variants, random, ramp) are firmware-only additions. Feasibility depends on the LFO code (#10).

### #22 Sequencer enhancements
Candidates: per-step probability, ratchets, extra scales, and longer or chained patterns. Before deciding scope, measure the remaining RAM and flash headroom and check the session format constraints (#13).

### #23 Feature request intake
Collect the features the user actually wants and turn each one into an issue once #10 shows what is feasible. The first external list became #32–#42.

## Feature requests (external list, 2026-10-25)

From a third-party wish list, one issue per item. All of them depend on #17 (patching infrastructure) and on free RAM and flash. Anything that stores new per-step data has to fit the session format (#13, `docs/session-format.md`) or extend it in a way Components still accepts.

### #32 Micro-step note timing (off-grid notes, trills, ratchets)
Move notes off the 16-step grid and allow repeats within a step. The session already has a finer grid for automation: each lane holds 96 values, 16 steps × 6 micro-steps. Note steps have no such field yet. Check first whether the playback routine `FUN_080234AC` can schedule a note at micro-step resolution, and where a per-step offset or repeat count could live. *Found (`docs/sequencer.md`):* note-ons fire only when the step cell changes, but note-offs already run on a tick-resolution delta list (8 slots), which a delayed note-on or ratchet could share. Overlaps the ratchet idea in #22.

### #33 Unquantised recording
Record live notes at their real timing instead of snapping them to the nearest step. Needs #32's micro-step offset, or something like it, for storage. The live-input path into the recorder is mapped (`docs/sequencer.md` *Note routing*: keys and MIDI in reach `FUN_0802345C`, which forwards to per-track recorders built by `FUN_08021128`). Find what the recorders write; the path is reached from the Record button and documented in the user guide's *Recording a Pattern in real time*.

### #34 Step-time recording (SH-101 style)
Enter notes one after another from the keyboard pads, each one moving to the next step, with no transport running. A UI mode only. It writes the same step data as normal step editing, so the session format is unchanged.

### #35 Per-step probability and mutation (Launchpad Pro style)
Per-step trigger probability, plus Launchpad Pro-style mutation of a pattern's notes. Needs a per-step probability field (session format) and a random source at playback. The firmware already has a random generator for the random play direction: `FUN_0801AB9C` reads the hardware RNG at `0x50060800` (`docs/sequencer.md`). Same as the probability candidate in #22. The stock Mutate re-sequences the notes of a pattern (user guide p. 54).

### #36 Arpeggiator
The user guide documents no arpeggiator. Needs:
- a held-note buffer;
- arp modes (up, down, up/down, random, as played) and an octave range;
- a rate synced to the sequencer clock (24 PPQN tick cell, internal or MIDI-slaved: `docs/sequencer.md` *MIDI clock and transport*);
- a UI entry point (a Shift combination or a free button) and a home for its settings, in the patch's reserved bytes 27–40 or in the session.

### #37 Roll / looper performance effect
A momentary beat-repeat: while held, repeat the last 1/4, 1/8, … of the playback at the clock rate. It could be done by looping the sequencer's step position (`FUN_0801B698` computes it) instead of buffering audio, which avoids needing audio RAM.

### #38 Decouple the Osc 2 track from Osc 2
Let sequencer track 1 (the Osc 2 track, `docs/session-format.md`) drive only its MIDI output, so it can sequence an external synth without also playing oscillator 2. Probably a per-session or per-pattern flag that playback checks before passing track 1's notes to the synth engine. Find where track 1's notes reach Osc 2 and where they are sent to MIDI. *Found:* sequencer notes go through a note router and a per-track merger (vtable `0x08029F04`) whose outputs are MIDI out (`FUN_08018B48`), the synth engine (`FUN_080226BC`) and the recorder. The split point for #38 is the track-1 merger's stage 1 → stage 2 forward (`docs/sequencer.md` *Note routing*).

### #39 Randomise gate, glide and velocity patterns
A randomise action for each per-step property (gate length, glide, velocity) on its own, leaving the notes in place. This is unlike Mutate, which re-sequences the notes. It needs no new storage, only a UI action and a random fill of the existing step fields. The step layout (#13) must be known first.

### #40 Per-step modulation shapes on the mod sequencer
Choose a shape for each mod-sequencer step (step, ramp up/down, curve, …) instead of one global smooth on/off. Today byte 3 of a pattern is the smooth flag, and the mod-sequence output `FUN_08023998` crossfades from one step to the next with weight table `0x0802A286` (`docs/session-format.md`). A per-step shape needs storage, perhaps in unused bits of the mod step records, and more weight tables or a computed curve.

### #41 AFX mode / per-key overlays
Per-key parameter overlays, as on Bass Station II's AFX mode: each key recalls its own set of patch parameters. Components implements this only for Bass Station II (`frontend/constants/bass-station-ii/afx-overlay-midi-config`, `docs/bass-station-ii.md` *Overlays*), so there is nothing Mono Station-specific to reuse. Needs storage for one overlay per key and a hook in note-on that applies it. The biggest item on the list, so check RAM and flash headroom first.

### #42 Fast LFO mode (audio-rate)
An LFO range that reaches well into the audio band. LFO rate is an 8-bit parameter (0–255, CC(8); `tools/patchbank.py` `PARAMS`). A fast mode could be a flag that scales the rate. Questions:
- how often the LFO is updated (the control-rate tick);
- whether the destinations it modulates are applied at audio rate or per block.

Find the LFO code and its rate table first. Related to #21.
