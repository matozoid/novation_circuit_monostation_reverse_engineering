# Firmware source build (#43)

`tools/fwsrc.py` turns the stock app image into Thumb-2 assembly that GNU as/ld rebuild
**byte for byte**. It is the first step towards a full source tree: next comes splitting by
subsystem, then converting functions to C one at a time.

## Copyright rule

The firmware is Novation's code. Generated assembly (and later C) is **never checked in**.
Each user generates it locally from their own `firmware/*.syx`/`.bin` into `build/`
(git-ignored). The repo holds only the generator and metadata:

| File | What it is |
|------|------------|
| `tools/fwsrc.py` | generator (`generate`) and builder (`build`) |
| `tools/ghidra/ExportLayout.java` | Ghidra post-script that writes `src/layout.tsv` |
| `src/layout.tsv` | Ghidra's code/data map: addresses, kinds, data refs, function entries; no image bytes |
| `src/layout-fixes.tsv` | hand corrections applied over it (same line syntax) |
| `src/symbols.tsv` | `addr name [note]` names from `docs/*.md` (182: vectors, runtime, sequencer, recorder, MIDI, SysEx, vtables, tables) |

## Usage

    python3 tools/syx2bin.py firmware/monostation-firmware-800.syx firmware/monostation-firmware-800.bin
    python3 tools/fwsrc.py generate firmware/monostation-firmware-800.bin build/fwsrc
    python3 tools/fwsrc.py build build/fwsrc build/fwsrc/firmware.bin --compare firmware/monostation-firmware-800.bin

Output: `firmware.s` (one section `.text.image`) and `firmware.ld` (`. = 0x0800C000`).
Build flags: `-mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard`. Needs
`binutils-arm-none-eabi`. `tests/test_fwsrc.py` runs the round trip when the firmware is present.

To regenerate the layout after Ghidra changes:

    "$GHIDRA_HOME/support/analyzeHeadless" ghidra-project monostation -process monostation-firmware-800.bin \
      -noanalysis -scriptPath tools/ghidra -postScript ExportLayout.java "$PWD/src/layout.tsv"

## How the generator works

- **Layout.** `C` (instructions), `D` (defined data) and `U` (undefined) ranges tile the
  whole image (129,824 bytes). `layout-fixes.tsv` adds functions Ghidra missed, marks the
  `b .` fault loops as code, and turns falsely disassembled tables back into data (for
  example the synth param table at `0x0802A62E`).
- **Disassembly.** objdump runs on each `C` range (`-Mforce-thumb`, `--adjust-vma`).
- **PC-relative operands become labels:** branches (`b`/`bl`/`b<cond>`, `.n`/`.w`,
  `cbz`/`cbnz`), literal loads (`ldr*`, `vldr`, `ldrd`), and `add`/`sub pc` → `adr`.
  Labels: functions are `.global` + `.thumb_func`, named from `src/symbols.tsv` or Ghidra (`FUN_xxxxxxxx`),
  code labels are `.L_xxxxxxxx`, data labels are `D_xxxxxxxx`.
- **Probe assembly.** Every other instruction is assembled on its own, in IT groups, and
  kept as text only when its encoding matches the image. Otherwise it becomes
  `.inst.n`/`.inst.w`. On firmware 800, all 40,476 instructions are kept as text.
- **Pointer data.** Aligned data words whose value lies inside the image become
  `.word label` (1,444 words). An odd pointer to a function is the plain function symbol
  (thumb bit from `.thumb_func`). An even pointer to a function uses a local alias
  `.La_xxxxxxxx`, so the linker does not set bit 0.
- **Other data** is `.byte` rows, split at labels.

## Limits and next steps

- **Relocation.** Branch and literal targets are symbolic, but the file is still one
  section laid out at its original addresses. Moving code needs: per-subsystem files with
  global symbols, no fixed `.n` widths (so gas can relax branches), and a shift test that
  inserts padding and checks that every pointer and branch still resolves. Data words that
  only *look* like pointers (false positives) matter only once code moves.
- **Names.** `src/symbols.tsv` replaces Ghidra's `FUN_`/`D_` labels. A name doesn't make
  an address a function: function status comes from the layout (`F` lines). Each name must
  sit on an instruction, or on data outside a pointer word, and must be unique, or
  `generate` fails. Add a name when a doc identifies the function.
- **Features.** The generated tree is thrown away and regenerated, so features must live
  in checked-in overlay files (our own `.s`/`.c`) that replace named functions or add code.
  Until then, `tools/fwpatch.py` patches stay the way to change the firmware.
- **Toolchain hint.** newlib crti `_init`/`_fini` sit at `0x0802AEC8`/`0x0802AED4`, which
  points to arm-none-eabi-gcc. That matters for a matching C decompilation later.
