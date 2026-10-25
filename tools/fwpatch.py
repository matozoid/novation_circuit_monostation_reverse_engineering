#!/usr/bin/env python3
"""Build a patched firmware image from assembly source and a declarative hook list.

A patch is a directory with a patch.toml and the .s files it names (docs/patching.md).
The sources are assembled and linked into the free flash after the stock image, then each
hook overwrites a few bytes of the stock code (a BL or B.W to patch code, a pointer, or raw
bytes) after checking that the original bytes are what the patch expects.

Needs GNU binutils for ARM (arm-none-eabi-as/ld/objcopy/readelf); everything else is stdlib.
Example:
  python3 tools/fwpatch.py patches/hello firmware/monostation-firmware-800.bin out.bin --syx out.syx
"""

import argparse
import hashlib
import struct
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from fwsyx import LOAD_ADDRESS, MONOSTATION_PRODUCT_ID, encode_firmware

STOCK_SHA256 = {
    800: "b6cbf50a3f1ff58a9ef23e8ef270fdebae6e6ad4aa0439f8a1fa3de9230427ad",
}

HEADER_OFFSET = 0x1AC  # "MAIN" + BCD version, little-endian (docs/sysex-handlers.md)
HEADER_TAG = b"MAIN"

PATCH_ALIGN = 32
FLASH_END = 0x08040000  # end of sector 5; how much the bootloader accepts is unknown (#25)

HOOK_KINDS = ("bl", "b", "word", "bytes")


class PatchError(Exception):
    pass


def encode_branch(at, target, link):
    """Thumb-2 BL (link) or B.W (T4) at address `at` to `target`, as 4 little-endian bytes."""
    if at & 1:
        raise PatchError(f"branch site {at:#x} is not halfword aligned")
    offset = (target & ~1) - (at + 4)
    if not -(1 << 24) <= offset < (1 << 24):
        raise PatchError(f"branch from {at:#x} to {target:#x} is out of range")
    value = offset & ((1 << 25) - 1)
    s = value >> 24
    i1 = (value >> 23) & 1
    i2 = (value >> 22) & 1
    j1 = (1 - i1) ^ s
    j2 = (1 - i2) ^ s
    first = 0xF000 | (s << 10) | ((value >> 12) & 0x3FF)
    second = (0xD000 if link else 0x9000) | (j1 << 13) | (j2 << 11) | ((value >> 1) & 0x7FF)
    return struct.pack("<HH", first, second)


def version_bcd(version):
    """Firmware version as the image header stores it: 800 -> b'\\x00\\x08'."""
    if not 0 <= version <= 9999:
        raise PatchError(f"version {version} does not fit four BCD digits")
    return struct.pack("<H", int(str(version), 16))


def parse_hex(text):
    return bytes.fromhex(text.replace(" ", ""))


def load_patch(directory):
    """Read patch.toml and check its fields; returns the parsed dict."""
    path = directory / "patch.toml"
    with path.open("rb") as f:
        patch = tomllib.load(f)
    if patch.get("base") not in STOCK_SHA256:
        raise PatchError(f"{path}: base must be one of {sorted(STOCK_SHA256)}")
    if not patch.get("sources"):
        raise PatchError(f"{path}: sources is empty")
    for hook in patch.get("hook", []):
        if hook.get("kind") not in HOOK_KINDS:
            raise PatchError(f"{path}: hook kind must be one of {HOOK_KINDS}: {hook}")
        if "at" not in hook or "expect" not in hook:
            raise PatchError(f"{path}: every hook needs at and expect: {hook}")
        if (hook["kind"] == "bytes") != ("data" in hook) or (hook["kind"] != "bytes") != ("target" in hook):
            raise PatchError(f"{path}: bytes hooks take data, the others take target: {hook}")
    return patch


def linker_script(origin, limit, functions, data):
    lines = [
        f"MEMORY {{ PATCH (rx) : ORIGIN = {origin:#x}, LENGTH = {limit - origin:#x} }}",
        "ENTRY(__patch_start)",
        "SECTIONS {",
        "  .text : { __patch_start = .; *(.text .text.* .rodata .rodata.*) } > PATCH",
        "  .data : { *(.data .data.* .bss .bss.* COMMON) } > PATCH",
        "  /DISCARD/ : { *(.ARM.exidx* .ARM.extab*) }",
        "}",
        'ASSERT(SIZEOF(.data) == 0, "patch code cannot use .data or .bss: '
        'the stock startup code neither copies nor clears it")',
    ]
    # Functions get the Thumb bit, so the linker keeps BL (not BLX) and literals are callable.
    lines += [f"{name} = {address | 1:#x};" for name, address in functions.items()]
    lines += [f"{name} = {address:#x};" for name, address in data.items()]
    return "\n".join(lines) + "\n"


def run(tool, *args):
    try:
        result = subprocess.run([tool, *args], capture_output=True, text=True)
    except FileNotFoundError:
        raise PatchError(f"{tool} not found; install GNU binutils for ARM "
                         "(apt install binutils-arm-none-eabi)") from None
    if result.returncode:
        raise PatchError(f"{tool} failed:\n{result.stderr}")
    return result.stdout


