#!/usr/bin/env python3
"""Generate reassemblable source for the app image, and build it back (#43).

The source is generated locally from the user's own firmware file, so no vendor code is
checked in. What is checked in is the metadata in src/: layout.tsv (code/data map,
exported from Ghidra with tools/ghidra/ExportLayout.java) and symbols.tsv (names).

    python3 tools/fwsrc.py generate firmware/monostation-firmware-800.bin build/fwsrc
    python3 tools/fwsrc.py build build/fwsrc build/fwsrc/firmware.bin
    cmp build/fwsrc/firmware.bin firmware/monostation-firmware-800.bin

Code becomes Thumb-2 assembly: branch, literal-load and adr targets are labels, and data
words that Ghidra marks as pointers into the image are `.word label`. Every instruction
that is not PC-relative is assembled once on its own (a probe) and kept as text only when
its encoding matches the image; otherwise it is emitted as `.inst`. Needs binutils-arm-none-eabi.
"""
import argparse
import bisect
import re
import subprocess
import sys
import tempfile
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

LOAD_ADDRESS = 0x0800C000
ROOT = Path(__file__).resolve().parent.parent
LAYOUT = ROOT / "src" / "layout.tsv"
LAYOUT_FIXES = ROOT / "src" / "layout-fixes.tsv"
SYMBOLS = ROOT / "src" / "symbols.tsv"
CPU_FLAGS = ["-mcpu=cortex-m4", "-mthumb", "-mfpu=fpv4-sp-d16", "-mfloat-abi=hard"]
TOOL = "arm-none-eabi-"

Unit = namedtuple("Unit", "kind start end dtype")      # kind: C, D or U
Insn = namedtuple("Insn", "addr raw text")


class Layout:
    def __init__(self, units, refs, functions):
        self.units = units          # sorted, contiguous Unit list
        self.refs = refs            # data word address -> referenced address
        self.functions = functions  # entry address -> Ghidra name


def read_layout_lines(path):
    for line in Path(path).read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            yield line.split()


def override(units, new):
    """Replace whatever covers [new.start, new.end) by `new`, splitting units at its edges."""
    out = []
    for u in units:
        if u.end <= new.start or u.start >= new.end:
            out.append(u)
            continue
        if u.start < new.start:
            out.append(u._replace(end=new.start))
        if u.end > new.end:
            out.append(u._replace(start=new.end))
    out.append(new)
    out.sort(key=lambda u: u.start)
    return out


def load_layout(path=LAYOUT, fixes=LAYOUT_FIXES):
    """Ghidra's layout, then the hand-made corrections in layout-fixes.tsv (same syntax:
    C/D/U force a range's kind, F adds a function entry)."""
    units, refs, functions = [], {}, {}
    for f in read_layout_lines(path):
        if f[0] in "CDU":
            units.append(Unit(f[0], int(f[1], 16), int(f[2], 16), f[3] if f[0] == "D" else None))
        elif f[0] == "R":
            refs[int(f[1], 16)] = int(f[2], 16)
        elif f[0] == "F":
            functions[int(f[1], 16)] = f[2]
    units.sort(key=lambda u: u.start)
    if fixes and Path(fixes).exists():
        for f in read_layout_lines(fixes):
            if f[0] in "CDU":
                units = override(units, Unit(f[0], int(f[1], 16), int(f[2], 16), "fix"))
            elif f[0] == "F":
                functions[int(f[1], 16)] = f[2] if len(f) > 2 else f"FUN_{int(f[1], 16):08x}"
    return Layout(units, refs, functions)


SYMBOL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.$]*$")


