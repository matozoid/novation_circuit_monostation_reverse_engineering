#!/usr/bin/env python3
"""Flash a firmware .syx to a Circuit Mono Station through its bootloader (#16).

Follows Components' updater (docs/update-protocol.md): send the 0x71 start, then each
data packet after the bootloader acknowledges the previous one, and stop after the
0x73 packet (image offset 0, the vector table). The 0x76 trailer is not sent. Then wait
for the application and check that it reports the version in the image header.

Before anything is sent, the file is decoded and checked: message order, block sizes,
product id, trailer checksum, the MAIN header and the vector table.

Entering the bootloader is manual: switch the unit off, hold Scales + Note + Velocity
and switch it on. The script waits for the bootloader port.

MIDI I/O uses mido + python-rtmidi (imported in main); the helpers are stdlib-only.

Usage:
    python3 tools/flash.py firmware/monostation-firmware-800.syx [--port NAME] [--yes]
"""
import argparse
import struct
import sys
import time
from pathlib import Path

import fwsyx
import hwtest_slots as hw

HEADER_OFFSET = 0x1AC          # "MAIN" + BCD version, little-endian
HEADER_TAG = b"MAIN"
FLASH_END = 0x08040000         # end of sector 5; the bootloader's real limit is unknown (#25)
STACK_RANGES = ((0x10000000, 0x10010000), (0x20000000, 0x20020000))  # CCM, SRAM (top inclusive)
DATA_LENGTH = len(fwsyx.NOVATION_HEADER) + 1 + fwsyx.BLOCK_SEPTETS + 1

PACKET_TIMEOUT = 10            # seconds per ack (Components' FIRMWARE_UPDATE_TIMEOUT)
BOOTLOADER_WAIT = 600          # seconds to wait for the user to enter the bootloader
REBOOT_WAIT = 20               # seconds to wait for the new firmware (Components: 2 x 10 s)

STATUS_FAILED = {16: "user cancelled", 124: "upload failed, composition not viable",
                 126: "upload failed, composition viable"}
STATUS_SUCCESS = 127
STATUS_CONFIRM = (1, 17)       # waiting for user confirmation (timeout)


class FlashError(Exception):
    pass


def header_version(image):
    """Version from the image header: b'MAIN\\x00\\x08' -> 800."""
    if image[HEADER_OFFSET:HEADER_OFFSET + 4] != HEADER_TAG:
        raise ValueError(f"no {HEADER_TAG.decode()} tag at image offset {HEADER_OFFSET:#x}")
    bcd = struct.unpack_from("<H", image, HEADER_OFFSET + 4)[0]
    text = f"{bcd:x}"
    if not text.isdigit():
        raise ValueError(f"image header version {bcd:#06x} is not BCD")
    return int(text)


def check_syx(data):
    """Decode and check a firmware .syx before it is sent.

    Returns (packets to send, info dict). Raises ValueError with the reason otherwise."""
    messages = fwsyx.split_messages(data)
    if not messages:
        raise ValueError("no SysEx messages")
    commands = [fwsyx.command(m) for m in messages]
    if commands[0] != fwsyx.CMD_START:
        raise ValueError("first message is not the 0x71 start")
    product_id, container_version = fwsyx.parse_start(messages[0])
    if product_id != fwsyx.MONOSTATION_PRODUCT_ID:
        raise ValueError(f"product id {product_id} is not the Mono Station's "
                         f"{fwsyx.MONOSTATION_PRODUCT_ID}")
    if commands[-1] == fwsyx.CMD_TRAILER:
        trailer = messages[-1]
        body = commands[1:-1]
    else:
        trailer = None
        body = commands[1:]
    if not body or body[-1] != fwsyx.CMD_DATA_FIRST or body.count(fwsyx.CMD_DATA_FIRST) != 1 \
            or any(c != fwsyx.CMD_DATA for c in body[:-1]):
        raise ValueError("expected 0x71, then 0x72 blocks, one 0x73 block, then an optional 0x76")
    for m in messages[1:1 + len(body)]:
        if len(m) != DATA_LENGTH:
            raise ValueError(f"data message of {len(m)} bytes, expected {DATA_LENGTH}")

    image = fwsyx.decode_image(data)
    if trailer is None:
        raise ValueError("no 0x76 trailer, so the checksum can't be checked")
    if fwsyx.parse_trailer(trailer) != fwsyx.checksum(image):
        raise ValueError("trailer checksum doesn't match the image")
    if fwsyx.LOAD_ADDRESS + len(image) > FLASH_END:
        raise ValueError(f"image of {len(image)} bytes runs past {FLASH_END:#x}")
    sp, reset = struct.unpack_from("<II", image)
    if not any(lo <= sp <= hi for lo, hi in STACK_RANGES):
        raise ValueError(f"initial stack pointer {sp:#x} is not in RAM")
    if not reset & 1 or not fwsyx.LOAD_ADDRESS <= reset & ~1 < fwsyx.LOAD_ADDRESS + len(image):
        raise ValueError(f"reset vector {reset:#x} is not Thumb code inside the image")
    info = {
        "product_id": product_id,
        "container_version": container_version,
        "version": header_version(image),
        "image_bytes": len(image),
        "checksum": fwsyx.checksum(image),
        "blocks": len(body),
    }
    return messages[:1 + len(body)], info


