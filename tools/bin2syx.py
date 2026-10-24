#!/usr/bin/env python3
"""Encode a raw flash image (load address 0x0800C000) into a Mono Station firmware .syx."""

import argparse
import sys
from pathlib import Path

from fwsyx import MONOSTATION_PRODUCT_ID, checksum, decode_image, encode_firmware


def product_id(text):
    parts = text.split()
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("expected two hex bytes, e.g. '00 7f'")
    return tuple(int(part, 16) for part in parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bin", type=Path)
    parser.add_argument("syx", type=Path)
    parser.add_argument("--version", type=int, required=True,
                        help="firmware version written to the start message, e.g. 800")
    parser.add_argument("--product-id", type=product_id, default=MONOSTATION_PRODUCT_ID,
                        help="two hex bytes (default: '00 7f', Circuit Mono Station)")
    args = parser.parse_args()

    syx = encode_firmware(args.bin.read_bytes(), args.product_id, args.version)
    image = decode_image(syx)
    args.syx.write_bytes(syx)
    print(f"{args.syx}: {len(syx)} bytes, image {len(image)} bytes, "
          f"checksum 0x{checksum(image):08x}", file=sys.stderr)


if __name__ == "__main__":
    main()
