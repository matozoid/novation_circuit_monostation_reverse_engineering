@ Replaces the SPI read call of 'p' 0x40 op 2. r0 = address, r1 = destination, r2 = length.
@ An address inside one of the MCU ranges below is copied byte by byte; bytes past the
@ range's end read as FF. Any other address goes to the stock SPI read. Returns 0, as the
@ SPI read does; the caller then waits for the SPI to go idle, which it already is.
    .syntax unified
    .thumb
    .text

    .global p40_read
    .thumb_func
p40_read:
    push    {r4, r5, r6, lr}    @ r3 is left alone: it is passed on to the SPI read
    adr     r4, ranges
next_range:
    ldmia   r4!, {r5, r6}       @ r5 = start, r6 = end
    cbz     r6, not_mcu
    cmp     r0, r5
    blo     next_range
    cmp     r0, r6
    bhs     next_range
    subs    r6, r6, r0          @ bytes left in the range
copy:
    cbz     r2, done
    cbz     r6, fill
    ldrb    r5, [r0], #1
    strb    r5, [r1], #1
    subs    r2, #1
    subs    r6, #1
    b       copy
fill:
    movs    r5, #0xFF
fill_byte:
    strb    r5, [r1], #1
    subs    r2, #1
    bne     fill_byte
done:
    movs    r0, #0
    pop     {r4, r5, r6, pc}
not_mcu:
    pop     {r4, r5, r6, lr}
    b.w     fw_spi_read

    .align  2
ranges:                         @ STM32F405: start, end (exclusive)
    .word   0x08000000, 0x08100000  @ flash: bootloader, application, free sectors
    .word   0x1FFF0000, 0x1FFF7A24  @ system memory (ST ROM), OTP, lock bytes, unique id, flash size
    .word   0x1FFFC000, 0x1FFFC010  @ option bytes
    .word   0x10000000, 0x10010000  @ CCM RAM
    .word   0x20000000, 0x20020000  @ SRAM
    .word   0x40024000, 0x40025000  @ backup SRAM
    .word   0, 0