def parse_ack(msg):
    """(counter, short form) of a bootloader 0x72 ack, or None."""
    if msg[:4] != fwsyx.NOVATION_HEADER[:4] or len(msg) < 6 or msg[5] != fwsyx.CMD_DATA:
        return None
    if len(msg) == 10:
        return msg[6] + (msg[7] << 7) + (msg[8] << 14), True
    if len(msg) == 15:
        return msg[13] + (msg[12] << 4) + (msg[11] << 8), False
    return None


def parse_status(msg):
    """Status code of a 10-byte 0x73 status report, or None."""
    if msg[:4] == fwsyx.NOVATION_HEADER[:4] and len(msg) == 10 and msg[5] == fwsyx.CMD_DATA_FIRST:
        return msg[6]
    return None


def send_firmware(packets, send, next_msg, timeout=PACKET_TIMEOUT, progress=lambda i, n: None):
    """Send packets with Components' flow control.

    send(bytes) transmits one message; next_msg(timeout) returns the next received SysEx
    or None. After each packet except the 0x73 one (or the last), waits for an ack whose
    counter is above the last accepted one (or 0, short form), or a success status."""
    last = -1
    for i, packet in enumerate(packets):
        send(packet)
        progress(i + 1, len(packets))
        if packet[5] == fwsyx.CMD_DATA_FIRST or i == len(packets) - 1:
            return
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            msg = next_msg(left) if left > 0 else None
            if msg is None:
                raise FlashError(f"no ack within {timeout} s after packet {i} of {len(packets)}")
            if (ack := parse_ack(msg)) is not None:
                counter, short = ack
                if counter > last or (short and counter == 0):
                    last = counter
                    break
                continue
            status = parse_status(msg)
            if status in STATUS_FAILED:
                raise FlashError(f"bootloader reports {STATUS_FAILED[status]} ({status}) "
                                 f"after packet {i}")
            if status == STATUS_SUCCESS:
                break
            if status in STATUS_CONFIRM:
                print("\nthe device asks for confirmation", file=sys.stderr)
                deadline = time.monotonic() + timeout


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("syx", type=Path)
    parser.add_argument("--port", help="part of the bootloader port name")
    parser.add_argument("--yes", action="store_true", help="don't ask before flashing")
    args = parser.parse_args(argv)

    try:
        packets, info = check_syx(args.syx.read_bytes())
    except ValueError as e:
        sys.exit(f"{args.syx}: {e}")
    print(f"{args.syx}: firmware {info['version']} (start message says "
          f"{info['container_version']}), {info['image_bytes']} bytes, {info['blocks']} blocks, "
          f"checksum {info['checksum']:#010x}: OK")
    if info["version"] != info["container_version"]:
        print("warning: the image header and the start message give different versions")

    import mido

    if hw.matching_ports(mido.get_input_names(), args.port, bootloader=False):
        print("The application is running. Enter the bootloader by hand:")
    else:
        print("Waiting for the bootloader:")
    print("  1. switch the Mono Station off")
    print("  2. hold Scales + Note + Velocity")
    print("  3. switch it on, then let go")
    name = hw.find_port(mido, args.port, bootloader=True, wait=BOOTLOADER_WAIT)
    if name is None:
        sys.exit("no bootloader port appeared")
    dev = hw.Device(mido, name)
    versions = hw.query_version(dev)
    print(f"bootloader port {name!r}; versions (bootloader, firmware): {versions}")

    if not args.yes:
        answer = input(f"Flash firmware {info['version']} now? Type yes: ").strip().lower()
        if answer != "yes":
            dev.close()
            sys.exit("not flashed")

    def progress(i, n):
        if i == n or i % 100 == 0:
            print(f"\r  {i}/{n} packets", end="", file=sys.stderr, flush=True)

    dev.drain()
    start = time.monotonic()
    try:
        send_firmware(packets, dev.send_sysex, dev.next_sysex, progress=progress)
    except FlashError as e:
        dev.close()
        sys.exit(f"\nflash failed: {e}\nThe bootloader is still there: power-cycle into it "
                 "and run this again, or flash stock firmware.")
    print(f"\nsent in {time.monotonic() - start:.0f} s; waiting for the firmware to start")
    dev.close()

    deadline = time.monotonic() + REBOOT_WAIT
    while time.monotonic() < deadline:
        app = hw.find_port(mido, args.port, bootloader=False, wait=1)
        if app:
            dev = hw.Device(mido, app)
            versions = hw.query_version(dev)
            dev.close()
            if versions:
                if versions[1] != info["version"]:
                    sys.exit(f"the application reports {versions}, expected firmware {info['version']}")
                print(f"OK: application port {app!r} reports (bootloader, firmware) {versions}")
                return
    sys.exit(f"the application didn't report a version within {REBOOT_WAIT} s; "
             "check the unit, or enter the bootloader and flash again")


if __name__ == "__main__":
    main()
