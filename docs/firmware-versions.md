# Official firmware versions

Novation released three Mono Station firmware versions. The list and the feature summaries come from the firmware catalogue in Components 1.72.5 (`resources/firmwares.json` inside `app.asar`, entries with `product: "monostation"`), checked against Novation's *Circuit Mono Station v1.2 New Features User Guide* (2018-06-15). The summaries below are paraphrased; the catalogue holds the full release notes.

| Version | Build | Released | Catalogue id | File |
|---------|-------|----------|--------------|------|
| 1.0 | unknown | 2017 (launch) | not listed | none found |
| 1.1 | 745 | by June 2017 | 9 | `url: null`; `/api/v2/firmwares/9/file` returned HTTP 500 (2026-10-25) |
| 1.2 | 800 | 2018-06-15 | 8 | bundled in Components as `monostation-firmware-800.syx` |

The build number is what the firmware reports in its `0x70` version reply and in the image header (`docs/sysex-handlers.md`); Components compares it to decide whether to offer an update. Our mods use builds between 745 and 800 (`README.md`).

Where the 1.2 file comes from: Components 1.72.5 bundles it as `resources/firmwares/monostation-firmware-800.syx` inside `app.asar` (Windows installer → Squirrel `Components-1.72.5-full.nupkg` → `lib/net45/resources/app.asar`), with a second copy at `resources/mididata/`. Components' constant `MONOSTATION_LATEST_FIRMWARE_VERSION` is 800, and its upstream URL is `https://s3-eu-west-1.amazonaws.com/circuit-content/f53741d5f0a40a808d29ac4f137222a4e0974ce3/monostation-firmware-800.syx`. The same `app.asar` holds the init session `session_32.monostationsession`. A script to extract them reproducibly is #50; finding the older builds is #51.

Hashes of the 1.2 files (`firmware/`):

| File | SHA-256 |
|------|---------|
| `monostation-firmware-800.syx` | `960db2d0950123fca9aea13cac52844a8da26cc90e047faae5d9a589ec616a71` |
| `monostation-firmware-800.bin` (`tools/syx2bin.py`) | `b6cbf50a3f1ff58a9ef23e8ef270fdebae6e6ad4aa0439f8a1fa3de9230427ad` |

## 1.0

The launch firmware. It has no catalogue entry and no release notes. The Sound On Sound review was written on a pre-release build, on which 16th-note gates were always tied; Novation said that would be fixed before shipping.

## 1.1 (build 745)

- **Step automation:** while stopped, press Record and select a step, then move controls to automate that step.
- **Initialise patch:** Shift + Patches sets all synth settings to their defaults.
- **Reset mod depth:** hold Clear and turn a mod depth knob clockwise to set it to zero.
- In the Settings View (hold Shift at power-on):
  - **Clock output rates:** 1, 2, 4, 8 or 24 PPQN.
  - **Pot pickup:** a knob sends nothing until it passes its saved position.
  - **CC RX/TX:** choose whether controller data is received and/or transmitted.

## 1.2 (build 800)

- **Patch Flip:** a patch change per step.
  - With Record on during playback, pressing a pad in the Patches View records a patch change; Shift + pad records it without the note.
  - Edit: while stopped, press Record and select a step; the Patches button lights red if the step has a patch, and the Patches View shows or changes it.
  - Clear + Patches clears the pattern's patch changes, or only the selected step's in step edit.
- **Mod Seq notes as a CV sequencer:** the Mod track gets its own monophonic note sequence (Note and Gate views, real-time recording, no velocity because the Velocity View stays the mod automation). Mutate changes both the notes and the mod sequence. With the sequencer stopped, the Mod track's steps can be auditioned from the pads.
- **CV/gate source:** the Session Settings View (Shift + Sessions) picks which track drives CV/gate: Osc 1 (default), Osc 2 or Mod.
- **LFO key sync separate from clock sync:** the LFO Sync button toggles clock sync, Shift + Pitch (mod matrix) toggles key sync. The LFO phase also resets when the sequencer starts, whatever the key sync setting.
- **Re-trigger / legato envelopes:** Shift + PWM (mod matrix) toggles envelope retrigger; dim = no retrigger on overlapping notes, bright = retrigger on every note.

## Sources

- Components 1.72.5 `app.asar`, `resources/firmwares.json` (catalogue ids 8 and 9).
- *Circuit Mono Station v1.2 New Features User Guide*: https://fael-downloads-prod.focusrite.com/customer/prod/s3fs-public/novation/downloads/15689/circuit-mono-station-v1-2-new-features-user-guide.pdf
- Sound On Sound review (pre-release build, v1.1 mention): https://www.soundonsound.com/reviews/novation-circuit-mono-station
- Synthtopia on 1.2 (2018-06-15): https://www.synthtopia.com/content/2018/06/15/novation-circuit-mono-station-gets-free-update-heres-whats-new/
