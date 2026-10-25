# Hardware test: patch slots 64–127 (#31)

The firmware's patch store has 128 slots (`docs/sysex-spec.md`, *Patch store*), but the panel and Components use only 64. This plan checks on a real unit what is stored in slots 64–127 and whether program change and SysEx can reach them. Tests 1–3 only read. Tests 4–5 write, but only to slot 64, which the panel can't reach.

## Automated run

`tools/hwtest_slots.py` runs tests 0–3 and needs `pip install mido python-rtmidi`; it works on Linux and Windows. Test 4 (write) and test 5 (request) stay manual.

```sh
python3 tools/hwtest_slots.py ~/ms-dumps            # --port NAME, --channel N, --skip-session-backup, --skip-trigger, --skip-pc
```

What the script does:
1. It checks the version with `0x70`.
2. It reboots into the bootloader with `F0 00 20 29 00 71 00 7F 00 00 F7` and waits up to 8 s for the bootloader port. The `0x78` storage dumps are served only by the bootloader; the app ignores them (it has no `0x77`/`0x78`/`0x79` code), and Components also switches to the bootloader before a backup.
3. In the bootloader, it backs up the patches and the sessions. If a backup fails, it stops before running anything else.
4. It tries the 128-slot dump.
5. It asks you to press Play to leave the bootloader, then waits for the app port.
6. Back in the app, if the 128-slot dump failed, it falls back to `p 0x40` op 2 reads. It never sends op 0 or op 1.
7. It classifies slots 64–127 and checks that slots 0–63 match the backup.
8. It sets the envelope Trigger (patch byte 27, NRPN 0:73 on the Osc 1 channel) to 0 and then to 1. Each time it asks whether a second key, pressed while the first is still held, restarts the envelope. This changes only the current patch's working copy. Skip it with `--skip-trigger`.
9. It sends PC 63 and PC 64 on the Osc 1 channel, prompting for what you hear and see, then PC 0. The PC 0 also undoes the Trigger change.

On Linux the dumps are read from the ALSA rawmidi device (`/dev/snd/midiC*D*`, found through `/proc/asound`), like `amidi`. Through the sequencer, a session dump lost blocks to input overruns. The dumps and `report.json` are written to the output directory. The manual steps below do the same with `amidi`.

**Result of the first run (2026-10-25):** slots 64–127 all hold `Initial Patch`, and PC 64 loads slot 64. Details are in #31.

## Setup

- Linux with `alsa-utils` (`amidi`). Connect the Mono Station over USB, running stock firmware 800.
- Find the port: `amidi -l`. Below it is called `$P` (e.g. `P=hw:2,0,0`).
- Tests 0 and 1 need the **bootloader**: send `amidi -p $P -S 'F0 00 20 29 00 71 00 7F 00 00 F7'` (or hold Scales + Note + Velocity at power-on), run `amidi -l` again for the bootloader port (its name contains `Bootloader` or `BL`) and use that as `$P`. Press Play to return to the app before tests 2–5.
- Turn the output volume down before test 3. A slot full of `FF` would set every parameter to its maximum.
- Run the decode snippets from the repo root. Keep the dumps outside the repo, e.g. in `~/ms-dumps/`.

## 0. Back up first

Save the 64 visible patches and the sessions, so anything can be restored afterwards:

```sh
mkdir -p ~/ms-dumps && cd ~/ms-dumps
# all 64 patches: start 0x2000, length 0x8000 (what Components requests)
amidi -p $P -S 'F0 00 20 29 00 78 00 00 00 00 02 00 00 00 00 00 00 00 08 00 00 00 F7' -r patches-64.syx -t 5
# all sessions: start 0x12000, length 0x2D6000 (big; allow time)
amidi -p $P -S 'F0 00 20 29 00 78 00 00 00 01 02 00 00 00 00 00 02 0D 06 00 00 00 F7' -r sessions.syx -t 30
python3 tools/sessionsyx.py list ~/ms-dumps/sessions.syx
```

`-t` is the idle timeout in seconds; raise it if a dump ends early. Check that the patch dump decodes:

```sh
python3 -c "
import sys; sys.path.insert(0, 'tools'); import sessionsyx
start, s = sessionsyx.decode_transfer(open(sys.argv[1], 'rb').read())
print(hex(start), len(s), 'bytes, CRC ok')" ~/ms-dumps/patches-64.syx
```

Expect `0x2000 32768`. Components can also make a backup of patches and sessions.

## 1. Dump all 128 slots in one request

Same command, twice the length (`0x10000`):

```sh
amidi -p $P -S 'F0 00 20 29 00 78 00 00 00 00 02 00 00 00 00 00 00 01 00 00 00 00 F7' -r ~/ms-dumps/patches-128.syx -t 5
```

Result:
- **A `0x77` header echoing start `0x2000` and length `0x10000`, followed by blocks and a CRC trailer**: the dump handler takes any range. This also answers the open question in `docs/session-transport.md`. Go to *Reading the slots* below.
- **No reply, or a reply that is still 64 slots long**: the handler limits the range. Use test 2 instead.

## 2. Fallback: read the hidden region with `p 0x40` op 2

Op 2 of the external-flash command reads 256 bytes (`docs/sysex-handlers.md`), so 128 reads cover `0xA000`–`0x12000`. Use **only op 2**: op 0 erases a 4 KiB sector and op 1 programs flash.

