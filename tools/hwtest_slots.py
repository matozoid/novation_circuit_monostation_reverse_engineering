#!/usr/bin/env python3
"""Automated hardware test for patch slots 64-127 (#31, docs/hw-test-patch-slots.md).

Runs the read-only tests 0-3 of the plan against a connected Mono Station.
The storage dumps (0x78) are served by the bootloader, not the app, so the
script reboots into it with the short 0x71 message (as Components' device page
does), runs tests 0-1 there, then asks you to press Play to return to the app:

    0  back up the 64 visible patches (0x2000/0x8000) and all sessions
    1  request all 128 slots in one 0x78 dump (0x2000/0x10000)
    2  fallback if test 1 fails (in the app): read 0xA000-0x12000 with 'p' 0x40 op 2
    -  classify every slot (erased FF / Initial Patch / data)
    T  envelope Trigger (patch byte 27, NRPN 0:73) set to 0 then 1, asking
       what you hear; changes only the working copy of the current patch
    3  program change 63 and 64 on the Osc 1 channel, asking you what happened,
       then program change 0 (which also undoes the Trigger test)

Nothing is written to the device's flash. The only 'p' 0x40 operation sent is
op 2 (read). Dumps and report.json go to the output directory.

MIDI I/O uses mido + python-rtmidi (the #16 exception to stdlib-only); the
helpers above main() are stdlib-only and unit-tested. On Linux the 0x78 dumps
bypass mido and read the ALSA rawmidi device (/dev/snd/midiCcDd), as amidi
does: through the ALSA sequencer, a 3 MB session dump loses blocks to input
overruns.

Usage:
    python3 tools/hwtest_slots.py ~/ms-dumps [--port NAME] [--channel 1]
                                  [--skip-session-backup] [--skip-trigger] [--skip-pc]
"""
import argparse
import json
import os
import select
import sys
import time
from pathlib import Path

import sessionsyx

ROOT = Path(__file__).resolve().parent.parent
FIRMWARE_BIN = ROOT / "firmware" / "monostation-firmware-800.bin"
LOAD_BASE = 0x0800C000
INITIAL_PATCH_ADDR = 0x08028F6B

PATCH_LEN = 126
SLOT_SIZE = 512
VISIBLE_SLOTS = 64
ALL_SLOTS = 128
HIDDEN_START = sessionsyx.PATCHES_START + VISIBLE_SLOTS * SLOT_SIZE   # 0xA000
HIDDEN_END = sessionsyx.PATCHES_START + ALL_SLOTS * SLOT_SIZE         # 0x12000
SESSIONS_LENGTH = sessionsyx.SESSION_SIZE * 33

MS_HEADER = bytes([0xF0, 0x00, 0x20, 0x29, 0x01, 0x61])
P40_PREFIX = MS_HEADER + bytes([0x70, 0x40, 0x00])
P40_READ = 2
P40_READ_BYTES = 256
P40_REPLY_LEN = 10 + 2 * P40_READ_BYTES + 1                            # 523
VERSION_REQUEST = bytes([0xF0, 0x00, 0x20, 0x29, 0x00, 0x70, 0xF7])
REBOOT_TO_BOOTLOADER = bytes([0xF0, 0x00, 0x20, 0x29, 0x00, 0x71, 0x00, 0x7F, 0x00, 0x00, 0xF7])
BOOTLOADER_WAIT = 8         # seconds; Components' maxWaitForResetTimeout
TRIGGER_NRPN = (0, 73)                                                  # param id 2


def p40_read_request(addr):
    """'p' 0x40 op 2: read 256 bytes of external flash at addr."""
    return P40_PREFIX + bytes([P40_READ]) + sessionsyx.to_nibbles(addr) + b"\xF7"


def decode_p40_reply(msg):
    """Return the 256 bytes carried by a 523-byte op-2 reply."""
    if len(msg) != P40_REPLY_LEN or msg[:10] != P40_PREFIX + bytes([P40_READ]) \
            or msg[-1] != 0xF7:
        raise ValueError(f"not a p 0x40 op 2 reply ({len(msg)} bytes)")
    n = msg[10:-1]
    return bytes(n[i] << 4 | n[i + 1] for i in range(0, len(n), 2))