def load_symbols(path=SYMBOLS):
    """addr -> name from symbols.tsv (`addr name [comment]`), if present. A name only
    renames the label at that address; whether it is a function comes from the layout."""
    names = {}
    if Path(path).exists():
        for n, line in enumerate(Path(path).read_text().splitlines(), 1):
            f = line.split(None, 2)
            if not f or f[0].startswith("#"):
                continue
            addr, name = int(f[0], 16), f[1]
            if not SYMBOL_NAME.match(name) or name.startswith(("FUN_", "D_")):
                raise ValueError(f"{path}:{n}: bad symbol name {name!r}")
            if addr in names:
                raise ValueError(f"{path}:{n}: {addr:#010x} named twice")
            names[addr] = name
        dup = {v for v in names.values() if list(names.values()).count(v) > 1}
        if dup:
            raise ValueError(f"{path}: names used twice: {', '.join(sorted(dup))}")
    return names


def check_layout(layout, image_size):
    pos = LOAD_ADDRESS
    for u in layout.units:
        if u.start != pos:
            raise ValueError(f"layout gap or overlap at {pos:#x} (next unit {u.start:#x})")
        pos = u.end
    if pos != LOAD_ADDRESS + image_size:
        raise ValueError(f"layout ends at {pos:#x}, image at {LOAD_ADDRESS + image_size:#x}")


# --- disassembly -------------------------------------------------------------------------

OBJDUMP_LINE = re.compile(r"^\s*([0-9a-f]+):\t([0-9a-f ]+?)\s*\t(.*)$")


def objdump_range(image_path, start, end):
    out = subprocess.run(
        [TOOL + "objdump", "-D", "-b", "binary", "-marm", "-Mforce-thumb",
         f"--adjust-vma={LOAD_ADDRESS:#x}", f"--start-address={start:#x}",
         f"--stop-address={end:#x}", str(image_path)],
        check=True, capture_output=True, text=True).stdout
    insns = []
    for line in out.splitlines():
        m = OBJDUMP_LINE.match(line)
        if m:
            raw = bytes.fromhex("".join(
                h[2:] + h[:2] if len(h) == 4 else h for h in m.group(2).split()))
            insns.append(Insn(int(m.group(1), 16), raw, m.group(3).strip()))
    return insns


def disassemble(image_path, layout):
    ranges = [(u.start, u.end) for u in layout.units if u.kind == "C"]
    with ThreadPoolExecutor() as pool:
        parts = pool.map(lambda r: objdump_range(image_path, *r), ranges)
    code = {}
    for (start, end), insns in zip(ranges, parts):
        pos = start
        for i in insns:
            if i.addr != pos:
                raise ValueError(f"objdump lost sync at {i.addr:#x} (expected {pos:#x})")
            pos += len(i.raw)
        if pos != end:
            raise ValueError(f"code range {start:#x}-{end:#x} decodes to {pos:#x}")
        code[start] = insns
    return code


# --- operand rewriting -------------------------------------------------------------------

COND = "eq|ne|cs|cc|hs|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le|al"
BRANCH = re.compile(rf"^((?:bl|b)(?:{COND})?(?:\.[nw])?|cbn?z)\t(r\d+, )?(0x[0-9a-f]+)$")
PC_LOAD = re.compile(r"^(ldr[a-z]*(?:\.w)?|vldr|ldrd)\t(.*), \[pc, #(-?\d+)\]\t@ \(?(0x[0-9a-f]+)\)?$")
ADR = re.compile(r"^(addw?|subw?)(\.w)?\t(r\d+|sl|fp|ip|lr|sb), pc, #(\d+)\t@ \(adr [^,]+, (0x[0-9a-f]+)\)$")
COMMENT = re.compile(r"\t@.*$")


def pc_target(insn):
    """(kind, target address) when the instruction refers PC-relatively to an address."""
    t = insn.text
    if t.startswith(("blx\t", "bx\t", "tbb\t", "tbh\t")):
        return None
    m = BRANCH.match(t)
    if m:
        return "branch", int(m.group(3), 16)
    m = PC_LOAD.match(t)
    if m:
        return "load", int(m.group(4), 16)
    m = ADR.match(t)
    if m:
        return "adr", int(m.group(5), 16)
    ops = re.sub(r"\{.*\}", "", COMMENT.sub("", t).split("\t", 1)[-1])   # not register lists
    if "[pc" in ops or re.search(r", pc\b", ops):
        return "other", None    # reads PC in a form not rewritten: kept as .inst
    return None


