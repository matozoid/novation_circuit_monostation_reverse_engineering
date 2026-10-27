/* Stock firmware functions for C overlays (docs/source-build.md). Every name in src/symbols.tsv is a
 * global in the generated source, so declaring it here is enough to call it; a replaced
 * function's original is stock_<name>. Add prototypes as docs/source-map.md pins down
 * the arguments. Overlays cannot have .data or .bss: use const data, or RAM the stock
 * firmware leaves free (docs/hardware.md "Free RAM"). */
#ifndef FIRMWARE_H
#define FIRMWARE_H

#include <stddef.h>
#include <stdint.h>

void stock_main(void);              /* main: init, then the soft-timer main loop */

/* Cells (docs/source-map.md "Cells"): double-buffered values with subscribers. */
void *cell_read(void *cell);        /* front buffer */
void *cell_value(void *cell);       /* word 0 of the front buffer: a pointer for array cells */
void *cell_write_field0(void *cell); /* back buffer, field 0, marked dirty */
void cell_sync(void *cell);         /* copy front into back, mark every field dirty */
void cell_commit(void *cell);       /* swap and notify subscribers */

uint32_t rng_read(void);            /* hardware RNG, 0 on timeout */

extern const uint8_t pad_levels[16]; /* 0, 8, 17, ..., 127: glide and velocity pad levels */

/* Index into the keyboard layout's pad-to-note table (layout = keyboard view +0x30,
 * notes at layout +0x14, 0 = no key) for a pad of the 16 (32 in Expand view). */
uint8_t keyboard_pad_to_note(void *keyboard_view, unsigned pad);

/* Keyboard view LEDs: one entry of the layout (0-0xdf) in its scale/sounding colour. */
void keyboard_leds_render(void *keyboard_view, unsigned entry);
void stock_keyboard_leds_render(void *keyboard_view, unsigned entry);

/* Cell subscriber of a flag LED (this = LED object +0xc): colour 0x0d/0x0e from its flag.
 * The Shift LED (ui +0x10) is one. */
void flag_led_render_thunk(void *subscriber);
void stock_flag_led_render_thunk(void *subscriber);

/* Per LED frame (from the main loop): animates the palette, resolves the colour indices
 * of the LED buffers; returns 1. */
int led_frame_compose(void *leds, unsigned frame, void *ui);
int stock_led_frame_compose(void *leds, unsigned frame, void *ui);

/* Callback of the session read from flash (session control in r3; r0-r2 from the
 * flash driver): checks the magic, then loads the session's globals or a default one. */
void session_ctl_on_save_done(unsigned r0, unsigned r1, unsigned r2, void *session_ctl);
void stock_session_ctl_on_save_done(unsigned r0, unsigned r1, unsigned r2, void *session_ctl);

/* ui_button_event(ui, button): the UI's button-down router (docs/source-map.md). */
void ui_button_event(void *ui, unsigned button);
void stock_ui_button_event(void *ui, unsigned button);

/* ui_root_pad_down(ui, pad, velocity): pads 0-31, forwarded to the active view. */
void ui_root_pad_down(void *ui, unsigned pad, unsigned velocity);
void stock_ui_root_pad_down(void *ui, unsigned pad, unsigned velocity);

/* The child view that gets a pad event (pads 16-31 may go to an override view). */
void *mode_view_target(void *mode_view, unsigned pad);

/* Router (docs/sequencer.md): channels 0-2 keys, 3-5 sequencer, 6-7 MIDI in; notes are
 * relative to the root. */
void router_note_on(void *router, unsigned note, unsigned velocity, unsigned channel);
void stock_router_note_on(void *router, unsigned note, unsigned velocity, unsigned channel);
void router_note_off(void *router, unsigned note, unsigned channel);
void stock_router_note_off(void *router, unsigned note, unsigned channel);

void *memcpy(void *dst, const void *src, size_t n);
void *memset(void *dst, int c, size_t n);

#endif
