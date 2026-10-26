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
| `tools/fwsrc.py` | generator (`generate`), builder (`build`) and shift test (`shift`) |
| `tools/ghidra/ExportLayout.java` | Ghidra post-script that writes `src/layout.tsv` |
| `src/layout.tsv` | Ghidra's code/data map: addresses, kinds, data refs, function entries; no image bytes |
| `src/layout-fixes.tsv` | hand corrections applied over it (same line syntax) |
| `src/symbols.tsv` | `addr name [note]` names from `docs/*.md` (183: vectors, runtime, sequencer, recorder, MIDI, SysEx, image header, vtables, tables) |

## Usage

    python3 tools/syx2bin.py firmware/monostation-firmware-800.syx firmware/monostation-firmware-800.bin
    python3 tools/fwsrc.py generate firmware/monostation-firmware-800.bin build/fwsrc
    python3 tools/fwsrc.py build build/fwsrc build/fwsrc/firmware.bin --compare firmware/monostation-firmware-800.bin

Output: `firmware.s` (one section `.text.image`) and `firmware.ld` (`. = 0x0800C000`, plus
an `ASSERT` that `image_header` stays at image offset `0x1AC`, where the bootloader reads
`MAIN` + version). `generate --relax` drops branch widths so gas picks them; it is also
byte-identical on stock and is what code that moves needs (see *Relocation*).
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
- **Jump tables.** The entries after a `tbb`/`tbh` become `.byte`/`.short (target - base) / 2`
  as GCC emits them, until an entry is not a forward target in code (the pad byte stays a
  number). 29 tables.
- **Alignment.** A data unit after code gets `.p2align 2` (4-aligned at stock) or replaces
  the compiler's `00 bf` nop pad with it. Named data (`symbols.tsv`) is `.global`, `%object`.
- **Other data** is `.byte` rows, split at labels.

## Relocation

With `--relax`, a branch has no `.n`/`.w`, so gas widens it when its target moves out of
range. Exceptions, each needed for a byte-identical stock build:
- A 32-bit `b`/`b<cond>` that a 16-bit one would reach keeps `.w`. Those are tail calls the
  linker resolved, and gas would choose narrow.
- A branch to a function uses the local alias `.La_xxxxxxxx`: gas always makes a relaxed
  branch to a global symbol wide (it leaves a relocation), e.g. the self-loop `b .` handlers.
- Literal loads and `adr` keep their width (`.n` added to narrow ones). Gas never shrinks
  a relaxed instruction, and its first estimates in one big file widen some loads. A narrow
  load whose pool moves out of range is an assembler error, not a silent change.

Without `--relax` an insert that pushes a narrow branch out of range fails to assemble
("branch out of range").

**Shift test.** `fwsrc.py shift IMAGE --pad ADDR:N [--pad …] [--relax] [--keep DIR]`
inserts N bytes (`.space`) before the line at ADDR, labels every line `__m_<stock address>`,
builds, and reads the old→new map from `nm`. It then checks every line of the moved image:
- non-PC-relative instructions keep their bytes;
- branches, literal loads and `adr` keep their form and point to the moved target;
- jump tables still follow their `tbb`/`tbh`, and entries decode to moved targets;
- pointer words hold the moved address (thumb bit kept) and stay aligned;
- other data keeps its bytes.

A pad at an address where no line starts is an error. Data keeps its stock alignment after
an insert: from a pad until the next `.p2align 2`, every data unit gets `.p2align 2`/`1` for
its stock alignment, and code after data gets `.p2align 1`. At stock addresses all these
directives add no bytes. `tests/test_fwsrc.py` runs a shift with a 2-byte insert at `main`,
4094 bytes inside `clock_engine` and 1 byte inside the param table, checks that a planted
wrong pointer is caught, and that an insert before `0x1AC` fails to link. The checker sees
lines, not meaning: an insert inside a data structure (a table, the header) passes.

## Limits and next steps

- **Relocation.** The shift test passes, so code can move inside the one file. Still to
  come: per-subsystem files with global symbols (cross-file even-pointer aliases; tail calls
  across files are wide anyway). Data words that only *look* like pointers (false positives)
  would be rewritten wrongly when code moves; pointers at unaligned offsets in packed data
  are not found at all.
- **Names.** `src/symbols.tsv` replaces Ghidra's `FUN_`/`D_` labels. A name doesn't make
  an address a function: function status comes from the layout (`F` lines). Each name must
  sit on an instruction, or on data outside a pointer word, and must be unique, or
  `generate` fails. Add a name when a doc identifies the function.
- **Features.** The generated tree is thrown away and regenerated, so features must live
  in checked-in overlay files (our own `.s`/`.c`) that replace named functions or add code.
  Until then, `tools/fwpatch.py` patches stay the way to change the firmware.
- **Toolchain hint.** newlib crti `_init`/`_fini` sit at `0x0802AEC8`/`0x0802AED4`, which
  points to arm-none-eabi-gcc. That matters for a matching C decompilation later.