def rewrite(insn, kind, label):
    t = insn.text
    if kind == "branch":
        m = BRANCH.match(t)
        return f"{m.group(1)}\t{m.group(2) or ''}{label}"
    if kind == "load":
        m = PC_LOAD.match(t)
        return f"{m.group(1)}\t{m.group(2)}, {label}"
    if kind == "adr":
        m = ADR.match(t)
        wide = ".w" if len(insn.raw) == 4 else ""
        return f"adr{wide}\t{m.group(3)}, {label}"
    raise ValueError(kind)


def inst(insn):
    if len(insn.raw) == 2:
        return f".inst.n\t{int.from_bytes(insn.raw, 'little'):#06x}"
    hi, lo = int.from_bytes(insn.raw[:2], "little"), int.from_bytes(insn.raw[2:], "little")
    return f".inst.w\t{hi << 16 | lo:#010x}"


# --- probes: assemble non-PC-relative instructions alone ---------------------------------

def it_groups(insns):
    """Split into groups: an IT instruction with the instructions it covers, or one insn."""
    i = 0
    while i < len(insns):
        m = re.match(r"^it([te]{0,3})\t", insns[i].text)
        n = 1 + (1 + len(m.group(1)) if m else 0)
        yield insns[i:i + n]
        i += n


def assemble(source, workdir, name):
    s, o, b = (Path(workdir) / f"{name}{ext}" for ext in (".s", ".o", ".bin"))
    s.write_text(source)
    subprocess.run([TOOL + "as", *CPU_FLAGS, "-o", str(o), str(s)], check=True,
                   capture_output=True, text=True)
    subprocess.run([TOOL + "objcopy", "-O", "binary", "-j", ".text", str(o), str(b)], check=True)
    return b.read_bytes()


def probe(groups, workdir):
    """Indices of groups whose text assembles to exactly their bytes."""
    slot = 24
    lines = [".syntax unified", ".thumb", ".text", "base:"]
    for n, g in enumerate(groups):
        lines.append(f".org base + {n * slot}")
        lines += ["\t" + COMMENT.sub("", i.text) for i in g]
    try:
        out = assemble("\n".join(lines) + "\n", workdir, "probe")
    except subprocess.CalledProcessError as e:
        if len(groups) == 1:
            return set()
        half = len(groups) // 2                 # find the groups gas rejects
        return probe(groups[:half], workdir) | {half + k for k in probe(groups[half:], workdir)}
    good = set()
    for n, g in enumerate(groups):
        want = b"".join(i.raw for i in g)
        if out[n * slot:n * slot + len(want)] == want:
            good.add(n)
    return good


# --- generation --------------------------------------------------------------------------

