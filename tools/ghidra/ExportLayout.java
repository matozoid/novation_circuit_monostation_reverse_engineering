// Ghidra post-script: write the code/data layout of the app image for the source
// generator (tools/fwsrc.py). The output holds addresses and kinds only, no image bytes.
//   analyzeHeadless ghidra-project monostation -process monostation-firmware-800.bin \
//     -noanalysis -scriptPath tools/ghidra -postScript ExportLayout.java <out.tsv>
// Lines (hex addresses, ranges are [start, end)):
//   C start end          instructions (Thumb)
//   D start end type     defined data
//   U start end          undefined bytes
//   R from to            data reference from a data unit (pointer words)
//   F addr name          function entry
//   B start end          one address range of a function body (after its F line)
// @category Monostation

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressRange;
import ghidra.program.model.listing.CodeUnit;
import ghidra.program.model.listing.CodeUnitIterator;
import ghidra.program.model.listing.Data;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.Reference;

import java.io.FileWriter;
import java.io.PrintWriter;

public class ExportLayout extends GhidraScript {

    private PrintWriter w;
    private String kind;          // current run: "C" or "U"
    private long runStart, runEnd;

    private void flush() {
        if (kind != null) w.println(String.format("%s %08x %08x", kind, runStart, runEnd));
        kind = null;
    }

    private void extend(String k, long start, long end) {
        if (k.equals(kind) && start == runEnd) {
            runEnd = end;
            return;
        }
        flush();
        kind = k;
        runStart = start;
        runEnd = end;
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        String out = args.length > 0 ? args[0] : "layout.tsv";
        Listing listing = currentProgram.getListing();
        Address base = toAddr(0x0800C000L);
        MemoryBlock block = currentProgram.getMemory().getBlock(base);

        try (PrintWriter pw = new PrintWriter(new FileWriter(out))) {
            w = pw;
            w.println("# code/data layout of " + currentProgram.getName()
                      + ", block " + block.getName() + " " + block.getStart() + "-" + block.getEnd());
            CodeUnitIterator it = listing.getCodeUnits(block.getStart(), true);
            while (it.hasNext()) {
                CodeUnit cu = it.next();
                if (!block.contains(cu.getMinAddress())) break;
                long s = cu.getMinAddress().getOffset();
                long e = cu.getMaxAddress().getOffset() + 1;
                if (cu instanceof Instruction) {
                    extend("C", s, e);
                } else {
                    Data d = (Data) cu;
                    if (!d.isDefined()) {
                        extend("U", s, e);
                        continue;
                    }
                    flush();
                    w.println(String.format("D %08x %08x %s", s, e,
                              d.getDataType().getName().replace(' ', '_')));
                    for (Reference r : cu.getReferencesFrom()) {
                        Address to = r.getToAddress();
                        if (to.isMemoryAddress()) {
                            w.println(String.format("R %08x %08x", r.getFromAddress().getOffset(),
                                      to.getOffset()));
                        }
                    }
                }
            }
            flush();
            for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
                w.println(String.format("F %08x %s", f.getEntryPoint().getOffset(), f.getName()));
                for (AddressRange r : f.getBody()) {
                    w.println(String.format("B %08x %08x", r.getMinAddress().getOffset(),
                              r.getMaxAddress().getOffset() + 1));
                }
            }
        }
        println("wrote " + out);
    }
}
