// Ghidra post-script: summarise the analysed program, locate key functions by
// their known data xrefs (from docs/firmware-map.md), search for the Novation
// SysEx manufacturer id, and write a function listing to docs/ghidra-functions.txt.
// Run via import_firmware.sh (it chains this after analysis) or:
//   analyzeHeadless ghidra-project monostation -process monostation-firmware-800.bin \
//     -noanalysis -scriptPath tools/ghidra -postScript ExportMap.java <repo_root>
// @category Monostation

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.symbol.Reference;
import ghidra.program.model.symbol.ReferenceManager;

import java.io.File;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.util.LinkedHashSet;
import java.util.Set;

public class ExportMap extends GhidraScript {

    private FunctionManager fm;
    private ReferenceManager ref;

    // Functions that reference `addr`, following one hop through a literal-pool
    // word when the direct referrer is loose data (ARM `ldr rX,[pc,#..]` loads a
    // pool word that holds the target; the pool word itself is not in a function).
    private Set<Function> callers(Address addr) {
        Set<Function> out = new LinkedHashSet<>();
        for (Reference r : ref.getReferencesTo(addr)) {
            Address from = r.getFromAddress();
            Function f = fm.getFunctionContaining(from);
            if (f != null) {
                out.add(f);
            } else {
                for (Reference r2 : ref.getReferencesTo(from)) {
                    Function f2 = fm.getFunctionContaining(r2.getFromAddress());
                    if (f2 != null) out.add(f2);
                }
            }
        }
        return out;
    }

    private String names(Set<Function> fs) {
        if (fs.isEmpty()) return "(none)";
        StringBuilder sb = new StringBuilder();
        for (Function f : fs) {
            if (sb.length() > 0) sb.append(", ");
            sb.append(f.getName()).append('@').append(f.getEntryPoint());
        }
        return sb.toString();
    }

    @Override
    public void run() throws Exception {
        fm = currentProgram.getFunctionManager();
        ref = currentProgram.getReferenceManager();
        Listing listing = currentProgram.getListing();
        String[] args = getScriptArgs();
        String repo = args.length > 0 ? args[0] : ".";

        int count = fm.getFunctionCount();
        println("== " + count + " functions ==");

        // Known data anchors from the static pass (docs/firmware-map.md).
        String[][] anchors = {
            {"ui_pad_string", "0x080281e4"},
            {"ui_pot_string", "0x080281a9"},
            {"usb_product_string", "0x08028ed3"},
            {"patch_default", "0x08028f6b"},
            {"session_default", "0x08028d70"},
        };
        println("\n== Functions referencing known data anchors ==");
        for (String[] a : anchors) {
            Address at = toAddr(Long.decode(a[1]).longValue());
            println(String.format("  %-20s %s -> %s", a[0], at, names(callers(at))));
        }

        // The Novation SysEx stream is F0 00 20 29 ...; find where the parser
        // holds the manufacturer id 00 20 29.
        println("\n== Byte search 00 20 29 (Novation mfr id) ==");
        Memory mem = currentProgram.getMemory();
        byte[] pat = {0x00, 0x20, 0x29};
        byte[] mask = {(byte) 0xFF, (byte) 0xFF, (byte) 0xFF};
        Address found = mem.findBytes(currentProgram.getMinAddress(), pat, mask, true, monitor);
        int hits = 0;
        while (found != null && hits < 20) {
            Function f = fm.getFunctionContaining(found);
            boolean isData = listing.getDataAt(found) != null;
            println("  " + found + "  " + (f != null ? f.getName() : (isData ? "data" : "-")));
            Address next = found.add(1);
            found = mem.findBytes(next, pat, mask, true, monitor);
            hits++;
        }

        // Full function listing to a file, sorted by address.
        File out = new File(new File(repo, "docs"), "ghidra-functions.txt");
        try (PrintWriter w = new PrintWriter(new FileWriter(out))) {
            w.println("# Ghidra auto-analysis function listing (addr  size  name)");
            w.println("# " + count + " functions; names are auto (FUN_*) unless labelled.");
            for (Function f : fm.getFunctions(true)) {
                w.println(String.format("%s  %6d  %s",
                    f.getEntryPoint(), f.getBody().getNumAddresses(), f.getName()));
            }
        }
        println("\nwrote " + out.getPath());
    }
}
