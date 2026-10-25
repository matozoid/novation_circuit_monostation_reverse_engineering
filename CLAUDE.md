# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Don't commit, don't push. Let the user do this.

Ask the user to make decisions.

## Project state

Reverse-engineering project for the Novation Circuit Mono Station. Goal: reverse engineer the firmware and add new features to it. Work is tracked in `issues.md` (also holds established facts about the firmware container). There is no source code, build system, or test suite yet — only reference inputs. Tooling language: Python 3, stdlib only (in `tools/`). The one exception: tools that talk to the device (flasher #16, `tools/hwtest_slots.py`) use `mido` + `python-rtmidi`, imported inside `main()` so tests stay stdlib-only.

- Run tests: `python3 -m unittest discover -s tests -v` (single test: `python3 -m unittest tests.test_fwsyx.Firmware800Test.test_vector_table`). Firmware tests skip when `firmware/monostation-firmware-800.syx` is absent; `firmware/` is git-ignored and must currently be filled by hand (#2).
- Decode firmware: `python3 tools/syx2bin.py firmware/monostation-firmware-800.syx firmware/monostation-firmware-800.bin` → raw Cortex-M image at `0x0800C000`. Container format: `docs/firmware-container.md`.
- Encode firmware: `python3 tools/bin2syx.py image.bin out.syx --version 800` (pads with `FF`, writes checksum; byte-exact on stock).
- Flash firmware (#16): `.venv/bin/python tools/flash.py <firmware.syx>` checks the file, waits for a manual bootloader entry, sends with per-packet acks, then verifies the app's `0x70` version (`docs/update-protocol.md` *Our flasher*).
- Read memory (`p 0x40` op 2 only): `.venv/bin/python tools/p40dump.py START LEN OUT`. On stock it reads external flash; with `patches/memread` (v803) it also reads MCU memory, `DBGMCU_IDCODE` and SRAM3. Bootloader dump: `firmware/bootloader-740.bin` (`0x08000000`, #24).
- Bootloader 740 analysis (`docs/bootloader.md`): the button combo and backup-SRAM flag `2` keep it in the bootloader. No firmware checksum is verified. The max app is `0x0800C000`–`0x0807FFFF`. It starts a ~6 s IWDG, so patch code must not block. Import it into Ghidra with `tools/ghidra/import_firmware.sh firmware/bootloader-740.bin ghidra-project 0x08000000` (program `bootloader-740.bin`).
- Patch firmware (#17): `python3 tools/fwpatch.py patches/<name> firmware/monostation-firmware-800.bin out.bin --syx out.syx`. A patch is a dir with `patch.toml` (base, version, sources, `[functions]`, `[[hook]]` bl/b/word/bytes with mandatory `expect` bytes) plus `.s` files, linked after the image at `0x0802BB20`; read-only code, no `.data`/`.bss`. Needs `binutils-arm-none-eabi` (installed). Spec: `docs/patching.md`.

Firmware: Components bundles `monostation-firmware-800.syx` inside `app.asar` (Windows: installer → `Components-1.72.5-full.nupkg` → `lib/net45/resources/app.asar`; parse the asar JSON header to extract). It is 4059 SysEx msgs: `0x71` start, `0x72` data, `0x73` last data, `0x76` trailer.

Hardware: decoded image is a 129,824-byte Cortex-M4F app at load base `0x0800C000`; MCU is STM32F42x/43x (DEV_ID `0x419` read from the unit; `FLASHSIZE` 512 KB, RDP level 0, #24). Free RAM for patches: CCM `0x10000000`+ and SRAM3 `0x200235B8`–`0x2002FFFF` (`docs/hardware.md` *Free RAM*). Memory map, flash sectors and the protected bootloader region (`0x08000000`–`0x0800BFFF`, not in the file) are in `docs/hardware.md`. Ghidra (12.x, headless, Java scripts — no Jython/PyGhidra needed): `GHIDRA_HOME=<ghidra> tools/ghidra/import_firmware.sh` imports + runs `MapMemory.java` (memory map). `ExportMap.java` writes `docs/ghidra-functions.txt` (467-function listing); `DecompileFunc.java <addr>…` prints decompiled C; `DecompileAll.java <out.c>` dumps every function; `XRefs.java <addr>…` lists references; `MakeFunc.java <addr>…` creates functions auto-analysis missed (vtable targets such as playback `0x080234AC`) and decompiles them. Invoke post-scripts with `"$GHIDRA_HOME/support/analyzeHeadless" ghidra-project monostation -process monostation-firmware-800.bin -noanalysis -scriptPath tools/ghidra -postScript <Script>.java <args>`. DB in `ghidra-project/` is git-ignored. Subsystem map: `docs/firmware-map.md`, reproduced by `python3 tools/fwmap.py firmware/monostation-firmware-800.bin [xref_addr]` (vectors, peripheral literals, strings, literal-pool xrefs). Manual bootloader entry: hold Scales + Note + Velocity at power-on; Play exits (`docs/update-protocol.md`). App SysEx dispatcher `FUN_080202a0` (reboot `0x71`/`0x60`, version `0x70`, image header `MAIN`+BCD version at image offset `0x1AC`, external-flash `p 0x40` commands): `docs/sysex-handlers.md`. Quick Thumb-2 disasm without Ghidra: `python3 tools/disasm.py firmware/monostation-firmware-800.bin <addr>` (needs capstone).

## Reference inputs (repo root)

| File | What it is |
|------|------------|
| `CircuitMonoStationPatchSysExFormat.pdf` | Community spec (rev. 2020-06-30, by "chaocrator") of the single-patch SysEx dump. Offsets are right; its "MSB" bytes are the value's low bit (see `docs/sysex-spec.md`). |
| `circuit-mono-station-midi-parameters-v1.pdf` | Novation's official MIDI parameter list (v3, Aug 2017): CC / CC(8) / NRPN numbers, ranges, defaults, value tables. All 74 synth params match the firmware table at `0x0802A62A`. |
| `circuit-mono-station-ug-en-v1-2.pdf` | User guide v1.2. Bootloader Mode is on p. 90. |
| `Components-1.72.5-Setup.exe` / `Components-1.72.5-universal.dmg` | Novation Components editor/librarian (Windows / macOS). Likely contains the full SysEx protocol, patch parsing, and firmware update logic (Components is Electron/web-based — look for bundled JS). |
| `NovationUsbMidi_2.22.0.10_Installer.exe`, `novationusbmidi_2.30.0_84.exe` | Novation Windows USB-MIDI driver installers, two versions. |

These are large (~500 MB total), untrusted vendor binaries. Never execute them. Extract each into its own fresh directory outside the repo root (e.g. scratchpad) with tools like `7z`, `innoextract`, `dmg2img`/`7z`, `asar extract`, and analyse statically.

## Patch SysEx format

**Spec: `docs/sysex-spec.md`**, which supersedes the community PDF. The Mono Station has **one** patch format: the **136-byte** message `F0 00 20 29 01 61 <type> <slot> 00` + 126-byte patch + `F7`. It is used on the wire, in `.monostationpack` files (a ZIP holding `patches.syx`, a 64×136 = 8704-byte bank) and, without the framing, in firmware memory. Components' Mono Station validator accepts only 136 bytes. Message type: `0`=current patch, `1`=slot write, `0x40`=request. Mono Station device id is `01 61`; the original Circuit is `01 60`.

- Read a pack/bank: `python3 tools/patchbank.py list Roboo.monostationpack`; decode one patch's params: `python3 tools/patchbank.py show <pack> <slot|name>` (`tools/patchbank.py`: framing, name@9, category@25 / genre@26 (indices into `CATEGORIES`/`GENRES`, Components' Circuit tables), slot, and the synth param map `PARAMS`).
- Split-pair decode (resolved, #12): params the firmware sends as CC + LSB-CC (`CC(8)` in the MIDI PDF) take two bytes, `value = byte[off] << 1 | byte[off+1] >> 6`. So the `0x00`/`0x40` byte is the **low bit**, not `+128`. Signed ones subtract the firmware table's centre: 128 for coarse and pitch/cutoff mod depths, 100 for fine tune. `tests/test_patchbank.py` checks `PARAMS` against the firmware table at `0x0802A62A` and the PDF.
- Reference data: `Roboo.monostationpack` (a real 64-patch bank) and the firmware-embedded defaults `Classic Mono`@`0x08028868` / `Initial Patch`@`0x08028F6B`. The image holds only these templates, not a factory bank; Components' Mono Station init patch `pR` matches `Initial Patch` byte for byte.
- **Not Mono Station — don't mix in:** the 154-byte / 122-byte formats, Components' `kD` param array and its `RX`/`OX`/`UX` codec are **Bass Station II** (product id `00 33`; Components modules `bass-station-ii/*`). They are kept for reference in `docs/bass-station-ii.md`, `tools/bassstation2_patch.py` (`decode`/`selftest`) and `tools/bassstation2_params.tsv`. For Mono Station parameters and CCs, trust the firmware tables and `tools/patchbank.py` `PARAMS`.

Session SysEx dumps (`0x77` header / `0x79` 300-byte blocks / `0x7A` CRC-32 trailer; one transfer = 33 × 90,112-byte session images): `docs/session-transport.md`. List/split/rebuild with `python3 tools/sessionsyx.py list|split|build …` (byte-exact on the YBC dumps). The header field is a storage start address (sessions `0x12000`, patches `0x2000`), and a dump is requested with cmd `0x78` (`sessionsyx.request()`). Only the **bootloader** serves `0x78` (reboot into it with `F0 00 20 29 00 71 00 7F 00 00 F7`; its port is `Circuit Mono Station BL`; Play exits); on Linux read big dumps via rawmidi, not the ALSA sequencer. Session image layout (3 tracks of 16/8/8 patterns, automation lanes, one patch at `0x15F5C`): `docs/session-format.md`; `python3 tools/session.py info <image>`.

Extracting Components code: unpack `resources/app.asar` (parse the 16-byte asar header for the JSON directory, then slice file data) and read `ember-dist/assets/main-*.js`. Map a JS variable to its Ember module path by searching for `"frontend/...":<var>`, and check the path before attributing code to the Mono Station. Mono Station modules are `frontend/*/monostation/*`.
