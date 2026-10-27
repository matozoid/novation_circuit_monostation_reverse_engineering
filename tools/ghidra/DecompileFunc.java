// Ghidra post-script: decompile the function containing a given address and
// print the C to stdout. For investigating specific subsystems.
//   analyzeHeadless ghidra-project monostation -process monostation-firmware-800.bin \
//     -noanalysis -scriptPath tools/ghidra -postScript DecompileFunc.java 0x0800e1f8
// @category Monostation

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class DecompileFunc extends GhidraScript {

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length == 0) {
            println("usage: -postScript DecompileFunc.java <addr> [<addr> ...]");
            return;
        }
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);
        try {
            for (String arg : args) {
                Address at = toAddr(Long.decode(arg).longValue());
                Function f = currentProgram.getFunctionManager().getFunctionContaining(at);
                if (f == null) {
                    println("// no function at " + arg);
                    continue;
                }
                println("// ==== " + f.getName() + " @ " + f.getEntryPoint()
                        + " (" + f.getBody().getNumAddresses() + " bytes) ====");
                DecompileResults res = decomp.decompileFunction(f, 60, monitor);
                if (res.decompileCompleted()) {
                    println(res.getDecompiledFunction().getC());
                } else {
                    println("// decompile failed: " + res.getErrorMessage());
                }
            }
        } finally {
            decomp.dispose();
        }
    }
}