def decode_version(msg):
    """(bootloader, firmware) from a 0x70 reply: 5 decimal digits each."""
    if len(msg) < 17 or msg[:6] != VERSION_REQUEST[:6]:
        raise ValueError("not a 0x70 version reply")
    digits = lambda b: int("".join(str(d) for d in b))
    return digits(msg[6:11]), digits(msg[11:16])


def initial_patch(image):
    off = INITIAL_PATCH_ADDR - LOAD_BASE
    return image[off:off + PATCH_LEN]


def classify(stream, first_slot, init):
    """One dict per 512-byte slot in stream; first_slot numbers the first one."""
    out = []
    for i in range(len(stream) // SLOT_SIZE):
        p = stream[i * SLOT_SIZE:i * SLOT_SIZE + PATCH_LEN]
        kind = "erased" if p == b"\xFF" * PATCH_LEN else \
            "initial" if p == init else "data"
        out.append({"slot": first_slot + i, "kind": kind,
                    "name": p[:16].decode("latin1")})
    return out


def summarise(slots):
    counts = {}
    for s in slots:
        counts[s["kind"]] = counts.get(s["kind"], 0) + 1
    return counts


def is_transfer_message(msg):
    return msg[:5] == sessionsyx.NOVATION_HEADER and len(msg) > 5 and \
        msg[5] in (sessionsyx.CMD_HEADER, sessionsyx.CMD_DATA, sessionsyx.CMD_TRAILER)


# --- MIDI I/O (mido) --------------------------------------------------------

def raw_midi_path(port_name, proc=Path("/proc/asound")):
    """The ALSA rawmidi device behind a sequencer port, or None.

    A port is named "<client>:<port> <c>:<p>"; the client is the card's MIDI
    device name, the first line of /proc/asound/cardN/midiD."""
    client = port_name.split(":")[0]
    for f in sorted(proc.glob("card[0-9]*/midi[0-9]*")):
        try:
            name = f.read_text().splitlines()[0].strip()
        except (OSError, IndexError):
            continue
        if name == client:
            return Path("/dev/snd") / f"midiC{f.parent.name[4:]}D{f.name[4:]}"
    return None


class SysexSplitter:
    """Cuts a raw MIDI byte stream into complete F0..F7 messages."""
    def __init__(self):
        self.buf = None

    def feed(self, data):
        out = []
        for b in data:
            if b >= 0xF8:                   # real-time bytes may interleave
                continue
            if b == 0xF0:
                self.buf = bytearray([b])
            elif self.buf is not None:
                if b & 0x80 and b != 0xF7:  # any other status aborts the message
                    self.buf = None
                    continue
                self.buf.append(b)
                if b == 0xF7:
                    out.append(bytes(self.buf))
                    self.buf = None
        return out


def collect(next_msg, accept, idle, stop=lambda m: False, first_wait=None):
    """Collect SysEx messages (full F0..F7 bytes) that accept() keeps.

    next_msg(timeout) returns one message or None. Ends when stop(msg) is
    true, or after `idle` seconds without an accepted message (`first_wait`
    for the first one)."""
    got = []
    deadline = time.monotonic() + (first_wait if first_wait is not None else idle)
    while (left := deadline - time.monotonic()) > 0:
        raw = next_msg(left)
        if raw is None or not accept(raw):
            continue
        got.append(raw)
        if stop(raw):
            break
        deadline = time.monotonic() + idle
    return got


class Device:
    def __init__(self, mido, name):
        self.mido = mido
        self.name = name
        self.raw = raw_midi_path(name) if sys.platform.startswith("linux") else None
        self.open()

    def open(self):
        self.inp = self.mido.open_input(self.name)
        self.out = self.mido.open_output(self.name)

    def close(self):
        self.inp.close()
        self.out.close()

    def drain(self):
        for _ in self.inp.iter_pending():
            pass

    def send_sysex(self, msg):
        assert msg[0] == 0xF0 and msg[-1] == 0xF7
        self.out.send(self.mido.Message("sysex", data=list(msg[1:-1])))

    def send(self, msg):
        self.out.send(msg)

    def _next_mido(self, timeout):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            m = self.inp.poll()
            if m is None:
                time.sleep(0.0005)
            elif m.type == "sysex":
                return bytes([0xF0, *m.data, 0xF7])
        return None

    def next_sysex(self, timeout):
        """The next received SysEx (F0..F7 bytes), or None after `timeout` seconds."""
        return self._next_mido(timeout)

    def receive(self, accept, idle, stop=lambda m: False, first_wait=None):
        return collect(self._next_mido, accept, idle, stop, first_wait)

    def transfer(self, request, accept, idle, stop, first_wait):
        """Send request and collect the reply; on Linux via rawmidi.

        Returns (messages, how). The mido ports are closed meanwhile, since
        the sequencer holds the rawmidi device while they are open."""
        if self.raw is None:
            self.drain()
            self.send_sysex(request)
            return self.receive(accept, idle, stop, first_wait), "mido"
        self.close()
        try:
            fd = os.open(self.raw, os.O_RDWR | os.O_NONBLOCK)
        except OSError as e:
            path, self.raw = self.raw, None
            self.open()
            msgs, _ = self.transfer(request, accept, idle, stop, first_wait)
            return msgs, f"mido ({path} failed: {e})"
        split, pending = SysexSplitter(), []

        def next_raw(timeout):
            end = time.monotonic() + timeout
            while not pending:
                left = end - time.monotonic()
                if left <= 0 or not select.select([fd], [], [], left)[0]:
                    return None
                pending.extend(split.feed(os.read(fd, 65536)))
            return pending.pop(0)
        try:
            # Linux starts rawmidi input on the first poll or read; start it
            # (and drop stale bytes) before the request, or the reply's first
            # message can be lost.
            while select.select([fd], [], [], 0.05)[0]:
                os.read(fd, 65536)
            os.write(fd, request)
            msgs = collect(next_raw, accept, idle, stop, first_wait)
        finally:
            os.close(fd)
            self.open()
        return msgs, f"rawmidi {self.raw}"


def is_bootloader_port(name):
    """Components' test: the bootloader's port name says Bootloader or BL."""
    return "Circuit Mono Station Bootloader" in name or "Circuit Mono Station BL" in name


def matching_ports(names, wanted, bootloader):
    if wanted:
        names = [n for n in names if wanted.lower() in n.lower()]
    else:
        names = [n for n in names if "circuit mono station" in n.lower()]
    return [n for n in names if is_bootloader_port(n) == bootloader]


def find_port(mido, wanted, bootloader=False, wait=0):
    """The one app (or bootloader) port; polls up to `wait` seconds for it."""
    deadline = time.monotonic() + wait
    while True:
        try:
            ins, outs = mido.get_input_names(), mido.get_output_names()
        except Exception as e:  # ALSA drops ports while the unit re-enumerates
            if time.monotonic() >= deadline:
                sys.exit(f"cannot list MIDI ports: {e}")
            time.sleep(0.25)
            continue
        hits = matching_ports([n for n in ins if n in outs], wanted, bootloader)
        if len(hits) == 1:
            return hits[0]
        if time.monotonic() >= deadline:
            if wait and not hits:
                return None
            sys.exit(f"need exactly one matching port, found {hits!r}; "
                     f"ports: {ins!r} (use --port)")
        time.sleep(0.25)


def query_version(dev):
    dev.drain()
    dev.send_sysex(VERSION_REQUEST)
    v = dev.receive(lambda m: m[:6] == VERSION_REQUEST[:6] and len(m) >= 17, 2,
                    stop=lambda m: True)
    return decode_version(v[0]) if v else None


def dump(dev, start, length, path, idle, log):
    """0x78 request; save the raw reply to path. Returns a result dict."""
    log(f"request 0x{start:X}/0x{length:X} -> {path.name}")
    t0 = time.monotonic()
    msgs, how = dev.transfer(sessionsyx.request(start, length), is_transfer_message, idle,
                             stop=lambda m: m[5] == sessionsyx.CMD_TRAILER,
                             first_wait=max(idle, 5))
    raw = b"".join(msgs)
    path.write_bytes(raw)
    res = {"file": path.name, "via": how, "messages": len(msgs), "bytes": len(raw),
           "seconds": round(time.monotonic() - t0, 1)}
    if not msgs:
        res["error"] = "no reply"
        return res, None
    head = msgs[0]
    if head[5] == sessionsyx.CMD_HEADER:
        body = head[6:-1]
        res["echo_start"] = sessionsyx.from_nibbles(body[:8])
        res["echo_length"] = sessionsyx.from_nibbles(body[8:16])
    try:
        got_start, stream = sessionsyx.decode_transfer(raw)
    except (ValueError, IndexError) as e:
        res["error"] = str(e)
        return res, None
    res.update(ok=True, start=got_start, length=len(stream))
    return res, stream


def read_hidden(dev, outdir, log):
    """Test 2: 128 op-2 reads over 0xA000-0x12000."""
    out = bytearray()
    for addr in range(HIDDEN_START, HIDDEN_END, P40_READ_BYTES):
        dev.drain()
        dev.send_sysex(p40_read_request(addr))
        reply = dev.receive(lambda m: m[:10] == P40_PREFIX + bytes([P40_READ]), 2,
                            stop=lambda m: True)
        if not reply:
            return {"error": f"no reply at 0x{addr:X}", "bytes": len(out)}, None
        try:
            out += decode_p40_reply(reply[0])
        except ValueError as e:
            return {"error": f"0x{addr:X}: {e}", "bytes": len(out)}, None
        if addr % 0x1000 == 0:
            log(f"  p40 read 0x{addr:05X}")
    (outdir / "slots-64-127.bin").write_bytes(out)
    return {"ok": True, "file": "slots-64-127.bin", "bytes": len(out)}, bytes(out)


def nrpn(msb, lsb, value):
    """(cc, value) pairs for an NRPN write: CC 99, CC 98, data entry CC 6."""
    return [(99, msb), (98, lsb), (6, value)]


def ask(prompt):
    return input(prompt + " ").strip()


def trigger_test(dev, channel, log):
    """Envelope Trigger (patch byte 27, NRPN 0:73): what do 0 and 1 do?"""
    mido = dev.mido
    res = {"channel": channel, "nrpn": "0:73"}
    ask("Trigger test. Raise Amp Env Attack to about half so a restart is easy to "
        "hear, then press Enter.")
    for value in (0, 1):
        for cc, v in nrpn(*TRIGGER_NRPN, value):
            dev.send(mido.Message("control_change", channel=channel - 1, control=cc, value=v))
        log(f"sent NRPN 0:73 = {value} on ch {channel}")
        res[f"value{value}"] = ask(
            f"Trigger = {value}: hold one key, then press a second key while still "
            "holding the first. Does the envelope restart (attack replays) on the "
            "second key, or continue? [restart/continue/?]")
    res["notes"] = ask("Anything else to note? (Enter to skip)")
    return res


def pc_test(dev, channel, log):
    """Test 3: program change 63 and 64, answers typed by the user."""
    mido = dev.mido
    res = {"channel": channel}
    ask("Turn the Mono Station's volume DOWN, open the patch view, then press Enter.")
    for prog in (63, 64):
        dev.send(mido.Message("program_change", channel=channel - 1, program=prog))
        log(f"sent program change {prog} on ch {channel}")
        time.sleep(0.5)
        res[f"pc{prog}"] = {
            "sound_changed": ask(f"PC {prog}: did the sound change? [y/n/?]"),
            "display": ask(f"PC {prog}: what does the display show?"),
            "lit_pad": ask(f"PC {prog}: which patch pad is lit, on which page "
                           "(Oct up/down)? (e.g. 'p2 pad 32', 'none')"),
        }
    dev.send(mido.Message("program_change", channel=channel - 1, program=0))
    log("sent program change 0 to return to slot 0 (don't press Save)")
    res["notes"] = ask("Anything else to note? (Enter to skip)")
    return res


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("outdir", type=Path)
    ap.add_argument("--port", help="substring of the MIDI port name")
    ap.add_argument("--channel", type=int, default=1, help="Osc 1 MIDI channel (1-16)")
    ap.add_argument("--idle", type=float, default=3, help="dump idle timeout, seconds")
    ap.add_argument("--skip-session-backup", action="store_true")
    ap.add_argument("--skip-trigger", action="store_true", help="skip the Trigger test")
    ap.add_argument("--skip-pc", action="store_true", help="skip test 3")
    args = ap.parse_args(argv[1:])
    if not 1 <= args.channel <= 16:
        ap.error("--channel must be 1-16")

    try:
        import mido
    except ImportError:
        sys.exit("needs mido + python-rtmidi: pip install mido python-rtmidi")
    init = initial_patch(FIRMWARE_BIN.read_bytes()) if FIRMWARE_BIN.exists() else None

    args.outdir.mkdir(parents=True, exist_ok=True)
    report = {"date": time.strftime("%Y-%m-%dT%H:%M:%S"), "tests": {}}
    report_path = args.outdir / "report.json"

    def save():
        report_path.write_text(json.dumps(report, indent=2) + "\n")

    def log(s):
        print(s, flush=True)

    port = find_port(mido, args.port)
    report["port"] = port
    dev = Device(mido, port)
    t = report["tests"]
    try:
        v = query_version(dev)
        if v:
            report["bootloader_version"], report["firmware_version"] = v
            log(f"bootloader {v[0]}, firmware {v[1]}")
        else:
            log("no 0x70 version reply")
        if report.get("firmware_version") not in (None, 800):
            log("warning: plan was written for firmware 800")
        if init is None:
            log(f"warning: {FIRMWARE_BIN} missing; slots can't be compared to Initial Patch")
        save()

        # Into the bootloader, which serves the 0x78 dumps
        log("rebooting into the bootloader (0x71)")
        dev.send_sysex(REBOOT_TO_BOOTLOADER)
        dev.close()
        bl_port = find_port(mido, None, bootloader=True, wait=BOOTLOADER_WAIT)
        if bl_port is None:
            sys.exit(f"no bootloader port within {BOOTLOADER_WAIT} s; "
                     f"ports: {mido.get_input_names()!r}")
        time.sleep(0.1)
        report["bootloader_port"] = bl_port
        dev = Device(mido, bl_port)
        log(f"bootloader port: {bl_port}")
        report["bootloader_reply_0x70"] = query_version(dev)
        save()

        # 0: backups
        t["0_patches64"], visible = dump(dev, sessionsyx.PATCHES_START, sessionsyx.PATCHES_LENGTH,
                                         args.outdir / "patches-64.syx", args.idle, log)
        save()
        if visible is None:
            sys.exit(f"patch backup failed: {t['0_patches64']}; stopping before any other test")
        if not args.skip_session_backup:
            t["0_sessions"], _ = dump(dev, sessionsyx.SESSIONS_START, SESSIONS_LENGTH,
                                      args.outdir / "sessions.syx", max(args.idle, 10), log)
            save()
            if not t["0_sessions"].get("ok"):
                sys.exit(f"session backup failed: {t['0_sessions']}; stopping")

        # 1: one dump of all 128 slots
        t["1_dump128"], full = dump(dev, sessionsyx.PATCHES_START, ALL_SLOTS * SLOT_SIZE,
                                    args.outdir / "patches-128.syx", args.idle, log)
        save()

        # Back to the app
        dev.close()
        while True:
            ask("Press Play on the Mono Station to leave the bootloader, then press Enter.")
            port = find_port(mido, args.port, wait=10)
            if port:
                break
            log("app port not back yet")
        time.sleep(1)
        dev = Device(mido, port)
        log(f"app port: {port}")

        hidden = None
        if full is not None and len(full) == ALL_SLOTS * SLOT_SIZE:
            hidden = full[VISIBLE_SLOTS * SLOT_SIZE:]
            t["1_dump128"]["slots_0_63_match_backup"] = \
                full[:VISIBLE_SLOTS * SLOT_SIZE] == visible
            log("test 1: full 128-slot dump accepted")
        else:
            log("test 1: no full 128-slot dump; running test 2 (p 0x40 op 2 reads)")
            t["2_p40"], hidden = read_hidden(dev, args.outdir, log)
            save()

        if hidden is not None and init is not None:
            slots = classify(hidden, VISIBLE_SLOTS, init)
            report["hidden_slots"] = {"summary": summarise(slots), "slots": slots}
            report["visible_slots_summary"] = summarise(classify(visible, 0, init))
            log(f"slots 64-127: {report['hidden_slots']['summary']}")
        save()

        # Trigger listening test; test 3's program change 0 then reloads a patch
        if not args.skip_trigger:
            t["trigger"] = trigger_test(dev, args.channel, log)
            save()

        # 3: program change, only once slot 64's contents are known
        if args.skip_pc:
            pass
        elif hidden is None:
            log("skipping test 3: slot 64 contents unknown")
        else:
            t["3_program_change"] = pc_test(dev, args.channel, log)
            save()
        if not args.skip_trigger and "3_program_change" not in t:
            log("Trigger was changed on the current patch: reload it from its pad "
                "and don't press Save")
    finally:
        dev.close()
        save()
    log(f"report: {report_path}")


if __name__ == "__main__":
    main(sys.argv)
