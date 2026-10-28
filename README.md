# Circuit Mono Station firmware mods

New features for the Novation Circuit Mono Station, built on top of Novation's own
firmware 1.2, plus the reverse-engineering tools and notes that make them possible.

- **Plays like stock.** Each mod changes only the behaviour it adds; everything else is
  the original firmware, byte for byte.
- **Easy to undo.** The bootloader is never touched, so you can always flash stock
  firmware 1.2 back the same way.
- **No Novation code in this repo.** You supply the stock firmware yourself; the tools
  rebuild it from source generated on your machine.

> Unofficial and unsupported by Novation. Flashing modified firmware is at your own risk.

**Made with Claude.** The reverse engineering, the tools, the docs and the mods in this
repo were all done with [Claude Code](https://claude.com/claude-code). Claude can help
you too: open Claude Code in a checkout of this repo and ask it to walk you through
getting the stock firmware, building a mod and flashing it. `CLAUDE.md` gives it the
project's commands and safety rules.

## Feature: context-sensitive Mutate (#13)

Stock **Mutate** (Shift + Pattern Settings) shuffles whole steps. With this mod, Mutate
in the views below changes only one property of the steps that hold a note: the rhythm
and everything else stay as they are.

| View | Open with | Mutate now |
|------|-----------|------------|
| Velocity | Velocity | gives each note a random velocity from the velocity pads' levels |
| Gate | Gate | gives each note a random length, from 1/6 step up to the longest gate already in the pattern; one note keeps that longest gate, so the range holds press after press |
| Glide | Shift + Gate | half the notes get no glide, the others a random glide level 1–15 |
| Expand | Shift + Note | gives each note a random pitch from the notes on the 32 Expand pads: the current scale and root over four octaves (two for Chromatic) |

Tips:

- If no note is longer than one step, random gates go up to one step. For longer random
  gates, first make one note as long as you want the maximum to be.
- In Expand view the pattern is hidden: listen, or press Note to see the new notes.
- Works on the Osc 1 and Osc 2 tracks. In the Note and Pattern Settings views, and on the
  Mod Seq track, Mutate behaves as stock.

Firmware 1.2 is build 800 inside the firmware; this mod reports build **750**. That is
below 800 on purpose: Components offers an update to any unit below 800, so it should
offer to install stock 1.2 (from its code; not tried yet).

## Feature: patch randomiser (#45)

Open the **Patches** view and press **Mutate** (Shift + Pattern Settings): the current
patch gets random settings, as if every knob had been turned. Press again for another.

Every press gives a playable sound:

- Kept as they are: the octave and range of both oscillators (the patch stays in its
  register), patch level, audio-in level and the envelope-to-amp switch.
- Oscillators stay in tune: coarse and Osc 1 fine are centred, Osc 2 gets at most a
  little detune; pulse widths avoid the silent extremes.
- Osc 1 is always in the mix; the filter is never closed or bypassed, and resonance
  stays short of self-oscillation. Band and high pass follow the keys, with the band
  around the middle and the high pass low, so the notes get through.
- A low sustain always gets a decay long enough to hear.
- Most mod-matrix routings stay off, and the ones that are on get moderate depths.

The patch shows as edited, so save it the usual way if you like it, or load another
patch to throw it away. With MIDI out of the synth params on, the new values also go out
as CCs and NRPNs (some may be dropped).

The patch randomiser is part of the Mutate mod above (`overlays/randomise`).

## Feature: live transpose (#21)

Transpose the playing sequence from the keys, the way many monosynth sequencers do.

1. Open the Note view (or Expand: Shift + Note) of Osc 1 or Osc 2.
2. Hold **Shift** and tap a key. The key doesn't sound; both Osc tracks now play their
   patterns moved by that many scale degrees, counted from the first note of the
   track's pattern.
3. Hold Shift and tap the key of the pattern's first note to go back.

While you hold Shift, the key of the pattern's first note blinks yellow, and the key
you transposed to blinks blue.

The notes stay in the scale, and nothing is saved: the pattern itself doesn't change,
and loading another session starts it untransposed.
On its own this mod is `overlays/transpose` (build 751). To get both features in one
firmware (build 752), see the quick start below.

## What you need

- A Circuit Mono Station and a USB cable.
- Linux or macOS with Python 3.
- `arm-none-eabi-gcc` and `binutils-arm-none-eabi` (Debian/Ubuntu:
  `sudo apt install gcc-arm-none-eabi binutils-arm-none-eabi`).
- For flashing: a virtualenv with `mido` and `python-rtmidi`:

      python3 -m venv .venv
      .venv/bin/pip install mido python-rtmidi

- The stock firmware 1.2, `monostation-firmware-800.syx`. It ships inside Novation
  Components 1.72.5 (`resources/app.asar`; on Windows the installer holds
  `Components-1.72.5-full.nupkg`, which holds `lib/net45/resources/app.asar`). Unpack it
  with an asar extractor and copy the file to `firmware/`. Don't run the installer to get
  it; unpack it with `7z`.

## Quick start

Build the modded firmware:

    python3 tools/syx2bin.py firmware/monostation-firmware-800.syx firmware/monostation-firmware-800.bin
    python3 tools/fwsrc.py generate firmware/monostation-firmware-800.bin build/rand --overlay overlays/randomise
    python3 tools/fwsrc.py build build/rand build/rand/firmware.bin
    python3 tools/bin2syx.py build/rand/firmware.bin firmware/randomise-750.syx --version 750

For both features (Mutate and live transpose) in one firmware, build 752:

    python3 tools/fwsrc.py generate firmware/monostation-firmware-800.bin build/both --overlay overlays/randomise --overlay overlays/transpose --version 752
    python3 tools/fwsrc.py build build/both build/both/firmware.bin
    python3 tools/bin2syx.py build/both/firmware.bin firmware/mods-752.syx --version 752

Flash it (or `firmware/mods-752.syx` for both features):

    .venv/bin/python tools/flash.py firmware/randomise-750.syx

The flasher checks the file, then waits for the unit's bootloader:

1. Switch the Mono Station off.
2. Hold **Scales + Note + Velocity** and switch it on.
3. The flasher sends the firmware, waits for the unit to restart and checks that it
   reports the new version (750, or 752 for both).

Go back to stock firmware 1.2 at any time: connect the unit to Novation Components and
accept its firmware update, or flash it the same way:

    .venv/bin/python tools/flash.py firmware/monostation-firmware-800.syx

## Writing your own mods

`tools/fwsrc.py` turns the stock image into Thumb-2 assembly that rebuilds byte for byte.
An **overlay** replaces stock functions by name with your own C or assembly; the original
stays callable as `stock_<name>`. The Mutate feature is one small C file:
`overlays/randomise/randomise.c`, with prototypes of stock functions in
`overlays/include/firmware.h`. See `docs/source-build.md` (*Overlays*) for the format and
`docs/source-map.md` for what each stock function does.

Generated source is Novation's code: it stays in the git-ignored `build/` directory.
Never commit it.

## Tools

| Tool | What it does |
|------|--------------|
| `tools/syx2bin.py`, `tools/bin2syx.py` | firmware `.syx` ⇄ raw image (`docs/firmware-container.md`) |
| `tools/fwsrc.py` | generate and rebuild firmware source, with overlays (`docs/source-build.md`) |
| `tools/fwpatch.py` | apply small assembly patches with hooks (`docs/patching.md`) |
| `tools/flash.py` | flash a `.syx` through the bootloader and verify it (`docs/update-protocol.md`) |
| `tools/patchbank.py` | list and decode patches in `.monostationpack` banks (`docs/sysex-spec.md`) |
| `tools/sessionsyx.py`, `tools/session.py` | split, rebuild and inspect session dumps (`docs/session-transport.md`, `docs/session-format.md`) |
| `tools/fwmap.py`, `tools/disasm.py`, `tools/ghidra/` | firmware map, quick disassembly, Ghidra headless scripts |

## Documentation

For owners:

- [Official firmware versions](docs/firmware-versions.md): 1.0, 1.1 and 1.2, their build
  numbers and what each added.
- [Entering the bootloader by hand](docs/update-protocol.md#entering-the-bootloader-by-hand)
  and [Recovery](docs/update-protocol.md#recovery): bootloader mode, reading the version
  lights, and retrying an update that failed.
- [Patch SysEx](docs/sysex-spec.md): the 136-byte patch format, `.monostationpack` banks,
  how to request a patch, and the 64 hidden patch slots.
- [Session format](docs/session-format.md) and [session transport](docs/session-transport.md):
  what is in a session and how session backups travel over SysEx.
- [Patch slots 64–127 on the unit](docs/hw-test-patch-slots.md): what the hidden slots
  hold and what program change can reach.

For modders:

- [Firmware source build](docs/source-build.md): generating source and writing overlays.
- [Source map](docs/source-map.md): what each stock function does.
- [Sequencer](docs/sequencer.md): clock, patterns, note routing.
- [Hardware](docs/hardware.md): MCU, memory map, flash sectors, free RAM.
- [Bootloader](docs/bootloader.md), [update protocol](docs/update-protocol.md) and
  [firmware container](docs/firmware-container.md): how firmware gets onto the unit.
- [Binary patching](docs/patching.md), [SysEx handlers](docs/sysex-handlers.md),
  [firmware map](docs/firmware-map.md).
- [GitHub issues](https://github.com/matozoid/novation_circuit_monostation_reverse_engineering/issues): feature work (`enhancement`) and open research (`research`).

## Tests

    python3 -m unittest discover -s tests -v

Firmware tests skip when `firmware/monostation-firmware-800.syx` is missing; overlay
tests skip without `arm-none-eabi-gcc`.
