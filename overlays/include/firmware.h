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

void *memcpy(void *dst, const void *src, size_t n);
void *memset(void *dst, int c, size_t n);

#endif
