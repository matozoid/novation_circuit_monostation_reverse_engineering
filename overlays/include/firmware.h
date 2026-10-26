/* Stock firmware functions for C overlays (#43). Every name in src/symbols.tsv is a
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

/* ui_button_event(ui, button): the UI's button-down router (docs/source-map.md). */
void ui_button_event(void *ui, unsigned button);
void stock_ui_button_event(void *ui, unsigned button);

void *memcpy(void *dst, const void *src, size_t n);
void *memset(void *dst, int c, size_t n);

#endif
