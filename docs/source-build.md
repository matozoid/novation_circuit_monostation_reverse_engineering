# Firmware source build (#59)

`tools/fwsrc.py` turns the stock app image into Thumb-2 assembly that GNU as/ld rebuild
**byte for byte**, split into files by `src/files.tsv`. It is the first step towards a full
source tree. The goal is **full C++ source**, converted one function at a time, each one
proved by compiling to the stock bytes (*Matching*).

## Copyright rule

The firmware is Novation's code. Generated assembly and converted C/C++ are **never
checked in** (the repo is public): converted functions live in a git-ignored directory.
Each user generates it locally from their own `private/firmware/*.syx`/`.bin` into `build/`
(git-ignored). The repo holds only the generator and metadata:

| File | What it is |
|------|------------|
| `tools/fwsrc.py` | generator (`generate`), builder (`build`), shift test (`shift`) and function matcher (`match`) |
| `tools/fetch_toolchain.py` | installs the stock compiler (GCC 5.3.1) into git-ignored `toolchains/` for `match` |
| `tools/ghidra/ExportLayout.java` | Ghidra post-script that writes `src/layout.tsv` |
| `src/layout.tsv` | Ghidra's code/data map: addresses, kinds, data refs, function entries; no image bytes |
| `src/layout-fixes.tsv` | hand corrections applied over it (same line syntax) |
| `src/files.tsv` | `start end name [evidence]` address ranges, one source file each (see *Files*) |
| `overlays/<name>/` | our own code: `overlay.toml` plus `.s`/`.c`/`.cpp` files that replace stock functions (see *Overlays*) |
| `overlays/include/firmware.h` | prototypes of stock functions for C and C++ overlays |
| `src/symbols.tsv` | `addr name [note]` names from `docs/*.md` (895: every function, plus vectors, vtables and tables; `docs/source-map.md` gives each function's purpose) |

## Usage

    python3 tools/syx2bin.py private/firmware/monostation-firmware-800.syx private/firmware/monostation-firmware-800.bin
    python3 tools/fwsrc.py generate private/firmware/monostation-firmware-800.bin build/fwsrc
    python3 tools/fwsrc.py build build/fwsrc build/fwsrc/firmware.bin --compare private/firmware/monostation-firmware-800.bin

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

    python3 tools/fwsrc.py generate private/firmware/monostation-firmware-800.bin build/fwsrc --overlay overlays/hello
    python3 tools/fwsrc.py build build/fwsrc build/fwsrc/firmware.bin
    python3 tools/bin2syx.py build/fwsrc/firmware.bin out.syx --version 801

`overlays/<name>/overlay.toml`:

| Key | Meaning |
|-----|---------|
| `base` | stock version the overlay is written for; must match the image header (800) |
| `version` | optional: the BCD version written into the image header (`0x70` reply) |
| `sources` | `.s`, `.c` and `.cpp` files in the overlay directory |
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
`--overlay` can be repeated; two overlays may not replace the same name. Overlays that set
different versions need `generate --version N`, which sets the header version for the
build and overrides the overlays' own. Overlay code is ours and is checked in.

**C overlays.** `build` compiles `.c` with `arm-none-eabi-gcc` (`gcc-arm-none-eabi`), the
CPU flags above and `-std=gnu17 -Os -ffreestanding -fno-common -Wall -Wextra
-Werror=implicit-function-declaration`, no unwind tables, with `-I overlays/include` and
`-I` each overlay directory. Every stock name is a global in the generated tree, so a
declaration (in `firmware.h` or the overlay) is enough to call it; argument types come
from `docs/source-map.md` and the decompiles. libgcc is linked last, so helpers gcc calls
(64-bit division, double arithmetic) come from it only when the image lacks them. No
`.data`/`.bss`: use `const` data or the free RAM in `docs/hardware.md`.

**C++ overlays.** `build` compiles `.cpp` with `arm-none-eabi-g++` (shipped in the same
`gcc-arm-none-eabi` package) and `-std=gnu++17 -Os -ffreestanding -fno-exceptions -fno-rtti
-fno-threadsafe-statics -fno-use-cxa-atexit -Wall -Wextra`, no unwind tables. The stock app
is C++ built with GCC the same way: its vtables have 0 RTTI words and newlib's
`_init`/`_fini` are in the image. `firmware.h` wraps its declarations in `extern "C"`, so a
replaced function is an `extern "C"` function with the stock name. It can call a class
method non-virtually (`self->Class::method()`). A class that describes a stock object
needs the stock field offsets (check them with `static_assert(offsetof(...))`). Define its
virtual methods inside the class: then g++ emits no vtable of its own, and the stock
constructor's vtable stays in use. The overlay has no C++ runtime:
- **No static constructors or destructors:** `firmware.ld` puts `.init_array`/`.ctors`
  in a section that must stay empty ("overlays cannot have static constructors or
  destructors"). Nothing would run them.
- **No virtual destructors:** they need `operator delete`, which fails to link.
- **Nothing that needs the C++ runtime:** no `new`, no exceptions, no `typeid` or
  `dynamic_cast`.

`overlays/notesink_cpp` (version 802) is the proof. It rewrites `NoteSink` (vtable
`0x08027980`, `docs/class-diagram.md`) as a C++ class that implements `INoteSink`. Only the
version and the vtable's two slots change, and g++ compiles `note_sink_note_on` to exactly
the stock instructions.

`overlays/hello` is the source twin of `patches/hello` (`main` wrapped by a tail call,
version 801) and `overlays/hello_c` the same in C (`void main(void) { stock_main(); }`,
which gcc makes a `b.w` tail call). `tests/test_fwsrc.py` checks that all three produce the
same image, the one already run on the device, and that a C `static` variable fails to link.
They also check that a C++ static constructor fails to link.

`overlays/randomise` (#13, version 750: below stock 800, so Components offers to restore 1.2) is the first feature: it wraps `ui_button_event`
so that Mutate in the Velocity, Gate or Glide view randomises only that step field, and
in the Expand view only the notes' pitch. The
only stock bytes it changes are the version and the `ui_root` vtable's button-down slot
(`0x08027B24`).

To regenerate the layout after Ghidra changes:

    "$GHIDRA_HOME/support/analyzeHeadless" ghidra-project monostation -process monostation-firmware-800.bin \
      -noanalysis -scriptPath tools/ghidra -postScript ExportLayout.java "$PWD/src/layout.tsv"

## Matching

`match` checks converted functions without building the image:

    python3 tools/fwsrc.py match private/firmware/monostation-firmware-800.bin private/cpp/*.cpp --diff

It compiles each `.c`/`.cpp` with the overlay flags plus `-ffunction-sections
-fdata-sections -fno-reorder-functions`, so every function gets its own `.text.<name>`
section. Then it links each function whose name is a stock function (`src/symbols.tsv`)
at that function's stock address. Every other stock name is `PROVIDE`d at its stock
address (functions with the Thumb bit), so calls, tail calls and literal-pool pointers
resolve as in the image. Anything else (non-stock functions, `.rodata`, vtables g++
emits) is parked at `0x08080000`, so a reference to it shows up as a difference.
A data object whose name is a stock name (for example a vtable,
`extern "C" void *const vt_osc_view[20] = {0};`) is placed at its stock address. Its
contents are not compared. Stock constructors load `vt + 8` through a section anchor on
a vtable defined in the same translation unit, so they match only when the source
defines the vtable (`osc_view_ctor`, `gate_view_ctor`, `octave_view_ctor`).

A function's compiled bytes, literal pool included, are compared with the image:

| Status | Meaning |
|--------|---------|
| `MATCH` | the bytes are the stock bytes and reach the end of the stock code |
| `SHORT` | the bytes agree but stop before the stock code ends |
| `DIFF` | a byte differs; `--diff` prints stock and compiled instructions side by side |
| `EXTRA` | the function's name is not a stock function, so it is not placed |

A C++ method gets its stock name with an asm label: `int get() asm("cell_read");`. The
exit code is 1 when any function is `DIFF` or `SHORT`. A function ends at the next
function entry or named code address (`symbols.tsv`), so a this-adjusting thunk that
Ghidra folded into the function before it still ends that function. A function compiled
longer than its stock slot overlaps the next one; `match` links with
`--no-check-sections`, so it still reports the difference. Parameter types
change code generation: `recorder_write_patch_change` matches only with a `uint8_t`
pattern index (`mla r1, r1, r0, r4` instead of `mla r1, r0, r1, r4`).

**Unnamed RAM.** A RAM address with no stock name can be given one in the source:
`asm(".set usb_str_desc, 0x20002517"); extern uint8_t usb_str_desc[];`. The compiler
then treats it as an unknown address and loads it from one literal, as stock code does.
A constant cast such as `((uint8_t *)0x20002517)` lets the compiler fold offsets, so
`p[1]` gets a second literal `0x20002518` (`USBD_GetString` matches only with the
symbol).

**Specialised library code.** The STM32 Standard Peripheral Library functions match
their published source, with changes that look like a link-time optimiser specialising
each function for its callers:
- `NVIC_Init` has no `NVIC_IRQChannelCmd` check and no subpriority.
- `TIM_OC2Init` ignores `TIM_OCPolarity`.
- `TIM_DeInit` inlines `RCC_APBxPeriphResetCmd(p, ENABLE)` and tail-calls separate
  `DISABLE` copies (`RCC_APB1PeriphResetCmd_off`, `RCC_APB2PeriphResetCmd_off`).

To match, the source is written in the specialised form. The init-struct enums load
with `ldrb`, so they are one byte. `GPIO_Init` matches only when they are enum types
(`enum __attribute__((packed))`), not `uint8_t`. A `uint8_t` field may alias the
register stores, so GCC loads `GPIO_Mode` twice.

**Not GCC.** The vtable stubs at `080114F6`–`08011515` (`return_3`, `return_1`,
`return_0`, `mode_view_layer`) and `return_1_b` (`08015ABE`) return constants with a
32-bit `mov.w r0, #n`. `mode_view_layer` also compares with `cmp #3; ite ls`. GCC 5.3.1
gives `movs` at every `-O` level, and `ite cc` or `ite hi`. These stubs probably come
from another compiler or from hand-written assembly. Until that is known, they stay
assembly.

**Compiler version.** With `-Os`, arm-none-eabi-g++ 13.2 compiles `NoteSink::note_on` to
the stock bytes, but `note_off` gets the same instructions with the registers allocated
differently (`ldr r3, [r0, #8]` first in stock, `mov r3, r0` first in 13.2), whatever
the source wording. A full match needs the GCC version the stock was built with.

That version is **GNU Tools for ARM Embedded Processors 5-2016-q1-update** (GCC 5.3.1
20160307, `gcc-arm-none-eabi-5_3-2016q1`). We searched the image for the newlib-nano
`.text` sections (relocations masked) of the `thumb/v7e-m/fpv4-sp/hard` multilib from
nine releases, 4.9-2015q3 through 7-2018q2. Only 5_3-2016q1 matches all of them:
5_2-2015q4 lacks its `__swsetup_r`, `__swhatbuf_r`, `__smakebuf_r` and `__sputc_r`, and
both 5_4 releases lack its `_malloc_r` and `_free_r`. GCC 6 and 7 match far fewer bytes.
With this compiler both NoteSink functions match. `python3 tools/fetch_toolchain.py`
downloads it from Launchpad
(`https://launchpad.net/gcc-arm-embedded/5.0/5-2016-q1-update`, Linux x86-64), checks
Launchpad's MD5 and unpacks it into git-ignored `toolchains/`; `match` then uses it by
default (else `arm-none-eabi-` from PATH, with a note). `--cc PREFIX` picks another.

## Library code

The C runtime, newlib-nano and libstdc++ code in the image is not converted. `fwsrc.py
build` links it from the stock toolchain's own objects, so a later toolchain or library
version can replace it. `src/libraries.tsv` lists, for each library file in `files.tsv`,
the input sections it is made of, in image order:

```
file libc_findfp   libc_nano.a:lib_a-findfp.o(.text._cleanup_r .text.std.isra.0 ...)
file data_libc_impure@20000bb4  libc_nano.a:lib_a-impure.o(.data.impure_data .data._impure_ptr)
at 200035a8        libstdc++_nano.a:new_handler.o(.bss._ZN12_GLOBAL__N_113__new_handlerE) ...
symbol __init_array_start  init_array_crtbegin
```

- An input is `OBJECT(SECTION ...)`, where OBJECT is `crtbegin.o`, `crti.o`, `crtn.o` or
  `ARCHIVE:MEMBER`. The build finds each with the stock gcc's `-print-file-name` for the
  `armv7e-m/fpu` multilib (`-mcpu=cortex-m4 -mfpu=fpv4-sp-d16 -mfloat-abi=hard`). Without
  the stock toolchain it stops and names `tools/fetch_toolchain.py`.
- `file NAME@VMA`: the sections run from RAM at VMA. NAME is their load image.
- `at ADDR`: `.bss` sections, placed at ADDR without bytes in the image.
- `symbol`: a linker symbol at a file's start (`__libc_init_array`'s bounds) or at an
  address.
- Every other section of a listed object is discarded, as the stock link's `--gc-sections`
  did. For example, crtbegin's `.data` (`__dso_handle`) is unreferenced and not in the
  image. A kept section that needs a discarded one fails the link.

The linker script keeps one `KEEP` per section, because ld keeps file order within one
`file(sec1 sec2)` pattern. Two layout rules are easy to miss:
- ld merges string sections (`.rodata.str1.1`) across an output section. Each library
  file with strings therefore gets its own output section: `rodata_libc_impure`'s `"C"`
  stays apart from printf's strings.
- A file that runs from RAM ends the flash output section. The next section starts after
  that file's load image (`LOADADDR + SIZEOF`, a forward reference), so `.` never moves
  backwards.

App code reaches library code through the library's own symbol names, so `symbols.tsv`
uses those names: `memcpy`, `_ZnwjRKSt9nothrow_t` (`operator new(size_t, const
std::nothrow_t &)`) and `_ZSt7nothrow`. The stock operator new is the nothrow variant.
The shift test moves library files as blocks. It finds their new addresses through the
ELF's symbols, because a section's alignment can put it after the file's marker.
`generate --libraries none.tsv` (a missing file) generates the library files from the
image instead.

## ST libraries

ST's code in the image is linked from ST's source in the same way, compiled by `build`.
Two ST packages are used:

- **STM32F4xx Standard Peripheral Library V1.8.0** (STSW-STM32065). It provides
  CMSIS, the `stm32f4xx.h` device header and the StdPeriph drivers (DMA, GPIO, RCC, TIM).
  V1.9.0 is excluded because its GPIO driver writes a 32-bit `BSRR`, while the image
  uses the 16-bit `BSRRL`/`BSRRH` halves.
- **STM32 USB OTG host/device library V2.2.1** (STSW-STM32046): `usb_core.c`,
  `usb_dcd.c`, `usbd_core.c`, `usbd_ioreq.c`, `usbd_req.c`.

st.com requires a login to download, so the user puts the zips in `toolchains/`.
`tools/fetch_toolchain.py` checks each zip against a pinned SHA-256, unpacks it there and
marks it with `.installed`. `--archive FILE` takes a zip from elsewhere. ST files are
never committed: the git-ignored `toolchains/` holds them.

`src/libraries.tsv` adds three line kinds:

```
source usb_core.o  usb/Libraries/STM32_USB_OTG_Driver/src/usb_core.c
cflag  -DUSE_USB_OTG_FS
cflag  -Iusb/Libraries/STM32_USB_OTG_Driver/inc
file   usb_otg_corereset  usb_core.o(.text.USB_OTG_CoreReset.isra.1)
export usb_core.o  USB_OTG_CoreReset.isra.1  USB_OTG_CoreReset
```

- `source OBJECT PACKAGE/PATH`: the build compiles PATH, a file in the installed
  `stdperiph` or `usb` package, into `build/.../st/OBJECT`. A `file` input can then name
  OBJECT. The compiler is the stock one, with `-Os -ffunction-sections -fdata-sections`,
  the CPU flags, `-I src/st` and every `cflag`.
- `cflag`: a `-D`, `-I`, `-f`, `-m` or `-O` flag. A `-I PACKAGE/PATH` resolves into the
  package.
- `export OBJECT SYMBOL [NAME]`: objcopy makes a static symbol global, renamed to NAME
  if one is given. Generated data refers to these by name. For example, the DCD callback
  table holds `.word USBD_SOF`, and a linker-script symbol would lack the Thumb bit.

The configuration headers are ours, in `src/st/`: `stm32f4xx_conf.h`, `usb_conf.h`
(FS core, device mode, no VBUS sensing, FIFO sizes), `usbd_conf.h` and `usbd_desc.h`.
The build defines `STM32F427_437xx`, `HSE_VALUE=8000000` (the stock literal `0x7a1200`),
`USE_USB_OTG_FS` and `USE_EMBEDDED_PHY`. The stock DCD callback table at load address
`0x0802B67C` has 9 entries, so `VBUS_SENSING_ENABLED` is off.

Every ST function has its own file in `files.tsv`, named after the function in lower
case. The file's evidence says whether it is linked or generated. Each ST object's
definitions are made weak. The generated source's definitions of the unlinked functions
therefore win, and the linker script discards the unlinked sections.

26 functions and `APBAHBPrescTable` (`.data`, VMA `0x2000071A`) match. The other ST
functions stay generated:

- **LTO.** The stock build used link-time optimisation. Its interprocedural constant
  propagation specialised some functions for their only caller. `NVIC_Init` lost its
  `IRQChannelCmd` check and its sub-priority. `DMA_Init` has the constant `0x400` folded
  in. `TIM_OC2Init`, `TIM_DeInit` and the RCC `.part` clones
  (`RCC_APB1PeriphResetCmd_off`) are specialised in the same way. ST's source alone cannot
  reproduce them, and the build uses no LTO.
- **Version.** The stock USB code predates V2.2.1: its `USB_OTG_EPDeactivate` still
  clears `DAINTMSK`. The plan is to move the firmware fully to V2.2.1 later, which
  changes those functions' bytes.

## How the generator works

- **Layout.** `C` (instructions), `D` (defined data) and `U` (undefined) ranges tile the
  whole image (129,824 bytes). `layout-fixes.tsv` adds functions Ghidra missed, moves
  entries Ghidra put after an instruction scheduled before the push (`X` drops the wrong
  one), marks the `b .` fault loops as code, and turns falsely disassembled tables back
  into data (for example the synth param table at `0x0802A62E`).
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
| `libc_memchr` | `0800C230`–`0800C2D0` | newlib's assembly `memchr` (a plain `.text` section, linked before every `.text.*`) |
| `syscalls` | `0801EEAC`–`0801EEFC` | newlib stubs `_read` … `_close` |
| `stm32f4xx_it` | `08023A78`–`08024070` | IRQ and fault handlers, in ST's `stm32f4xx_it.c` order reversed (`NMI_Handler` last) |
| app files | `0800C2D0`–`08025E60` (60 ranges) | app code between linker input sections: each starts after a `00 00` linker fill that follows code (see below); named for their content, listed in `docs/source-map.md` |
| `startup` | `08025E60`–`08025EAC` | `Reset_Handler` + `Default_Handler` (CMSIS `startup_stm32f4xx.s`) |
| `libstdcxx_*`, `libc_*` | `08025EAC`–`08026D94` | one file per library object (`docs/firmware-map.md` *Runtime libraries*) |
| `rodata` | `08026D94`–`0802AE2C` | app constant data; `D_08026D94` is read by app code |
| `rodata_libstdcxx_nothrow`, `rodata_libc_*` | `0802AE2C`–`0802AEC8` | `std::nothrow`, locale `"C"`, `_global_impure_ptr`, `__sf_fake_*`, printf strings |
| `crti` | `0802AEC8`–`0802AEE0` | `_init`, `_fini` (newlib crti/crtn) |
| `init_array_crtbegin`, `init_array`, `fini_array` | `0802AEE0`–`0802AEEC` | `frame_dummy`; one static constructor (`0x080256F4`); `__do_global_dtors_aux` |
| `data` | `0802AEEC`–`0802BAA0` | `.data` load image (`_sidata`); `Reset_Handler` copies `0x0802AEEC`–`0x0802BB04` to `0x20000000`–`0x20000C18` |
| `data_libc_impure` | `0802BAA0`–`0802BB04` | `impure_data` and `_impure_ptr` at the end of `.data` |
| `image_tail` | `0802BB04`–`0802BB20` | 28 unexplained bytes up to the image end |

Files listed in `src/libraries.tsv` are not generated: the build links them from the
toolchain (*Library code* below). `.bss` runs to `0x200035B8`. `Reset_Handler` calls `0x0801A848`, then
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
  section's size to that alignment. A code piece that starts or ends at 2 mod 4 is
  *biased*: its section is `.p2align 1` with a base label `.Lb_<start>`, and the linker
  script puts `. = . + ((bias - .) & 3);` in front of it (bias = start mod 4). Its literal
  loads and `adr` are spelled out relative to the base, because gas computes
  `Align(PC, 4)` only in a 4-aligned section: a narrow load is
  `.short 0x4800 | rd << 8 | OFF >> 2`, a wide one `ldr rd, [pc, #OFF]` (`adr` becomes
  `addw`/`subw`), with `OFF = (T - B) - (((. - B) + 4 + bias) & ~3) + bias`. Gas evaluates
  these after branch relaxation, so they also hold in `--relax` builds. Data alignment
  inside a biased piece is `.fill`/`.space` of `((bias + (. - B)) & 2)` bytes. Gas does not
  count `.short` as an instruction of an IT block, so the rest of such a block is emitted
  as `.inst` (14 instructions on firmware 800). A clue to the real compile units: an
  object with a literal pool starts and ends 4-aligned. Another: `stm32f4xx_it.c`'s
  handlers appear in reverse source order, so GCC probably emitted every unit's functions
  last to first.
- **Input sections.** The linker fills the gap before a 4-aligned input section with
  `00 00`; gas pads inside a section with `00 bf` (`nop`). 58 app functions follow code
  plus a `00 00` fill, so each starts a new input section and an app file; the other two
  app files start after `crtbegin` and `stm32f4xx_it`. 4 follow a `00 bf` pad
  (`main`, `0x080114C0`, `recorder_on_cell`, `session_ctl_patch_write`), so one section
  holds several functions: the app was built without `-ffunction-sections`, and an
  input section is a whole compile unit. A boundary into a 2-aligned section, or at an
  address already 4-aligned, leaves no pad, so an app file can hold several units. The
  first, `app_main` (`0x0800C230`), runs 25.6 KB to `0x0801264C` with no pad.

## Classes

`src/classes.tsv` lists `addr class name`: a function (by entry address and its
`symbols.tsv` name) that goes into `<class>.s` instead of its `files.tsv` file, so that
each class can later become one `.cpp`. Stock compile units interleave classes (for
example the views' `on_cell` methods, then their `pot` methods), so a class file holds
several *pieces*, one section each (`.text.<file>.<start>`), and the linker script lists
the sections in stock order. A piece runs from a listed function to the next function
entry (or file start); what follows stays in its `files.tsv` file, and adjacent pieces of
one file merge. Classes are named after their vtable (`vt_<class>`); a function is listed
when its name starts with its class name. C, HAL and newlib code keeps its `files.tsv`
file. `generate` fails on a line whose address is not a function entry with that name.
The crossing rules above apply between pieces: a literal load into another piece of the
same file is an error too. Firmware 800: 301 functions in 63 classes, 158 files, 289
sections, byte-identical in both modes. `--classes FILE` on `generate` and `shift`
replaces the list (an absent file means none).

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
