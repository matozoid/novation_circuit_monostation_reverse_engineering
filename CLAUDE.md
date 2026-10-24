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

Hardware: decoded image is a 129,824-byte Cortex-M4F app at load base `0x0800C000`; MCU is STM32F405-class (likely STM32F405VG, LQFP100). Memory map, flash sectors and the protected bootloader region (`0x08000000`–`0x0800BFFF`, not in the file) are in `docs/hardware.md`. Disassemble: `python3 tools/disasm.py firmware/monostation-firmware-800.bin 0x08025e61` (needs `pip install capstone`). Ghidra import: `GHIDRA_HOME=/opt/ghidra tools/ghidra/import_firmware.sh` (`tools/ghidra/map_memory.py` lays out the memory map; DB is git-ignored). Subsystem map: `docs/firmware-map.md`, reproduced by `python3 tools/fwmap.py firmware/monostation-firmware-800.bin [xref_addr]` (vectors, peripheral literals, strings, literal-pool xrefs).

## Reference inputs (repo root)

| File | What it is |
|------|------------|
| `CircuitMonoStationPatchSysExFormat.pdf` | Community spec (rev. 2020-06-30, by "chaocrator") of the single-patch SysEx dump. Primary source of truth so far. |
| `Components-1.72.5-Setup.exe` / `Components-1.72.5-universal.dmg` | Novation Components editor/librarian (Windows / macOS). Likely contains the full SysEx protocol, patch parsing, and firmware update logic (Components is Electron/web-based — look for bundled JS). |
| `NovationUsbMidi_2.22.0.10_Installer.exe`, `novationusbmidi_2.30.0_84.exe` | Novation Windows USB-MIDI driver installers, two versions. |

These are large (~500 MB total), untrusted vendor binaries. Never execute them. Extract each into its own fresh directory outside the repo root (e.g. scratchpad) with tools like `7z`, `innoextract`, `dmg2img`/`7z`, `asar extract`, and analyse statically.

## Patch SysEx format (from the PDF)

Total message 136 bytes (offsets 0–135):

- **0–8 header**: `F0 00 20 29 01 61 00 00 00` — F0 start, `00 20 29` Novation mfr ID, `01` product type (synth), `61` product (Circuit Mono Station), `00` command (replace current patch; other commands unexplored), `00` location (only one), `00` reserved.
- **9–24 patch name**: 16 ASCII chars, space-padded (`0x20`).
- **25–134 patch data**: offset 25 probably category; 27 env retrigger/legato; 41–59 osc1/osc2 (octave, waveform, PW, coarse, fine, range, sync); 61–72 mixer levels; 73–80 filter; 81–84 envelope; 85–90 LFO; 91–92 distortion; 93–133 mod matrix (env/lfo/seq/vel → osc1/2 pitch & PW, amp, cutoff, distortion, aux CV); 134 selected routing (0–31). Many offsets in 25–40 and 49, 58, 60 are unknown.
- **135**: `F7` end.

Non-obvious encoding: parameters with a 0–255 range are split into an "LSB" byte (0–127) followed by an "MSB" byte that is only `0x00` or `0x40` — not a true MSB. `0x40` marks values at/above centre (64). Bipolar params use `0x40` (64) as centre/zero. Offset 99 (env → amp) is a boolean despite sitting among 0–127 mod amounts.

The PDF's init-patch values (hex/dec columns) are useful as a known-good reference vector for parsers/encoders.