def link(directory, patch, origin, limit, prefix):
    """Assemble and link the patch sources; returns (code bytes, {symbol: address})."""
    functions = patch.get("functions", {})
    data = patch.get("data", {})
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        objects = []
        for i, source in enumerate(patch["sources"]):
            obj = tmp / f"{i}.o"
            run(f"{prefix}as", "-mcpu=cortex-m4", "-mthumb", "-mfpu=fpv4-sp-d16",
                "-mfloat-abi=hard", "-I", str(directory), "-o", str(obj), str(directory / source))
            objects.append(str(obj))
        script = tmp / "patch.ld"
        script.write_text(linker_script(origin, limit, functions, data))
        elf = tmp / "patch.elf"
        run(f"{prefix}ld", "-nostdlib", "--no-warn-rwx-segments", "-T", str(script),
            "-o", str(elf), *objects)
        out = tmp / "patch.bin"
        run(f"{prefix}objcopy", "-O", "binary", "-j", ".text", str(elf), str(out))
        code = out.read_bytes()
        symbols = {}
        # readelf, not nm: nm clears the Thumb bit of .thumb_func symbols.
        for line in run(f"{prefix}readelf", "-sW", str(elf)).splitlines():
            fields = line.split()
            if len(fields) == 8 and fields[3] in ("FUNC", "NOTYPE") and fields[6].isdigit():
                name = fields[7]
                if not name.startswith("$") and name not in functions and name not in data:
                    symbols[name] = int(fields[1], 16)
    symbols.update({name: address | 1 for name, address in functions.items()})
    symbols.update(data)
    return code, symbols


def resolve(target, symbols):
    if isinstance(target, int):
        return target
    if target not in symbols:
        raise PatchError(f"unknown symbol {target!r}")
    return symbols[target]


def hook_bytes(hook, symbols):
    kind = hook["kind"]
    if kind == "bytes":
        return parse_hex(hook["data"])
    target = resolve(hook["target"], symbols)
    if kind == "word":
        return struct.pack("<I", target)
    return encode_branch(hook["at"], target, link=kind == "bl")


def build(directory, stock, prefix="arm-none-eabi-", limit=FLASH_END):
    """Return (image, edits, symbols, code origin, code size).

    edits lists (address, hook kind or "version", old bytes, new bytes) in patch order.
    """
    patch = load_patch(directory)
    if hashlib.sha256(stock).hexdigest() != STOCK_SHA256[patch["base"]]:
        raise PatchError(f"input image is not stock firmware {patch['base']}")
    origin = LOAD_ADDRESS + -(-len(stock) // PATCH_ALIGN) * PATCH_ALIGN
    code, symbols = link(directory, patch, origin, limit, prefix)

    image = bytearray(stock) + b"\xFF" * (origin - LOAD_ADDRESS - len(stock)) + code
    edits = []
    for hook in patch.get("hook", []):
        at, new = hook["at"], hook_bytes(hook, symbols)
        expect = parse_hex(hook["expect"])
        if len(expect) != len(new):
            raise PatchError(f"hook at {at:#x}: expect is {len(expect)} bytes, patch is {len(new)}")
        offset = at - LOAD_ADDRESS
        if not 0 <= offset <= len(stock) - len(new):
            raise PatchError(f"hook at {at:#x} is outside the stock image")
        if stock[offset:offset + len(new)] != expect:
            raise PatchError(f"hook at {at:#x}: found {stock[offset:offset + len(new)].hex()}, "
                             f"expected {expect.hex()}")
        for other, _, _, done in edits:
            if at < other + len(done) and other < at + len(new):
                raise PatchError(f"hooks at {other:#x} and {at:#x} overlap")
        image[offset:offset + len(new)] = new
        edits.append((at, hook["kind"], expect, new))

    if "version" in patch:
        if image[HEADER_OFFSET:HEADER_OFFSET + 4] != HEADER_TAG:
            raise PatchError("image header tag MAIN not found")
        old = bytes(image[HEADER_OFFSET + 4:HEADER_OFFSET + 6])
        new = version_bcd(patch["version"])
        image[HEADER_OFFSET + 4:HEADER_OFFSET + 6] = new
        edits.append((LOAD_ADDRESS + HEADER_OFFSET + 4, "version", old, new))
    return bytes(image), edits, symbols, origin, len(code)


def disassemble(data, address):
    try:
        from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
    except ImportError:
        return ""
    return "; ".join(f"{i.mnemonic} {i.op_str}" for i in Cs(CS_ARCH_ARM, CS_MODE_THUMB).disasm(data, address))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("patch", type=Path, help="patch directory holding patch.toml")
    parser.add_argument("stock", type=Path, help="stock decoded image (syx2bin output)")
    parser.add_argument("out", type=Path, help="patched image to write")
    parser.add_argument("--syx", type=Path, help="also encode the patched image as a firmware .syx")
    parser.add_argument("--prefix", default="arm-none-eabi-", help="binutils tool prefix")
    args = parser.parse_args()

    try:
        image, edits, symbols, origin, size = build(args.patch, args.stock.read_bytes(), args.prefix)
    except PatchError as e:
        sys.exit(f"fwpatch: {e}")
    patch = load_patch(args.patch)
    print(f"patch code: {size} bytes at {origin:#010x}-{origin + size:#010x}", file=sys.stderr)
    for name, address in sorted(symbols.items(), key=lambda kv: kv[1]):
        if origin <= address < origin + size and not name.startswith("__"):
            print(f"  {address:#010x} {name}", file=sys.stderr)
    for at, kind, old, new in edits:
        line = f"  {at:#010x}: {old.hex(' ')} -> {new.hex(' ')}"
        if kind in ("bl", "b") and (listing := disassemble(new, at)):
            line += f"   ({disassemble(old, at)} -> {listing})"
        print(line, file=sys.stderr)
    args.out.write_bytes(image)
    if args.syx:
        version = patch.get("version", patch["base"])
        args.syx.write_bytes(encode_firmware(image, MONOSTATION_PRODUCT_ID, version))
        print(f"{args.syx}: version {version}", file=sys.stderr)


if __name__ == "__main__":
    main()
