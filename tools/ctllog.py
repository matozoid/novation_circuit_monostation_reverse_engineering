#!/usr/bin/env python3
"""Log the Mono Station's control events through the stock firmware's debug reports.

The app has a hidden debug switch: `F0 00 20 29 01 61 70 32 <ports> <flags> F7` stores a
port mask (bit 0 USB, bit 1 DIN) and a flag byte at 0x200016A0. With a flag set, the
firmware reports each control event as `F0 00 20 29 01 61 70 33 <type> <id> n n n n F7`
(a 16-bit value in four nibbles, most significant first):

    flag  type  source                    id                     value
    0x01  0x01  pots_scan                 0x20-0x31 pot/fader   filtered ADC reading
    0x02  0x02  pad state machine         0x00-0x1F pad          velocity (0 = note-off)
    0x04  0x04  pad state machine         0x00-0x1F pad          pressure
    0x08  0x08  input_poll_task           0x32/0x33 encoder      raw wiper readings
    0x10  0x10  main loop button scan     0x00-0x21 button       1 pressed, 0 released
    0x20        echo every received SysEx and sent short MIDI message to the ports

The flags live in RAM: a reboot clears them, and this tool clears them on exit.
`map` walks through a list of control names and records the first id each one reports.
MIDI I/O uses mido + python-rtmidi (imported in main).

    .venv/bin/python tools/ctllog.py log
    .venv/bin/python tools/ctllog.py map docs/controls.tsv
"""
import argparse
import sys
import time

import hwtest_slots as hw

DEBUG_SET = 0x32
DEBUG_REPORT = 0x33
PORT_USB = 0x01
TYPES = {0x01: "pot", 0x02: "pad", 0x04: "pressure", 0x08: "encoder", 0x10: "button"}
ALL_CONTROLS = 0x1F
REPORT_PREFIX = hw.MS_HEADER + bytes([0x70, DEBUG_REPORT])


def debug_request(ports, flags):
    return hw.MS_HEADER + bytes([0x70, DEBUG_SET, ports & 0x7F, flags & 0x7F, 0xF7])


def decode_report(msg):
    """(type name, id, value) of a debug report, or None for any other message."""
    if len(msg) != 15 or not msg.startswith(REPORT_PREFIX) or msg[-1] != 0xF7:
        return None
    kind, ident = msg[8], msg[9]
    value = 0
    for nibble in msg[10:14]:
        value = value << 4 | nibble & 0xF
    return TYPES.get(kind, f"type{kind:#04x}"), ident, value


def format_report(report):
    kind, ident, value = report
    return f"{kind:8} id {ident:#04x}  value {value:5} ({value:#06x})"


def read_reports(dev, timeout):
    """Debug reports received within `timeout` seconds."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        msg = dev.next_sysex(end - time.monotonic())
        report = msg and decode_report(msg)
        if report:
            yield report


def run_log(dev):
    print("logging; move controls, Ctrl+C to stop", file=sys.stderr)
    while True:
        for report in read_reports(dev, 1):
            print(format_report(report), flush=True)


def run_map(dev, out):
    """Prompt for each control, record the first press/move as `name<TAB>type<TAB>id`."""
    names = [line.split("\t")[0] for line in out.read_text().splitlines()
             if line and not line.startswith("#")] if out.exists() else []
    if not names:
        sys.exit(f"{out}: put one control name per line first")
    rows = []
    for name in names:
        input(f"\n{name}: press Enter, then press or move it once ")
        dev.drain()
        seen = None
        for kind, ident, value in read_reports(dev, 5):
            if kind != "pressure":
                seen = (kind, ident)
                break
        print(f"  -> {seen[0]} {seen[1]:#04x}" if seen else "  -> nothing in 5 s")
        rows.append(f"{name}\t{seen[0]}\t{seen[1]:#04x}" if seen else f"{name}\t\t")
    out.write_text("".join(row + "\n" for row in rows))
    print(f"wrote {out}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", help="part of the application port name")
    parser.add_argument("--flags", type=lambda x: int(x, 0), default=ALL_CONTROLS,
                        help="debug flag byte (default 0x1F: every control report)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("log", help="print every report")
    mapper = sub.add_parser("map", help="record the id of each named control")
    mapper.add_argument("names", type=hw.Path, help="file with one control name per line; "
                        "rewritten with the ids")
    args = parser.parse_args(argv)

    import mido

    dev = hw.Device(mido, hw.find_port(mido, args.port, wait=10) or sys.exit("no app port"))
    print(f"port {dev.name!r}, versions (bootloader, firmware): {hw.query_version(dev)}",
          file=sys.stderr)
    dev.send_sysex(debug_request(PORT_USB, args.flags))
    try:
        if args.cmd == "log":
            run_log(dev)
        else:
            run_map(dev, args.names)
    except KeyboardInterrupt:
        pass
    finally:
        dev.send_sysex(debug_request(0, 0))
        dev.close()


if __name__ == "__main__":
    main()
