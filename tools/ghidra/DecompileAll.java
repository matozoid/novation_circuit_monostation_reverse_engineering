// Ghidra post-script: decompile every function into one file (arg: output path),
// for grepping the whole firmware at once.
//   analyzeHeadless ghidra-project monostation -process monostation-firmware-800.bin \
//     -noanalysis -scriptPath tools/ghidra -postScript DecompileAll.java all.c
// @category Monostation
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import java.io.PrintWriter;

public class DecompileAll extends GhidraScript {
    @Override
    public void run() throws Exception {
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);
        try (PrintWriter out = new PrintWriter(getScriptArgs()[0])) {
            for (Function f : currentProgram.getFunctionManager().getFunctions(true)) {
                out.println("// ==== " + f.getName() + " @ " + f.getEntryPoint() + " ====");
                DecompileResults res = decomp.decompileFunction(f, 60, monitor);
                out.println(res.decompileCompleted() ? res.getDecompiledFunction().getC()
                        : "// decompile failed: " + res.getErrorMessage());
            }
        } finally {
            decomp.dispose();
        }
    }
}
