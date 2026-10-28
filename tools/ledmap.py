#!/usr/bin/env python3
"""Map the Mono Station's LED channels to physical LEDs (needs overlays/ledtest flashed).

The LED output buffer (0x200021F8) holds 352 channel levels that leds_update turns into
the SPI frame. leds_render_rgb fills it for the 82 RGB LEDs, three channels each, from
the table at 0x08029DCA (per LED, an index into the channel table at 0x080290D4). The 21
mono LEDs are channels 0-22 except 7 and 15 (the direct columns of the driver table at
0x080292F2), and six more channels are driven but not in the RGB table.

overlays/ledtest adds `F0 00 20 29 01 61 7D <n> (<hi> <lo>)*n F7`: light those n
channels, blank the rest; n = 0 hands the LEDs back.

    .venv/bin/python tools/ledmap.py light 24 25 26     # light channels by number
    .venv/bin/python tools/ledmap.py map leds.tsv       # step through every LED

`map` lights one LED at a time (an RGB LED white, all three channels) and asks what lit
up; Enter alone records nothing. It rewrites the file after each answer, and rows already
labelled are skipped, so it can be stopped and resumed. MIDI I/O uses mido +
python-rtmidi (imported in main).
"""
import argparse
import sys
from pathlib import Path

import hwtest_slots as hw

CMD_LEDTEST = 0x7D
CHANNELS = 352
RGB_LEDS = 82
RGB_LED_TABLE = 0x08029DCA
RGB_CHANNEL_TABLE = 0x080290D4
MONO_CHANNELS = [c for c in range(23) if c not in (7, 15)]
SPARE_CHANNELS = [224, 238, 239, 312, 326, 327]


def ledtest_request(channels):
    assert len(channels) <= 8 and all(0 <= c < CHANNELS for c in channels)
    body = [len(channels)]
    for c in channels:
        body += [c >> 7, c & 0x7F]
    return hw.MS_HEADER + bytes([CMD_LEDTEST, *body, 0xF7])


def u16(image, addr):
    off = addr - hw.LOAD_BASE
    return image[off] | image[off + 1] << 8


def rgb_channels(image, led):
    """The three output channels of RGB LED `led` (0-81), as leds_render_rgb writes them."""
    entry = u16(image, RGB_LED_TABLE + 2 * led)
    return [u16(image, RGB_CHANNEL_TABLE + 2 * (entry + i)) for i in range(3)]


def all_leds(image):
    """(name, channels) for every LED: rgb0..rgb81, mono0..mono20, spare0..spare5."""
    leds = [(f"rgb{i}", rgb_channels(image, i)) for i in range(RGB_LEDS)]
    leds += [(f"mono{i}", [c]) for i, c in enumerate(MONO_CHANNELS)]
    leds += [(f"spare{i}", [c]) for i, c in enumerate(SPARE_CHANNELS)]
    return leds


def read_labels(path):
    labels = {}
    if path.exists():
        for line in path.read_text().splitlines():
            fields = line.split("\t")
            if len(fields) >= 3 and not line.startswith("#"):
                labels[fields[0]] = fields[2]
    return labels


def write_labels(path, leds, labels):
    rows = ["# led\tchannels\tlabel"]
    for name, channels in leds:
        if name in labels:
            rows.append(f"{name}\t{','.join(map(str, channels))}\t{labels[name]}")
    path.write_text("".join(row + "\n" for row in rows))


def run_map(dev, leds, path):
    labels = read_labels(path)
    for name, channels in leds:
        if name in labels:
            continue
        dev.send_sysex(ledtest_request(channels))
        try:
            labels[name] = input(f"{name} (channels {channels}): what lit up? ").strip()
        except EOFError:
            break
        write_labels(path, leds, labels)
    print(f"wrote {path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", help="part of the application port name")
    parser.add_argument("--image", type=Path, default=hw.FIRMWARE_BIN,
                        help="stock 800 image, for the RGB tables")
    sub = parser.add_subparsers(dest="cmd", required=True)
    light = sub.add_parser("light", help="light channels until Enter")
    light.add_argument("channels", type=lambda x: int(x, 0), nargs="+")
    mapper = sub.add_parser("map", help="step through every LED and record what lit")
    mapper.add_argument("out", type=Path)
    args = parser.parse_args(argv)

    import mido

    dev = hw.Device(mido, hw.find_port(mido, args.port, wait=10) or sys.exit("no app port"))
    print(f"port {dev.name!r}, versions (bootloader, firmware): {hw.query_version(dev)}",
          file=sys.stderr)
    try:
        if args.cmd == "light":
            for i in range(0, len(args.channels), 8):
                dev.send_sysex(ledtest_request(args.channels[i:i + 8]))
                input(f"lit {args.channels[i:i + 8]}; Enter for the next ")
        else:
            run_map(dev, all_leds(args.image.read_bytes()), args.out)
    except KeyboardInterrupt:
        pass
    finally:
        dev.send_sysex(ledtest_request([]))
        dev.close()


if __name__ == "__main__":
    main()
