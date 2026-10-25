# Firmware update protocol

How Components 1.72.5 sends a firmware `.syx` file to a Circuit Mono Station (#4). The message formats are described in `docs/firmware-container.md`.

Sources, all in `app.asar` → `ember-dist/assets/`:
- the generic updater class `s1` in `main-*.js`, next to the `WAITING_FOR_PACKET_REQUEST` state enum;
- the status-report enum `EP`;
- the Mono Station device class (`productName="Circuit Mono Station"`, productId `[0,127]`) and its base class `H1`;
- the update component's `sendFirmwareToDevice`;
- the `chunkFirmware` handler in `decode-worker-*.js`;
- the timeouts in `ember-dist/index.html` (`MAX_WAIT_FOR_RESET_TIMEOUT` 8000, `FIRMWARE_UPDATE_TIMEOUT` 10000).

All of this is the host side, read statically. The bootloader's side has not been observed. It is inferred from what the host waits for.

The application's side (how firmware 800 handles `0x70` and `0x71`) is in `docs/sysex-handlers.md`.

## Ports and device state

Components tells the application from the bootloader by the MIDI port name:
- **bootloader:** the name contains `Circuit Mono Station Bootloader` or `Circuit Mono Station BL`;
- **application ("present"):** the name contains `Circuit Mono Station` and neither bootloader string.

**Version inquiry.** Components sends `F0 00 20 29 00 70 F7` to whichever port is active. The reply has `0x70` at byte 5. Bytes 6–10 are the bootloader version and bytes 11–15 the firmware version, each as 5 decimal digits, most significant first. An update is offered when the firmware version is below `MONOSTATION_LATEST_FIRMWARE_VERSION` (800).

Mono Station update config (`o1` defaults overridden by the device class):

| option | value | effect |
|--------|-------|--------|
| `sendToMainApplication` | `false` | the firmware itself always goes to the bootloader port |
| `waitForDeviceConfirmation` | `false` | no "press a button on the device" step |
| `listenForDeviceStatusReport` | `false` (default) | the transfer counts as done once the last packet is sent |
| `listenForPreUpdateStatusReport` | `false` (default) | |
| `waitForUpdatedFirmwareVersion` | `true` (default) | after the transfer, poll until the reported version is current |
| `waitForReboot` | `true` (default) | then wait for the application port |
| `maxWaitForResetTimeout` | 8000 ms | wait for the bootloader port to appear |
| packet timeout | 10000 ms (`FIRMWARE_UPDATE_TIMEOUT`) | wait for each acknowledgement |
| `rebootTimeout` | 10000 ms (base class) | the post-update polls run every 500 ms for up to 2 × 10 s |

## Sequence

1. **Split.** The file is cut into its SysEx messages, one packet per `F0 … F7`, in file order. For the stock 800 file that is 4,059 packets: `0x71` start, 4,056 × `0x72`, `0x73` (image offset 0), `0x76` trailer. No re-encoding happens; the packets are sent exactly as stored.

2. **Enter the bootloader.**
   - If the unit is already in the bootloader, go straight to step 3.
   - If the application is running, Components sends **packet 0, the full 15-byte `0x71` start message** (`F0 00 20 29 00 71 00 7F` + 6 version digits + `F7`), to the application's port. It then waits up to 8 s for the bootloader port to appear, plus 100 ms. If the port doesn't appear, the update fails with `errors.firmware-process.timeout`.
   - The device page's manual "reboot to bootloader" uses the short form `F0 00 20 29 00 71 00 7F 00 00 F7` instead.
   - Otherwise the update fails with `device-not-present`.

3. **Transfer, with device-driven flow control.** Components listens on the bootloader input and sends packet 0 (the `0x71` start, again) to the bootloader output. Then, for every packet:
   - send it;
   - if it is the `0x73` message (byte 5 = `0x73`) or the last packet, stop: the transfer is complete;
   - otherwise start the 10 s timeout and wait for an acknowledgement.

   **Acknowledgement:** a SysEx from the bootloader that starts `F0 00 20 29` with byte 5 = `0x72`.
   - **10-byte form** `F0 00 20 29 00 72 a b c F7`: counter = `a + (b << 7) + (c << 14)`.
   - **15-byte form:** counter = `b[13] + (b[12] << 4) + (b[11] << 8)`, three nibbles at the end of the 8 bytes at 6–13. This looks like a nibble encoding used by other products. Only the short form accepts a counter of 0 after a higher one.

   Components sends the next packet only when the counter is greater than the last one it accepted, or is 0. It starts from −1, so the first ack may be 0. Duplicate or stale acks are ignored, and a missing ack ends the update with a timeout. So the bootloader paces the transfer, presumably acknowledging each packet once it has been written or buffered.

   **Status reports:** a 10-byte SysEx with byte 5 = `0x73` and the code at byte 6. With `listenForDeviceStatusReport` off, Mono Station never needs to send one, but the handler is active:

   | code | name | host reaction |
   |------|------|---------------|
   | 0 | `initiateUpgrade` | log |
   | 1 | `waitingForUserConfirmation` | prompt |
   | 16 | `userCancelled` | abort (`user-cancelled`) |
   | 17 | `waitingForUserConfirmationTimeout` | prompt |
   | 124 | `uploadFailedCompositionNotViable` | fail (`errors.firmware-update.failed`) |
   | 126 | `uploadFailedCompositionViable` | fail |
   | 125 | `uploadSuccessfulCompositionNotViable` | log only |
   | 127 | `uploadSuccessful` | if packets remain, continue with the next one; otherwise done |

   If the port disconnects while waiting for an ack, the update fails with `device-disconnected`.

4. **After the transfer.** Components polls every 500 ms for up to about 20 s.
   - It first waits until the device is connected, reports a firmware version, and no longer needs an update. That proves the new image booted and answers `0x70` with the new version.
   - It then waits until the application port ("present") is back.

## Consequences for our own flasher (#16)

- **The `0x76` trailer is never sent.** The loop stops at the `0x73` packet, and in the stock 800 file that packet is the second to last, followed by `0x76`. So the bootloader must finish the update on `0x73`, the vector-table block at offset 0. It then reboots, since Components next waits for the new version to be reported. As far as Components is concerned, the checksum in the trailer is unused. The bootloader could still compute its own check, which #25 would answer. `tools/bin2syx.py` should keep writing a correct trailer anyway.
- **The `0x73` block must come last.** A flasher must not send anything after it, or must at least expect the device to have left the bootloader.
- **Reboot.** The app accepts any `0x71` with `00 7F` at bytes 6–7 (or a bare `0x60`), writes `2` to backup SRAM and jumps to the bootloader without a system reset (`docs/sysex-handlers.md`). A patched image must keep that path to stay updatable without the manual entry (#29).
- **Version digits.** The bootloader sees the `0x71` start message with the version digits. Whether it rejects versions it considers older or equal is unknown (#25). Components itself only offers an update when the running version is below 800. That version comes from the image header (`MAIN` + BCD at image offset `0x1B0`), not from the container digits. A modified image that reports a version of 800 or higher won't be "updated" by Components, which matters for restoring stock (#15).
- **Pacing.** Wait for each ack: the 10-byte `0x72` reply with an increasing counter. Do not stream. Use a timeout of at least 10 s per packet.
## Our flasher (`tools/flash.py`, #16)

`python3 tools/flash.py <firmware.syx> [--port NAME] [--yes]` implements the sequence above. It needs mido + python-rtmidi.
1. It decodes and checks the file first: message order (`0x71`, `0x72`…, one `0x73`, `0x76`), 44-byte data messages, product id `00 7F`, the trailer checksum, the `MAIN` header version, an image end at or below `0x08040000`, and a vector table with the stack in RAM and a Thumb reset vector inside the image.
2. It asks you to enter the bootloader by hand and waits up to 10 minutes for its port. It never sends the `0x71` reboot.
3. It sends `0x70`, then asks for confirmation unless `--yes` is given.
4. It sends every packet up to and including `0x73`, each one after the previous ack. The timeout is 10 s per ack, and status codes 16/124/126 abort.
5. It waits up to 20 s for the application port and checks that its `0x70` firmware version equals the image header version.

The packet order (0x71 once to the bootloader, then one packet per ack) follows the updater's `#Ui`/`#$i` methods. **On hardware (2026-10-25):** stock 800 was flashed over 800 with bootloader 740:
- the bootloader answers `0x70` with both versions, (740, 800);
- it acked every packet, and 4,058 packets took 7 s;
- after `0x73` the app started by itself and reported 800, so no `0x76` trailer is needed and reflashing the same version is accepted.

## Entering the bootloader by hand

From the user guide (v1.2, appendix *Bootloader Mode*, p. 90):
1. Turn the unit off.
2. Hold **Scales**, **Note** and **Velocity**.
3. Power it on.

The guide's picture shows the buttons Osc 1, Osc 2, Mod Seq, Oct ↓, Oct ↑, Clear, Save and Sessions, with Osc 1 and Osc 2 lit. Pressing Osc 1 or Osc 2 shows a firmware element's version number as a binary pad pattern. **Observed (2026-10-25), with patch `hello` (version 801) flashed:** Osc 2 lit four pad rows as `00001000`, `00000001`, `00000000`, `00000000`. That is the app's header version, BCD `0x0801` from image offset `0x1B0`, high byte first. So the bootloader reads the app's `MAIN` header itself. Rows 3–4 are probably the next two header bytes (`0x1B2`–`0x1B3`, `00 00`), but that isn't tested. Osc 1 shows `00000111 01000000`: the bootloader's version 740 (BCD `0x0740`) in the same format, presumably from its own header at `0x080001AC`. **Play** exits and reboots into the application. The guide calls this an "engineering mode" for updating firmware, **and factory patches**, when a normal update fails. Novation's support article on bootloader mode gives the same combination.

The application has no code for this. Its only jump to `0x08000000` is the SysEx reboot (`docs/sysex-handlers.md`). So the bootloader must read the buttons itself before it starts the app, and **the combination should work even when the application image is broken.** Inferred, not yet tried on hardware.

The application has other power-on combinations, which are not bootloader entries:
- **Shift:** System Settings;
- **Shift + Save:** toggle save lock;
- **Shift + Clear:** load the Init Patch instead of the last session.

## Recovery

- **Recovery path.** Components starts directly at step 3 when the bootloader port is already present, for example when the unit was booted into the bootloader by hand. A failed transfer can be retried that way, provided the bootloader region itself is untouched.

## Not yet known

- Whether the bootloader's button check really runs before any check of the app image. This needs a test on hardware.
- The exact ack timing and whether the counter equals the packet index, which needs a capture of a real update.
- What the bootloader checks: product id, version, block count, checksum (#25).
