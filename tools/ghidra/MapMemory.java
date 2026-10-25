// Ghidra pre-script: lay out the STM32F42x memory map around the imported
// application image, so pointers into RAM, peripherals and the bootloader
// resolve. Run via import_firmware.sh. See docs/hardware.md for the addresses.
// (Java rather than Python: Ghidra 12.x headless has no bundled Jython, and
// PyGhidra needs a separate Python env.)
// @category Monostation

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;

public class MapMemory extends GhidraScript {

    private void block(String name, long start, long length,
                       boolean read, boolean write, boolean execute) throws Exception {
        Memory mem = currentProgram.getMemory();
        Address at = toAddr(start);
        try {
            MemoryBlock b = mem.createUninitializedBlock(name, at, length, false);
            b.setRead(read);
            b.setWrite(write);
            b.setExecute(execute);
            b.setVolatile(name.startsWith("periph") || name.equals("scs"));
            println(String.format("  + %-14s %#010x +%#x", name, start, length));
        } catch (Exception e) {
            println(String.format("  = %-14s skipped (%s)", name, e.getClass().getSimpleName()));
        }
    }

    @Override
    public void run() throws Exception {
        // The imported image starts with its vector table (app 0x0800C000, bootloader 0x08000000).
        Address base = currentProgram.getMinAddress();
        // 48 KB bootloader: not in the app image; skipped when the bootloader itself is imported.
        block("bootloader", 0x08000000L, 0x0000C000L, true, false, true);
        // RAM.
        block("ccm", 0x10000000L, 0x00010000L, true, true, false);
        block("sram", 0x20000000L, 0x00030000L, true, true, false);
        block("backup_sram", 0x40024000L, 0x00001000L, true, true, false);
        // Peripheral spaces (volatile; not analysed as code).
        block("periph_apb1", 0x40000000L, 0x00008000L, true, true, false);
        block("periph_apb2", 0x40010000L, 0x00008000L, true, true, false);
        block("periph_ahb1", 0x40020000L, 0x00008000L, true, true, false);
        block("otg_hs", 0x40040000L, 0x00040000L, true, true, false);
        block("otg_fs", 0x50000000L, 0x00040000L, true, true, false);
        block("system_mem", 0x1FFF0000L, 0x00008000L, true, false, true); // ST ROM + OTP + UID
        block("scs", 0xE000E000L, 0x00001000L, true, true, false);         // SCB/NVIC/SysTick

        // Point the disassembler at the reset handler; auto-analysis walks the
        // vector table from there.
        long reset = currentProgram.getMemory().getInt(base.add(4)) & 0xFFFFFFFEL;
        println(String.format("  reset handler %#010x", reset));
        Address resetAddr = toAddr(reset);
        disassemble(resetAddr);
        createFunction(resetAddr, "Reset_Handler");
        addEntryPoint(resetAddr);
    }
}
