#!/usr/bin/env python3
"""Quick Thumb-2 disassembly of a decoded image around an address.

Optional analysis helper (not part of the core stdlib-only tooling); needs `pip install capstone`.
Example: python3 tools/disasm.py firmware/monostation-firmware-800.bin 0x08025e61 -n 20
"""

import argparse
import struct
import sys
from pathlib import Path

LOAD_ADDRESS = 0x0800C000


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bin", type=Path)
    parser.add_argument("address", type=lambda x: int(x, 0),
                        help="address to disassemble from (Thumb bit is ignored)")
    parser.add_argument("-n", type=int, default=16, help="number of instructions")
    parser.add_argument("--base", type=lambda x: int(x, 0), default=LOAD_ADDRESS)
    args = parser.parse_args()

    try:
        from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
    except ImportError:
        sys.exit("capstone not installed: pip install capstone")

    image = args.bin.read_bytes()
    start = (args.address & ~1) - args.base
    if not 0 <= start < len(image):
        sys.exit(f"address {args.address:#x} is outside the image at base {args.base:#x}")

    md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)
    window = image[start:start + args.n * 4 + 4]
    for instruction in list(md.disasm(window, args.address & ~1))[:args.n]:
        raw = struct.unpack(f"<{instruction.size // 2}H", instruction.bytes)
        print(f"  {instruction.address:#010x}: {' '.join(f'{h:04x}' for h in raw):10} "
              f"{instruction.mnemonic:8} {instruction.op_str}")


if __name__ == "__main__":
    main()
