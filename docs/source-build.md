# Firmware source build (#43)

`tools/fwsrc.py` turns the stock app image into Thumb-2 assembly that GNU as/ld rebuild
**byte for byte**, split into files by `src/files.tsv`. It is the first step towards a full
source tree: next come more file ranges, then converting functions to C one at a time.

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
| `src/files.tsv` | `start end name [evidence]` address ranges, one source file each (see *Files*) |
| `overlays/<name>/` | our own code: `overlay.toml` plus `.s`/`.c` files that replace stock functions (see *Overlays*) |
| `overlays/include/firmware.h` | prototypes of stock functions for C overlays |
| `src/symbols.tsv` | `addr name [note]` names from `docs/*.md` (895: every function, plus vectors, vtables and tables; `docs/source-map.md` gives each function's purpose) |

## Usage

    python3 tools/syx2bin.py firmware/monostation-firmware-800.syx firmware/monostation-firmware-800.bin
    python3 tools/fwsrc.py generate firmware/monostation-firmware-800.bin build/fwsrc
    python3 tools/fwsrc.py build build/fwsrc build/fwsrc/firmware.bin --compare firmware/monostation-firmware-800.bin

Output: one `<name>.s` per file (section `.text.<name>`) and `firmware.ld`
(`. = 0x0800C000`, the sections in address order, plus an `ASSERT` that `image_header`
stays at image offset `0x1AC`, where the bootloader reads `MAIN` + version). `generate`
deletes `.s` files from an earlier run (first line `@ Generated …`). `--files PATH` (on
`generate` and `shift`) uses another file list. `generate --relax` drops branch widths so gas picks them; it is also
byte-identical on stock and is what code that moves needs (see *Relocation*).
Build flags (`--fatal-warnings`): `-mcpu=cortex-m4 -mthumb -mfpu=fpv4-sp-d16 -mfloat-abi=hard`. Needs
`binutils-arm-none-eabi`. `tests/test_fwsrc.py` runs the round trip when the firmware is present.

## Overlays

An overlay is checked-in code of ours that replaces stock functions by name. It works on
the generated tree, so features stay out of the generated source:

    python3 tools/fwsrc.py generate firmware/monostation-firmware-800.bin build/fwsrc --overlay overlays/hello
    python3 tools/fwsrc.py build build/fwsrc build/fwsrc/firmware.bin
    python3 tools/bin2syx.py build/fwsrc/firmware.bin out.syx --version 801

`overlays/<name>/overlay.toml`:

| Key | Meaning |
|-----|---------|
| `base` | stock version the overlay is written for; must match the image header (800) |
| `version` | optional: the BCD version written into the image header (`0x70` reply) |
| `sources` | `.s` and `.c` files in the overlay directory |
| `replace` | stock function names (from `src/symbols.tsv`) the overlay defines |

**Redirect, not move.** A replaced function's stock body stays at its address, renamed
`stock_<name>`, so an overlay can wrap it (`b.w stock_<name>` for a tail call). The name
goes to the overlay: every `bl`, wide `b`/`b<cond>` and pointer word (vtables, vectors)
outside the stock body now names the overlay function, and nothing else in the image
changes. References from inside the stock body (loops back to its entry) stay with it.
A narrow branch, `cbz` or PC-relative load of a replaced entry cannot reach the code after
the image, and `generate` fails with `cannot redirect to the overlay: <address> …`. On
firmware 800 no function is reached that way, so every function is replaceable. Not
detected: code that falls through into a replaced entry without a branch.

`generate` copies the sources into the tree as `zz_overlay_<overlay>_<file>` (first line
`@ Copied by …` or `/* Copied by … */`, removed on the next run like generated files; C
copies start with `#line 1 "<original>"`, so errors name the original) and writes
`overlays.txt` (`replace NAME` and `include DIR` lines). `firmware.ld` then links all
non-stock `.text`/`.rodata` after the image (stock sections exclude `zz_overlay_*`
objects: gcc puts `main` in `.text.startup`, the section of the stock `startup` file) (`overlay_start`, 4-aligned; 0x0802BB20 on 800) and asserts there is no
`.data`/`.bss` (the stock startup neither copies nor clears it for overlays) and that the
code ends below `0x08080000`. `build` checks with `readelf` that each replaced name is a
`.thumb_func` in the overlay code: a plain label would turn `bl` into `blx` to ARM state.
`--overlay` can be repeated; two overlays may not replace the same name or set different
versions. Overlay code is ours and is checked in.

**C overlays.** `build` compiles `.c` with `arm-none-eabi-gcc` (`gcc-arm-none-eabi`), the
CPU flags above and `-std=gnu17 -Os -ffreestanding -fno-common -Wall -Wextra
-Werror=implicit-function-declaration`, no unwind tables, with `-I overlays/include` and
`-I` each overlay directory. Every stock name is a global in the generated tree, so a
declaration (in `firmware.h` or the overlay) is enough to call it; argument types come
from `docs/source-map.md` and the decompiles. libgcc is linked last, so helpers gcc calls
(64-bit division, double arithmetic) come from it only when the image lacks them. No
`.data`/`.bss`: use `const` data or the free RAM in `docs/hardware.md`.

`overlays/hello` is the source twin of `patches/hello` (`main` wrapped by a tail call,
version 801) and `overlays/hello_c` the same in C (`void main(void) { stock_main(); }`,
which gcc makes a `b.w` tail call). `tests/test_fwsrc.py` checks that all three produce the
same image, the one already run on the device, and that a C `static` variable fails to link.

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

## Files

`src/files.tsv` lists `start end name` ranges `[start, end)`. Ranges it leaves out become
`gap_<start>.s`. The checked-in list names what the image itself shows; the image holds no
source-file names:

| File | Range | Evidence |
|------|-------|----------|
| `vectors` | `0800C000`–`0800C1AC` | vector table, 107 words up to the image header |
| `image_header` | `0800C1AC`–`0800C1D0` | `MAIN` + BCD version, read by the bootloader |
| `crtbegin` | `0800C1D0`–`0800C230` | `__do_global_dtors_aux`, `frame_dummy` (the `.fini`/`.init_array` entries) |
| `syscalls` | `0801EEAC`–`0801EEFC` | newlib stubs `_read` … `_close` |
| `stm32f4xx_it` | `08023A78`–`08024070` | IRQ and fault handlers, in ST's `stm32f4xx_it.c` order reversed (`NMI_Handler` last) |
| app files | `0800C230`–`08025E60` (60 ranges) | app code between linker input sections: each starts after a `00 00` linker fill that follows code (see below); named for their content, listed in `docs/source-map.md` |
| `startup` | `08025E60`–`08025EAC` | `Reset_Handler` + `Default_Handler` (CMSIS `startup_stm32f4xx.s`) |
| `libstdcxx_*`, `libc_*` | `08025EAC`–`08026D94` | one file per library object (`docs/firmware-map.md` *Runtime libraries*) |
| `rodata` | `08026D94`–`0802AE30` | app constant data; `D_08026D94` is read by app code |
| `rodata_libc_*` | `0802AE30`–`0802AEC8` | `_global_impure_ptr`, `__sf_fake_*`, printf strings |
| `crti` | `0802AEC8`–`0802AEE0` | `_init`, `_fini` (newlib crti/crtn) |
| `init_array` / `fini_array` | `0802AEE0`–`0802AEEC` | `frame_dummy` + one static constructor (`0x080256F4`); `__do_global_dtors_aux` |
| `data` | `0802AEEC`–`0802BAA0` | `.data` load image (`_sidata`); `Reset_Handler` copies `0x0802AEEC`–`0x0802BB04` to `0x20000000`–`0x20000C18` |
| `data_libc_impure` | `0802BAA0`–`0802BB04` | `impure_data` and `_impure_ptr` at the end of `.data` |
| `image_tail` | `0802BB04`–`0802BB20` | 28 unexplained bytes up to the image end |

`.bss` runs to `0x200035B8`. `Reset_Handler` calls `0x0801A848`, then
`0x08025EE0` (`__libc_init_array`), then `main`.

Rules, checked by `generate`:
- A file starts at an instruction or at a layout unit. Undefined (`U`) units are split where a file starts inside them.
- Only wide (32-bit) branches and data words may cross files. A narrow branch, a literal
  load, `adr` or a jump table that would cross is an error ("reference across files"):
  those are resolved by the assembler, so they must stay in one object. On firmware 800 no
  narrow branch crosses a function boundary, but 8 literal loads read a neighbouring
  function's pool.
- A local label (`.L_x`, `.La_x`) that another file uses gets a `.global` twin without the
  dot (`L_x`, `La_x`). The other file refers to the twin; the file itself keeps the local
  name, because gas makes a relaxed branch to a global symbol wide.
- **Alignment.** A section is aligned to its most aligned `.p2align`, and ARM gas pads the
  section's size to that alignment. So a file whose data needs 4-alignment, or whose code
  has a literal load or `adr` (gas warns about `Align(PC, 4)` in a less aligned section),
  must start and end 4-aligned; otherwise `generate` fails. Code-only files need 2. This is
  also a clue to the real compile units: an object with a literal pool starts and ends
  4-aligned. Another clue: `stm32f4xx_it.c`'s handlers appear in reverse source order,
  so GCC probably emitted every unit's functions last to first.
- **Input sections.** The linker fills the gap before a 4-aligned input section with
  `00 00`; gas pads inside a section with `00 bf` (`nop`). 58 app functions follow code
  plus a `00 00` fill, so each starts a new input section and an app file; the other two
  app files start after `crtbegin` and `stm32f4xx_it`. 4 follow a `00 bf` pad
  (`main`, `0x080114C0`, `recorder_on_cell`, `session_ctl_patch_write`), so one section
  holds several functions: the app was built without `-ffunction-sections`, and an
  input section is a whole compile unit. A boundary into a 2-aligned section, or at an
  address already 4-aligned, leaves no pad, so an app file can hold several units. The
  first, `app_main` (`0x0800C230`), runs 25.6 KB to `0x0801264C` with no pad.

A split into 829 files, one per function where these rules allow, also builds
byte-identical in both modes and passes the shift test.

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

- **Relocation.** The shift test passes across files, so code can move. Data words that only *look* like pointers (false positives)
  would be rewritten wrongly when code moves; pointers at unaligned offsets in packed data
  are not found at all.
- **Compile units.** The app files cover the whole app code, but are bounded only
  where the linker left a pad. Finer splits need other evidence: call clusters, vtables,
  HAL driver order.
- **Names.** `src/symbols.tsv` replaces Ghidra's `FUN_`/`D_` labels. A name doesn't make
  an address a function: function status comes from the layout (`F` lines). Each name must
  sit on an instruction, or on data outside a pointer word, and must be unique, or
  `generate` fails. Every function has a name and a line in `docs/source-map.md`;
  `python3 tools/fwsrc.py coverage [--list]` counts both, and a test keeps them complete.
  Rename in both files together.
- **Features.** The generated tree is thrown away and regenerated, so features live in
  overlays (see *Overlays*), in `.s` or C. `firmware.h` holds few prototypes yet; add them
  as arguments are pinned down.
- **Toolchain hint.** newlib crti `_init`/`_fini` sit at `0x0802AEC8`/`0x0802AED4`, which
  points to arm-none-eabi-gcc. That matters for a matching C decompilation later.
