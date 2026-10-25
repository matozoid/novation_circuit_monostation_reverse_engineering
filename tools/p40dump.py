#!/usr/bin/env python3
"""Read memory through the app's 'p' 0x40 op 2 command, 256 bytes per request.

On stock firmware this reads the external SPI flash (addresses below 0x800000). With
patch memread (patches/memread, #24) the same command also reads MCU memory: flash
0x08000000-0x080FFFFF (the bootloader is 0x08000000-0x0800BFFF), system memory and OTP,
option bytes, CCM, SRAM and backup SRAM.

Only op 2 (read) is ever sent; ops 0 and 1 erase and program external flash.
MIDI I/O uses mido + python-rtmidi (imported in main).

Example (bootloader plus the first 16 KiB of the app, which must match the stock image):
    python3 tools/p40dump.py 0x08000000 0x10000 ~/ms-dumps/mcu-08000000.bin
"""
import argparse
import sys
from pathlib import Path

import hwtest_slots as hw

RETRIES = 3


def read_block(dev, addr):
    """256 bytes at addr; None if no valid reply came."""
    for _ in range(RETRIES):
        dev.drain()
        dev.send_sysex(hw.p40_read_request(addr))
        reply = dev.receive(lambda m: m[:10] == hw.P40_PREFIX + bytes([hw.P40_READ]), 2,
                            stop=lambda m: True)
        if reply:
            try:
                return hw.decode_p40_reply(reply[0])
            except ValueError:
                pass
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("start", type=lambda x: int(x, 0))
    parser.add_argument("length", type=lambda x: int(x, 0), help="rounded up to 256 bytes")
    parser.add_argument("out", type=Path)
    parser.add_argument("--port", help="part of the application port name")
    args = parser.parse_args(argv)

    import mido

    dev = hw.Device(mido, hw.find_port(mido, args.port, wait=10) or sys.exit("no app port"))
    print(f"port {dev.name!r}, versions (bootloader, firmware): {hw.query_version(dev)}",
          file=sys.stderr)
    out = bytearray()
    end = args.start + -(-args.length // hw.P40_READ_BYTES) * hw.P40_READ_BYTES
    for addr in range(args.start, end, hw.P40_READ_BYTES):
        block = read_block(dev, addr)
        if block is None:
            args.out.write_bytes(out)
            dev.close()
            sys.exit(f"\nno reply at {addr:#x}; wrote the {len(out)} bytes read so far")
        out += block
        if addr % 0x1000 == 0:
            print(f"\r  {addr:#010x}", end="", file=sys.stderr, flush=True)
    dev.close()
    args.out.write_bytes(out)
    print(f"\n{args.out}: {len(out)} bytes from {args.start:#x}", file=sys.stderr)


if __name__ == "__main__":
    main()