class Generator:
    def __init__(self, image, layout, names, code):
        self.image, self.layout, self.code = image, layout, code
        self.end = LOAD_ADDRESS + len(image)
        self.functions = set(layout.functions)
        self.symbols = names
        self.names = dict(layout.functions)
        self.names.update(names)
        self.insn_at = {i.addr: i for insns in code.values() for i in insns}
        self.unit_starts = [u.start for u in layout.units]
        self.labels = {}            # address -> label
        self.aliases = set()        # function entries that also need a plain local label
        self.stats = {"inst": 0, "insns": 0, "words": 0}

    def label(self, addr):
        """Label for an image address, or None when no unit boundary lies there."""
        if not LOAD_ADDRESS <= addr < self.end:
            return None
        if addr in self.labels:
            return self.labels[addr]
        if addr in self.names:
            name = self.names[addr]
        elif addr in self.insn_at:
            name = f".L_{addr:08x}"
        else:
            name = f"D_{addr:08x}"
            if self.unit_kind(addr) == "C":
                return None              # inside an instruction
        self.labels[addr] = name
        return name

    def unit_kind(self, addr):
        n = bisect.bisect_right(self.unit_starts, addr) - 1
        u = self.layout.units[n] if n >= 0 else None
        return u.kind if u and u.start <= addr < u.end else None

    def word_at(self, addr):
        return int.from_bytes(self.image[addr - LOAD_ADDRESS:addr - LOAD_ADDRESS + 4], "little")

    def plan(self, workdir):
        """Choose the text of every instruction; collect labels."""
        self.text = {}
        plain = []
        for insns in self.code.values():
            for g in it_groups(insns):
                if any(pc_target(i) for i in g):
                    for i in g:
                        self.plan_pc(i)
                else:
                    plain.append(g)
        good = probe(plain, workdir)
        for n, g in enumerate(plain):
            for i in g:
                self.text[i.addr] = COMMENT.sub("", i.text) if n in good else inst(i)
                self.stats["inst"] += n not in good
        self.stats["insns"] = len(self.insn_at)
        self.plan_words()
        self.plan_symbols()

    def plan_symbols(self):
        """Every name in symbols.tsv gets a label, referenced or not. It must sit on an
        instruction or on data outside a pointer word."""
        bad = []
        for addr in sorted(self.symbols):
            in_word = any(addr - k in self.words for k in (1, 2, 3))
            if not LOAD_ADDRESS <= addr < self.end or in_word \
                    or (self.unit_kind(addr) == "C" and addr not in self.insn_at):
                bad.append(f"{addr:#010x} {self.symbols[addr]}")
            else:
                self.label(addr)
        if bad:
            raise ValueError("symbols not on an instruction or data boundary: " + ", ".join(bad))

    def plan_words(self):
        """Data words that hold image addresses become `.word label`: those Ghidra marks as
        references, and every other aligned data word whose value lies in the image. A
        Thumb code pointer (odd, one past an instruction) becomes `label + 1`."""
        self.words = {}
        candidates = set(self.layout.refs)
        for u in self.layout.units:
            if u.kind != "C":
                candidates.update(range((u.start + 3) & ~3, u.end - 3, 4))
        for addr in sorted(candidates):
            if addr - LOAD_ADDRESS + 4 > len(self.image) or self.unit_kind(addr) == "C":
                continue
            value = self.word_at(addr)
            if not LOAD_ADDRESS <= value < self.end:
                continue
            if value & 1 and value - 1 in self.insn_at:
                lab = self.label(value - 1)
                # the linker sets bit 0 for a .thumb_func symbol by itself
                suffix = "" if self.is_function(value - 1) else " + 1"
            elif self.is_function(value):
                # an even pointer to a function: a local alias keeps bit 0 clear
                self.label(value)
                self.aliases.add(value)
                lab, suffix = f".La_{value:08x}", ""
            else:
                lab, suffix = self.label(value), ""
            if lab is not None:
                self.words[addr] = lab + suffix

    def is_function(self, addr):
        return addr in self.functions and addr in self.insn_at

    def plan_pc(self, i):
        t = pc_target(i)
        if t is None:
            self.text[i.addr] = COMMENT.sub("", i.text)
            return
        kind, target = t
        lab = self.label(target) if target is not None else None
        if lab is None:
            self.text[i.addr] = inst(i)
            self.stats["inst"] += 1
        else:
            self.text[i.addr] = rewrite(i, kind, lab)

    def emit(self):
        out = [f"@ Generated by tools/fwsrc.py from the firmware image; do not commit.",
               ".syntax unified", ".thumb", ".section .text.image, \"ax\", %progbits", ".p2align 2", ""]
        for u in self.layout.units:
            if u.kind == "C":
                for i in self.code[u.start]:
                    self.emit_label(out, i.addr)
                    out.append(f"\t{self.text[i.addr]}")
            else:
                self.emit_data(out, u)
        return "\n".join(out) + "\n"

    def emit_label(self, out, addr):
        lab = self.labels.get(addr)
        if lab is None:
            return
        if self.is_function(addr):
            out += ["", f"\t.global {lab}", f"\t.thumb_func", f"\t.type {lab}, %function"]
        out.append(f"{lab}:")
        if addr in self.aliases:
            out.append(f".La_{addr:08x}:")

    def emit_data(self, out, u):
        pos = u.start
        while pos < u.end:
            self.emit_label(out, pos)
            if pos in self.words and pos + 4 <= u.end:
                out.append(f"\t.word\t{self.words[pos]}")
                self.stats["words"] += 1
                pos += 4
                continue
            stop = pos + 1
            while stop < u.end and stop - pos < 16 and stop not in self.labels \
                    and stop not in self.words:
                stop += 1
            chunk = self.image[pos - LOAD_ADDRESS:stop - LOAD_ADDRESS]
            out.append("\t.byte\t" + ", ".join(f"0x{b:02x}" for b in chunk))
            pos = stop


