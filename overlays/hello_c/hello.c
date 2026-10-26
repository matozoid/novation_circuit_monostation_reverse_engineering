/* Entered from the reset handler in place of main, after .data and .bss are set up. */
#include "firmware.h"

void main(void)
{
    stock_main();
}