```sh
python3 - ~/ms-dumps <<'EOF'
import subprocess, sys
P = "hw:2,0,0"                         # set to your port
for addr in range(0xA000, 0x12000, 0x100):
    nib = " ".join(f"{(addr >> s) & 0xF:02X}" for s in range(28, -4, -4))
    msg = f"F0 00 20 29 01 61 70 40 00 02 {nib} F7"
    subprocess.run(["amidi", "-p", P, "-S", msg, "-r", f"{sys.argv[1]}/p40-{addr:05x}.syx", "-t", "1"], check=True)
EOF
python3 - ~/ms-dumps <<'EOF'
import glob, sys
out = bytearray()
for f in sorted(glob.glob(f"{sys.argv[1]}/p40-*.syx")):
    r = open(f, "rb").read()
    assert len(r) == 523 and r[0] == 0xF0 and r[-1] == 0xF7, f
    n = r[10:-1]
    out += bytes(n[i] << 4 | n[i + 1] for i in range(0, 512, 2))
open(f"{sys.argv[1]}/slots-64-127.bin", "wb").write(out)
print(len(out), "bytes")
EOF
```

Expect `32768 bytes`. Slot *n* (64–127) starts at offset `(n − 64) × 512`.

## Reading the slots

Each slot holds the bare 126-byte patch (name first) at the start of its 512 bytes. Classify each one:

```sh
python3 - <<'EOF' ~/ms-dumps/patches-128.syx
import sys; sys.path.insert(0, 'tools'); import sessionsyx
data = open(sys.argv[1], 'rb').read()
_, s = sessionsyx.decode_transfer(data) if data[:1] == b'\xF0' else (0, data)
first = 0 if len(s) == 0x10000 else 64          # slots-64-127.bin holds 64..127
init = open('firmware/monostation-firmware-800.bin', 'rb').read()[0x08028F6B - 0x0800C000:][:126]
for i in range(len(s) // 512):
    p = s[i * 512:i * 512 + 126]
    kind = 'erased FF' if p == b'\xFF' * 126 else 'Initial Patch' if p == init else 'data'
    print(f"{first + i:3d}  {kind:13s}  {p[:16].decode('latin1')!r}")
EOF
```

For the fallback, pass `~/ms-dumps/slots-64-127.bin` instead.

Record:
- the class of slots 64–127: all `FF`, all `Initial Patch`, or real patches (which could be a factory or test bank, #8);
- whether slots 0–63 match `patches-64.syx`. They should.

## 3. Program change 64

Only do this if test 1 or 2 finished, so the contents of slot 64 are known. Turn the volume down if slot 64 is `FF`.

The patch program change goes on the Osc 1 MIDI channel, default ch 1 (status `C0`). Check *Settings* if the channel was changed.

```sh
amidi -p $P -S 'C0 3F'    # PC 63: the last visible slot, as a control
amidi -p $P -S 'C0 40'    # PC 64: first hidden slot
```

Record:
- whether the sound changes and what the display shows;
- which pad, if any, is lit on the patch view, on either page. The firmware renders only pages 0 and 1, so a current patch of 64 should light no pad.

Return to a normal patch with `C0 00` or a pad press. Loading a patch changes only the current session's working copy, so nothing is saved unless you press Save. **Don't save while slot 64 is loaded.**

Expected from the firmware: PC 64 loads slot 64 (#31). If nothing happens, the clamp is somewhere not yet traced. Note this in #31.

## 4. Optional: write slot 64 over SysEx

This writes flash, but only slot 64. Use a known patch from the backup, e.g. slot 0 of `patches-64.syx`, re-framed as a type-1 slot write to slot `0x40`:

```sh
python3 - ~/ms-dumps <<'EOF'
import sys; sys.path.insert(0, 'tools'); import sessionsyx
_, s = sessionsyx.decode_transfer(open(f"{sys.argv[1]}/patches-64.syx", 'rb').read())
msg = bytes([0xF0, 0x00, 0x20, 0x29, 0x01, 0x61, 0x01, 0x40, 0x00]) + s[:126] + b'\xF7'
assert len(msg) == 136
open(f"{sys.argv[1]}/write-slot64.syx", 'wb').write(msg)
EOF
amidi -p $P -s ~/ms-dumps/write-slot64.syx
```

Then rerun test 1 or 2 and check that slot 64 now matches slot 0. Then send `C0 40` and check that it plays the same as slot 0.

## 5. Optional: request slot 64

```sh
amidi -p $P -S 'F0 00 20 29 01 61 40 40 00 F7' -r ~/ms-dumps/req-slot64.syx -t 2
```

The type-`0x40` request isn't confirmed on hardware for any slot (#12), so run `40 00` (slot 0) first as a control. A 136-byte reply for slot 64 that matches slot 0 means the request path also accepts the hidden slots.

## Undo

- Slot 64 after test 4: write it back to its original content from the dump (same message, different payload). If it was all `FF`, an erase would be needed, which the slot-write path doesn't do. Leaving the copied patch there is harmless, because the panel can't reach it.
- Patches and sessions: restore from `patches-64.syx` and `sessions.syx` with Components, or by sending them back as uploads (`docs/session-transport.md`).

## Write the results into

- #31 in `issues.md`: contents of slots 64–127, the dump range limit, program change 64, and the outcomes of the slot write and the request;
- `docs/session-transport.md` *Open*: whether other start/length values are accepted;
- #12: whether the `0x40` request works.
- #12 and `docs/sysex-spec.md` byte 27: what Trigger 0 and 1 do (`report.json` → `tests.trigger`).
