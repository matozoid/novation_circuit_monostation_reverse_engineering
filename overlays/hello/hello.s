@ Entered from the reset handler in place of main, after .data and .bss are set up.
    .syntax unified
    .thumb
    .text

    .global main
    .thumb_func
    .type main, %function
main:
    b.w     stock_main      @ tail call: main returns to the reset handler as before
