# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Don't commit, don't push. Let the user do this.

Ask the user to make decisions.

## Project state

Reverse-engineering project for the Novation Circuit Mono Station. Goal: reverse engineer the firmware and add new features to it. Work is tracked in `issues.md` (also holds established facts about the firmware container). There is no source code, build system, or test suite yet — only reference inputs. Tooling language: Python 3, stdlib only (in `tools/`).

- Run tests: `python3 -m unittest discover -s tests -v` (single test: `python3 -m unittest tests.test_fwsyx.Firmware800Test.test_vector_table`). Firmware tests skip when `firmware/monostation-firmware-800.syx` is absent; `firmware/` is git-ignored and must currently be filled by hand (#2).
- Decode firmware: `python3 tools/syx2bin.py firmware/monostation-firmware-800.syx firmware/monostation-firmware-800.bin` → raw Cortex-M image at `0x0800C000`. Container format: `docs/firmware-container.md`.
- Encode firmware: `python3 tools/bin2syx.py image.bin out.syx --version 800` (pads with `FF`, writes checksum; byte-exact on stock).

Firmware: Components bundles `monostation-firmware-800.syx` inside `app.asar` (Windows: installer → `Components-1.72.5-full.nupkg` → `lib/net45/resources/app.asar`; parse the asar JSON header to extract). It is 4059 SysEx msgs: `0x71` start, `0x72` data, `0x73` last data, `0x76` trailer.

Hardware: decoded image is a 129,824-byte Cortex-M4F app at load base `0x0800C000`; MCU is STM32F405-class (likely STM32F405VG, LQFP100). Memory map, flash sectors and the protected bootloader region (`0x08000000`–`0x0800BFFF`, not in the file) are in `docs/hardware.md`. Ghidra (12.x, headless, Java scripts — no Jython/PyGhidra needed): `GHIDRA_HOME=<ghidra> tools/ghidra/import_firmware.sh` imports + runs `MapMemory.java` (memory map). `ExportMap.java` writes `docs/ghidra-functions.txt` (467-function listing); `DecompileFunc.java <addr>…` prints decompiled C. Invoke post-scripts with `"$GHIDRA_HOME/support/analyzeHeadless" ghidra-project monostation -process monostation-firmware-800.bin -noanalysis -scriptPath tools/ghidra -postScript <Script>.java <args>`. DB in `ghidra-project/` is git-ignored. Subsystem map: `docs/firmware-map.md`, reproduced by `python3 tools/fwmap.py firmware/monostation-firmware-800.bin [xref_addr]` (vectors, peripheral literals, strings, literal-pool xrefs). Quick Thumb-2 disasm without Ghidra: `python3 tools/disasm.py firmware/monostation-firmware-800.bin <addr>` (needs capstone).

## Reference inputs (repo root)

| File | What it is |
|------|------------|
| `CircuitMonoStationPatchSysExFormat.pdf` | Community spec (rev. 2020-06-30, by "chaocrator") of the single-patch SysEx dump. Primary source of truth so far. |
| `Components-1.72.5-Setup.exe` / `Components-1.72.5-universal.dmg` | Novation Components editor/librarian (Windows / macOS). Likely contains the full SysEx protocol, patch parsing, and firmware update logic (Components is Electron/web-based — look for bundled JS). |
| `NovationUsbMidi_2.22.0.10_Installer.exe`, `novationusbmidi_2.30.0_84.exe` | Novation Windows USB-MIDI driver installers, two versions. |

These are large (~500 MB total), untrusted vendor binaries. Never execute them. Extract each into its own fresh directory outside the repo root (e.g. scratchpad) with tools like `7z`, `innoextract`, `dmg2img`/`7z`, `asar extract`, and analyse statically.

## Patch SysEx format

**Authoritative spec: `docs/sysex-spec.md`** (extracted from Components 1.72.5), which supersedes the community PDF. The modern current-patch message is **154 bytes**: a 9-byte header (bytes 0–3 `F0 00 20 29`, 4–5 device id, 6 protocol version = 6, 7 message type, 8 slot), a 144-byte payload, then `F7`. The payload is a 7→8-bit MIDI bit stream that unpacks to **126 data bytes**; the 85 parameters live at bytes 7–99. The PDF's "pseudo-MSB `0x40`" bytes were an artifact of not unpacking — after unpacking each param is a plain 8-bit byte decoded by its type (`DT_U`/`DT_U8`/`DT_S`−64/`DT_S8`−128/`DT_B` bit/`DT_M` mask).

- Machine-readable schema: `tools/monostation_patch_params.tsv` (name, patchIndex, type, range, CC/NRPN per param).
- Decode/encode: `python3 tools/patch.py decode patch.syx` and `python3 tools/patch.py selftest` (`tools/patch.py` codec matches Components' `OX` byte-exact).
- Still open (need a hardware dump): byte-exact validation, the message-type enum at byte 7, header bytes 4–5, and the name/category split in bytes 100–125. The legacy 122-byte message and the 8704-byte bank (64×136) are noted in the spec.

Extracting the schema: unpack `resources/app.asar` from Components (parse the 16-byte asar header for the JSON directory, then slice file data) and read `ember-dist/assets/main-*.js` — the Mono Station patch array is the `kD` literal; parse/pack helpers are `fromPatchSysex`/`RX`/`OX`/`LX`/`UX`.
