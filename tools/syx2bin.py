#!/usr/bin/env python3
"""Decode a Mono Station firmware .syx into a raw flash image (load address 0x0800C000)."""

import argparse
import sys
from pathlib import Path

from fwsyx import LOAD_ADDRESS, decode_image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("syx", type=Path)
    parser.add_argument("bin", type=Path)
    args = parser.parse_args()

    image = decode_image(args.syx.read_bytes())
    args.bin.write_bytes(image)
    print(f"{args.bin}: {len(image)} bytes, "
          f"0x{LOAD_ADDRESS:08x}-0x{LOAD_ADDRESS + len(image):08x}", file=sys.stderr)


if __name__ == "__main__":
    main()
