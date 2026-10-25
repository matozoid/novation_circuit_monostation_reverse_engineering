@ Entered from the reset handler in place of main, after .data and .bss are set up.
    .syntax unified
    .thumb
    .text

    .global hello_main
    .thumb_func
hello_main:
    b.w     fw_main         @ tail call: main returns to the reset handler as before
