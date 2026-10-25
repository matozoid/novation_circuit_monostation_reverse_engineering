// Ghidra post-script: list references to each address (callers and data refs)
// with the containing function.
//   analyzeHeadless ghidra-project monostation -process monostation-firmware-800.bin \
//     -noanalysis -scriptPath tools/ghidra -postScript XRefs.java 0x0802323c
// @category Monostation
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.symbol.Reference;

public class XRefs extends GhidraScript {
    @Override
    public void run() throws Exception {
        for (String arg : getScriptArgs()) {
            Address at = toAddr(Long.decode(arg).longValue());
            println("== refs to " + at);
            for (Reference r : getReferencesTo(at)) {
                Function f = getFunctionContaining(r.getFromAddress());
                println("  " + r.getFromAddress() + " " + r.getReferenceType() + " in "
                        + (f == null ? "?" : f.getName()));
            }
        }
    }
}
