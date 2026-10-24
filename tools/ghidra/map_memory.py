# Ghidra pre-script (Jython): lay out the STM32F405 memory map around the imported
# application image, so pointers into RAM, peripherals and the bootloader resolve.
# Run via import_firmware.sh. See docs/hardware.md for the addresses.
# @category Monostation

from ghidra.program.model.mem import MemoryConflictException

mem = currentProgram.getMemory()
space = currentProgram.getAddressFactory().getDefaultAddressSpace()


def addr(value):
    return space.getAddress(value)


def uninitialized(name, start, length, read, write, execute):
    try:
        block = mem.createUninitializedBlock(name, addr(start), length, False)
        block.setRead(read)
        block.setWrite(write)
        block.setExecute(execute)
        block.setVolatile("periph" in name or "scs" in name)
        print("  + %-14s %#010x +%#x" % (name, start, length))
    except MemoryConflictException:
        print("  = %-14s already present (image block)" % name)


# The 48 KB bootloader is not in the image; leave it uninitialized to be filled from a dump (#24).
uninitialized("bootloader", 0x08000000, 0x0000C000, True, False, True)
# RAM.
uninitialized("ccm", 0x10000000, 0x00010000, True, True, False)
uninitialized("sram", 0x20000000, 0x00020000, True, True, False)
uninitialized("backup_sram", 0x40024000, 0x00001000, True, True, False)
# Peripheral spaces (volatile; no auto-analysis as code).
uninitialized("periph_apb1", 0x40000000, 0x00008000, True, True, False)
uninitialized("periph_apb2", 0x40010000, 0x00008000, True, True, False)
uninitialized("periph_ahb1", 0x40020000, 0x00008000, True, True, False)
uninitialized("otg_hs", 0x40040000, 0x00040000, True, True, False)
uninitialized("otg_fs", 0x50000000, 0x00040000, True, True, False)
uninitialized("system_mem", 0x1FFF0000, 0x00008000, True, False, True)  # ST ROM bootloader + OTP + UID
uninitialized("scs", 0xE000E000, 0x00001000, True, True, False)         # SCB/NVIC/SysTick

# Point the disassembler at the reset handler; auto-analysis walks the vector table from there.
reset = mem.getInt(addr(0x0800C004)) & 0xFFFFFFFE
print("  reset handler %#010x" % reset)
disassemble(addr(reset))
createFunction(addr(reset), "Reset_Handler")
addEntryPoint(addr(reset))