LINKER_SCRIPT = f"""\
/* Generated by tools/fwsrc.py. */
SECTIONS
{{
    . = {LOAD_ADDRESS:#x};
    .text : {{ KEEP(*(.text.image)) }}
}}
"""


def generate(image_path, outdir, layout_path=LAYOUT, symbols_path=SYMBOLS, fixes=LAYOUT_FIXES):
    image = Path(image_path).read_bytes()
    layout = load_layout(layout_path, fixes)
    check_layout(layout, len(image))
    code = disassemble(image_path, layout)
    gen = Generator(image, layout, load_symbols(symbols_path), code)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        gen.plan(tmp)
    # labels used by .word are only known after plan; emit afterwards
    (outdir / "firmware.s").write_text(gen.emit())
    (outdir / "firmware.ld").write_text(LINKER_SCRIPT)
    return gen.stats


def build(srcdir, out_bin):
    srcdir = Path(srcdir)
    objs = []
    for s in sorted(srcdir.glob("*.s")):
        o = s.with_suffix(".o")
        subprocess.run([TOOL + "as", *CPU_FLAGS, "-o", str(o), str(s)], check=True)
        objs.append(str(o))
    elf = srcdir / "firmware.elf"
    subprocess.run([TOOL + "ld", "-T", str(srcdir / "firmware.ld"), "-o", str(elf), *objs],
                   check=True)
    subprocess.run([TOOL + "objcopy", "-O", "binary", str(elf), str(out_bin)], check=True)
    return Path(out_bin).read_bytes()


def first_difference(a, b):
    for n, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return n
    return None if len(a) == len(b) else min(len(a), len(b))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write firmware.s and firmware.ld")
    g.add_argument("image", type=Path)
    g.add_argument("outdir", type=Path)
    g.add_argument("--layout", type=Path, default=LAYOUT)
    g.add_argument("--symbols", type=Path, default=SYMBOLS)
    b = sub.add_parser("build", help="assemble and link a generated tree")
    b.add_argument("srcdir", type=Path)
    b.add_argument("out", type=Path)
    b.add_argument("--compare", type=Path, help="image the build must equal")
    args = parser.parse_args(argv)

    if args.cmd == "generate":
        stats = generate(args.image, args.outdir, args.layout, args.symbols)
        print(f"{args.outdir}: {stats['insns']} instructions ({stats['inst']} as .inst), "
              f"{stats['words']} pointer words")
    else:
        data = build(args.srcdir, args.out)
        print(f"{args.out}: {len(data)} bytes")
        if args.compare:
            ref = args.compare.read_bytes()
            diff = first_difference(data, ref)
            if diff is not None:
                sys.exit(f"differs from {args.compare} at {LOAD_ADDRESS + diff:#x}")
            print(f"identical to {args.compare}")


if __name__ == "__main__":
    main()
