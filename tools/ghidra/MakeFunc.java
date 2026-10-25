// Ghidra post-script: disassemble and create a function at each address that
// auto-analysis missed (e.g. ones reached only through vtables), then decompile
// it. Modifies the Ghidra DB.
//   analyzeHeadless ghidra-project monostation -process monostation-firmware-800.bin \
//     -noanalysis -scriptPath tools/ghidra -postScript MakeFunc.java 0x080234ac
// @category Monostation
import ghidra.app.cmd.disassemble.ArmDisassembleCommand;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;

public class MakeFunc extends GhidraScript {
    @Override
    public void run() throws Exception {
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);
        for (String arg : getScriptArgs()) {
            Address at = toAddr(Long.decode(arg).longValue());
            Function f = getFunctionAt(at);
            if (f == null) {
                new ArmDisassembleCommand(at, null, true).applyTo(currentProgram, monitor);
                f = createFunction(at, null);
            }
            if (f == null) { println("// cannot create " + arg); continue; }
            println("// ==== " + f.getName() + " @ " + f.getEntryPoint() + " ====");
            DecompileResults res = decomp.decompileFunction(f, 120, monitor);
            println(res.decompileCompleted() ? res.getDecompiledFunction().getC() : "// failed " + res.getErrorMessage());
        }
        decomp.dispose();
    }
}
