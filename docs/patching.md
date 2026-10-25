# Binary patching (#17)

`tools/fwpatch.py` builds a patched firmware image from assembly source and a declarative hook list. Patches are source, not hex blobs.

```sh
python3 tools/fwpatch.py patches/<name> firmware/monostation-firmware-800.bin out.bin --syx out.syx
```

It needs GNU binutils for ARM (`apt install binutils-arm-none-eabi`: `as`, `ld`, `objcopy`, `readelf`). If capstone is installed, it also prints a disassembly of each branch site before and after. The tests in `tests/test_fwpatch.py` skip the build tests when binutils or the firmware is missing.

## Where patch code goes

The stock 800 image ends at `0x0802BB20`; its last data is the `.data` initialiser (`0x0802AEEC`–`0x0802BB04`). Patch code is linked at the first 32-byte boundary after the image (`0x0802BB20` for 800) and may grow up to the end of sector 5 (`0x08040000`), about 80 KB. How large an image the bootloader accepts hasn't been tested (#25). The stock code has no literal reference to its own end address, so appending code doesn't seem to break a self-check, but this isn't proven.

Patch code is **read-only**: the linker rejects any `.data` or `.bss`, because the stock startup code copies and clears only its own ranges (`.data` `0x20000000`–`0x20000C18`, `.bss` `0x20000C18`–`0x200035B8`). Free RAM for patch state isn't mapped yet.

## patch.toml

```toml
base = 800                 # stock image the patch applies to; its SHA-256 is checked
version = 801              # optional: reported version (image header MAIN at 0x1AC) and .syx start message
sources = ["hello.s"]      # assembled with -mcpu=cortex-m4 -mthumb, FPv4-SP, hard-float

[functions]                # firmware functions the source may call (Thumb bit added)
sysex_dispatch = 0x080202A0

[data]                     # firmware data addresses (used as is)
version_header = 0x0800C1AC

[[hook]]
at = 0x08025E8E            # address in the stock image
kind = "bl"                # bl | b | word | bytes
target = "my_main"         # symbol from the sources, [functions] or [data], or an integer
expect = "e6 f7 1f fa"     # the stock bytes at `at`; the build fails if they differ
```

Hook kinds:

| kind | writes | use |
|------|--------|-----|
| `bl` | 4-byte Thumb-2 `BL target` | redirect an existing call to a wrapper |
| `b` | 4-byte `B.W target` | jump from inside a function to a trampoline |
| `word` | 32-bit `target`, Thumb bit kept for `.thumb_func` symbols | replace a vector, vtable entry or function pointer |
| `bytes` | `data` (hex) | anything else: constants, strings, NOPs (`00 bf`) |

Every hook needs `expect`, which must be as long as what the hook writes. Hooks may not overlap. The tool prints each edit and every patch symbol.

## Writing patch source

- Mark every function `.thumb_func`. Without it `readelf` reports an even address, and a `word` hook or `ldr r0, =func` would then branch to ARM state and fault.
- Calls to firmware functions listed under `[functions]` link as plain `BL`/`B.W`, and their literals carry the Thumb bit.
- A `b` hook in the middle of a function overwrites 4 bytes, which can be one 32-bit or two 16-bit instructions. The trampoline has to run those instructions itself and then `b.w` back to `at + 4`. Don't displace PC-relative instructions (`ldr rX, [pc, …]`, `adr`, branches) without rewriting them, and don't pick a site that is a branch target.
- The calling convention is AAPCS with hard-float: r0–r3 and s0–s15 are argument and scratch registers, and r4–r11 and s16–s31 must be preserved.

## Flashing

Encode with `--syx` and send it through the bootloader (#16, `docs/update-protocol.md`). To go back, reflash stock `monostation-firmware-800.syx`. Bump `version` so the device and Components show that a patched image is running. A `version` below 800 makes Components offer the stock update.
