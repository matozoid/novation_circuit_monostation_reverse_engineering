#!/usr/bin/env python3
"""Generate reassemblable source for the app image, and build it back (#43).

The source is generated locally from the user's own firmware file, so no vendor code is
checked in. What is checked in is the metadata in src/: layout.tsv (code/data map,
exported from Ghidra with tools/ghidra/ExportLayout.java), symbols.tsv (names) and
files.tsv (which address ranges become which .s file; the rest goes into gap_*.s).

    python3 tools/fwsrc.py generate firmware/monostation-firmware-800.bin build/fwsrc
    python3 tools/fwsrc.py build build/fwsrc build/fwsrc/firmware.bin
    cmp build/fwsrc/firmware.bin firmware/monostation-firmware-800.bin
    python3 tools/fwsrc.py coverage        # functions named and listed in docs/source-map.md
    python3 tools/fwsrc.py generate IMAGE build/fwsrc --overlay overlays/hello   # our code

An overlay (overlays/<name>/overlay.toml plus .s files) replaces stock functions by name:
the stock body stays in place as stock_<name>, the overlay code is linked after the image
and every bl, b.w and pointer word to the function now reaches the overlay instead.

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
import tomllib
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

LOAD_ADDRESS = 0x0800C000
ROOT = Path(__file__).resolve().parent.parent
LAYOUT = ROOT / "src" / "layout.tsv"
LAYOUT_FIXES = ROOT / "src" / "layout-fixes.tsv"
SYMBOLS = ROOT / "src" / "symbols.tsv"
FILES = ROOT / "src" / "files.tsv"
SOURCE_MAP = ROOT / "docs" / "source-map.md"
CPU_FLAGS = ["-mcpu=cortex-m4", "-mthumb", "-mfpu=fpv4-sp-d16", "-mfloat-abi=hard"]
TOOL = "arm-none-eabi-"
IMAGE_HEADER = 0x1AC    # "MAIN" + BCD version: the bootloader reads it at this image offset
FLASH_END = 0x08080000  # end of sector 7, the last one the bootloader erases
OVERLAY_TOML = "overlay.toml"
OVERLAY_COPY = "@ Copied by tools/fwsrc.py from an overlay; edit the original:"
OVERLAY_COPY_C = "/* Copied by tools/fwsrc.py from an overlay; edit the original: */"
OVERLAY_LIST = "overlays.txt"   # `replace NAME` and `include DIR` lines, for the build
OVERLAY_PREFIX = "zz_overlay_"  # sorted after every generated file
OVERLAY_INCLUDE = ROOT / "overlays" / "include"     # shared headers for C overlays
CC_FLAGS = ["-std=gnu17", "-Os", "-ffreestanding", "-fno-common", "-fno-exceptions",
            "-fno-unwind-tables", "-fno-asynchronous-unwind-tables", "-Wall", "-Wextra",
            "-Werror=implicit-function-declaration"]

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


FILE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


def load_files(path=FILES):
    """[(start, end, name)] from files.tsv (`start end name [comment]`), sorted and
    checked: each range [start, end) becomes one source file."""
    files = []
    if Path(path).exists():
        for n, line in enumerate(Path(path).read_text().splitlines(), 1):
            f = line.split(None, 3)
            if not f or f[0].startswith("#"):
                continue
            start, end, name = int(f[0], 16), int(f[1], 16), f[2]
            if not FILE_NAME.match(name) or name.startswith("gap_") or start >= end:
                raise ValueError(f"{path}:{n}: bad file line")
            files.append((start, end, name))
    files.sort()
    names = [f[2] for f in files]
    if len(set(names)) != len(names):
        raise ValueError(f"{path}: file names used twice")
    for (s0, e0, n0), (s1, e1, n1) in zip(files, files[1:]):
        if s1 < e0:
            raise ValueError(f"{path}: {n0} and {n1} overlap")
    return files


def cover_files(files, end):
    """files plus gap_<start> files for every range they leave out, up to end."""
    out, pos = [], LOAD_ADDRESS
    for start, stop, name in files + [(end, end, None)]:
        if start > pos:
            out.append((pos, start, f"gap_{pos:08x}"))
        if name:
            out.append((start, stop, name))
        pos = max(pos, stop)
    return out


Overlay = namedtuple("Overlay", "path base version sources replace")


def load_overlay(directory):
    """overlays/<name>/overlay.toml: base (stock version), optional version (written to the
    image header), sources (.s and .c files in the directory) and replace (stock function
    names)."""
    directory = Path(directory)
    path = directory / OVERLAY_TOML
    with path.open("rb") as f:
        t = tomllib.load(f)
    unknown = set(t) - {"base", "version", "sources", "replace"}
    if unknown:
        raise ValueError(f"{path}: unknown keys {sorted(unknown)}")
    if not isinstance(t.get("base"), int) or not t.get("sources"):
        raise ValueError(f"{path}: needs base (stock version) and sources")
    version = t.get("version")
    if version is not None and not (isinstance(version, int) and 0 <= version <= 9999):
        raise ValueError(f"{path}: version must fit four BCD digits")
    for src in t["sources"]:
        if not FILE_NAME.match(Path(src).stem) or Path(src).suffix not in (".s", ".c") \
                or "/" in src:
            raise ValueError(f"{path}: source {src!r} is not a .s or .c file in the overlay")
    return Overlay(directory, t["base"], version, list(t["sources"]), list(t.get("replace", [])))


def image_version(image):
    """The BCD version in the image header: b'MAIN' 00 08 -> 800."""
    header = image[IMAGE_HEADER:IMAGE_HEADER + 6]
    if header[:4] != b"MAIN":
        raise ValueError("image header tag MAIN not found")
    return int(f"{int.from_bytes(header[4:], 'little'):x}")


def apply_overlays(image, overlays, version=None):
    """Check the overlays against the image and each other; returns (image with the new
    header version, replaced names). A given version overrides the overlays' own, so
    overlays with different versions can be built together."""
    stock = image_version(image)
    replace, own = [], None
    for ov in overlays:
        if ov.base != stock:
            raise ValueError(f"{ov.path}: base {ov.base}, but the image is version {stock}")
        for name in ov.replace:
            if name in replace:
                raise ValueError(f"{ov.path}: {name} is replaced by two overlays")
            replace.append(name)
        if ov.version is not None and version is None:
            if own not in (None, ov.version):
                raise ValueError(f"{ov.path}: version {ov.version}, another overlay says {own}"
                                 " (give the build one with --version)")
            own = ov.version
    if version is None:
        version = own
    if version is not None:
        if not 0 <= version <= 9999:
            raise ValueError(f"version {version} must fit four BCD digits")
        image = bytearray(image)
        image[IMAGE_HEADER + 4:IMAGE_HEADER + 6] = int(str(version), 16).to_bytes(2, "little")
        image = bytes(image)
    return image, replace


def split_units(units, starts):
    """Undefined (U) units split where a file starts inside them: their bytes can go to
    either file."""
    out = []
    for u in units:
        for a in sorted(a for a in starts if u.kind == "U" and u.start < a < u.end):
            out.append(u._replace(end=a))
            u = u._replace(start=a)
        out.append(u)
    return out


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
MARKER = "__m_"     # shift test: label on every line, named after its stock address
LOCAL_LABEL = re.compile(r"\.La?_([0-9a-f]{8})\b")
GENERATED = "@ Generated by tools/fwsrc.py from the firmware image; do not commit."


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


def wide_by_choice(insn, target):
    """A 32-bit b/b<cond> whose target the 16-bit encoding would also reach."""
    if len(insn.raw) != 4 or insn.text.startswith("bl"):
        return False
    offset = target - (insn.addr + 4)
    limit = 2048 if re.match(r"^b(\.w)?\t", insn.text) else 256
    return -limit <= offset < limit


def rewrite(insn, kind, label, relax=False):
    """Operand as a label. With relax, a branch loses its width suffix so gas picks the
    encoding (and widens it when code moves). Literal loads and adr keep their width:
    gas never shrinks a relaxed instruction, and in one large file its first estimates
    widen some loads. A fixed narrow load that ends up out of range is an assembler error."""
    t = insn.text
    if kind == "branch":
        m = BRANCH.match(t)
        op = re.sub(r"\.[nw]$", "", m.group(1)) if relax else m.group(1)
        return f"{op}\t{m.group(2) or ''}{label}"
    if kind == "load":
        m = PC_LOAD.match(t)
        op = m.group(1)
        if len(insn.raw) == 2 and not op.endswith(".w"):
            op += ".n"
        return f"{op}\t{m.group(2)}, {label}"
    if kind == "adr":
        m = ADR.match(t)
        wide = ".w" if len(insn.raw) == 4 else ".n"
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
    def __init__(self, image, layout, names, code, relax=False, pads=None, markers=False,
                 files=(), replace=()):
        self.image, self.layout, self.code, self.relax = image, layout, code, relax
        self.pads = dict(pads or {})    # address -> bytes inserted before it (shift test)
        self.markers = markers          # label every line M_<address> (shift test)
        self.end = LOAD_ADDRESS + len(image)
        self.files = cover_files(list(files), self.end)
        self.file_starts = [f[0] for f in self.files]
        self.exported = set()       # addresses whose label another file refers to
        self.crossing = []          # references a file boundary must not split
        self.pc_loads = set()       # files with PC-relative loads or adr: they need 4
        self.functions = set(layout.functions)
        self.symbols = names
        self.names = dict(layout.functions)
        self.names.update(names)
        self.replaced = self.plan_replace(replace)  # stock entry -> (overlay name, body end)
        self.insn_at = {i.addr: i for insns in code.values() for i in insns}
        self.unit_starts = [u.start for u in layout.units]
        self.labels = {}            # address -> label
        self.aliases = set()        # function entries that also need a plain local label
        self.tables = {}            # jump-table entry address -> (directive, expression)
        self.tables_base = set()    # first entry of each jump table
        self.stats = {"inst": 0, "insns": 0, "words": 0, "tables": 0}

    def plan_replace(self, replace):
        """The stock body of a replaced function keeps its place as stock_<name>; the name
        goes to the overlay. The body runs to the next function entry."""
        by_name = {name: addr for addr, name in self.names.items()}
        entries = sorted(self.functions)
        out, bad = {}, []
        for name in replace:
            addr = by_name.get(name)
            if addr is None or addr not in self.functions:
                bad.append(name)
                continue
            n = bisect.bisect_right(entries, addr)
            out[addr] = (name, entries[n] if n < len(entries) else self.end)
            self.names[addr] = f"stock_{name}"
            if addr in self.symbols:
                self.symbols = {**self.symbols, addr: f"stock_{name}"}
        if bad:
            raise ValueError("overlay replaces unknown functions: " + ", ".join(bad))
        return out

    def redirect(self, src, target):
        """The overlay's name when a reference at src to target must reach the overlay:
        target is a replaced entry and src lies outside its stock body."""
        if target not in self.replaced:
            return None
        name, end = self.replaced[target]
        return None if target <= src < end else name

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

    def file_of(self, addr):
        return self.files[bisect.bisect_right(self.file_starts, addr) - 1][2]

    def use(self, src, target, allowed=True):
        """src refers to the label at target; a reference into another file exports it."""
        if self.file_of(src) != self.file_of(target):
            self.exported.add(target)
            if not allowed:
                self.crossing.append(f"{src:#010x} -> {target:#010x}")

    def word_at(self, addr):
        return int.from_bytes(self.image[addr - LOAD_ADDRESS:addr - LOAD_ADDRESS + 4], "little")

    def plan(self, workdir):
        """Choose the text of every instruction; collect labels."""
        self.text = {}
        self.unredirectable = []    # references to a replaced function that cannot reach it
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
        self.plan_tables()
        self.plan_symbols()
        self.plan_files()

    def plan_files(self):
        """Files must start on an instruction or a data unit (undefined bytes are split for
        them), and only wide branches and data may cross files:
        literal loads, adr, narrow branches and jump tables stay inside one file."""
        bad = [f"{name} starts inside a unit at {start:#010x}" for start, _, name in self.files
               if start not in self.insn_at and start not in self.unit_starts]
        bad += [f"reference across files: {c}" for c in self.crossing]
        bad += [f"cannot redirect to the overlay: {c}" for c in getattr(self, "unredirectable", [])]
        if bad:
            raise ValueError("; ".join(bad))

    def plan_tables(self):
        """tbb/tbh tables become `(target - base)/2` like GCC emits them, so they follow
        code that moves. The table starts right after the instruction (base = its pc); an
        entry that is no forward target inside the code (the pad byte) stays a number."""
        for addr, i in self.insn_at.items():
            if not i.text.startswith(("tbb\t", "tbh\t")):
                continue
            size, directive = (1, ".byte") if i.text.startswith("tbb") else (2, ".short")
            base = addr + 4
            self.tables_base.add(base)
            pos = base
            while self.unit_kind(pos) != "C" and pos + size <= self.end:
                value = int.from_bytes(self.image[pos - LOAD_ADDRESS:pos - LOAD_ADDRESS + size],
                                       "little")
                target = base + 2 * value
                if target not in self.insn_at or target <= addr:
                    break
                self.use(addr, pos, False)
                self.use(addr, target, False)
                self.tables[pos] = (directive, f"({self.label(target)} - {self.label(base)}) / 2")
                pos += size
            self.stats["tables"] += 1

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
            value = target = self.word_at(addr)
            if not LOAD_ADDRESS <= value < self.end:
                continue
            if value & 1 and (name := self.redirect(addr, value - 1)) \
                    and self.is_function(value - 1):
                self.words[addr] = name         # an overlay .thumb_func: the linker sets bit 0
                continue
            if value & 1 and value - 1 in self.insn_at:
                target = value - 1
                lab = self.label(target)
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
                self.use(addr, target)

    def is_function(self, addr):
        return addr in self.functions and addr in self.insn_at

    def plan_pc(self, i):
        t = pc_target(i)
        if t is None:
            self.text[i.addr] = COMMENT.sub("", i.text)
            return
        kind, target = t
        lab = self.label(target) if target is not None else None
        name = self.redirect(i.addr, target)
        if name is not None:
            # bl and the wide branches reach the overlay after the image; narrow branches,
            # cbz and PC-relative loads of the entry do not
            if kind != "branch" or len(i.raw) != 4:
                self.unredirectable.append(f"{i.addr:#010x} {COMMENT.sub('', i.text)} "
                                           f"-> {self.replaced[target][0]}")
            self.text[i.addr] = rewrite(i, kind, name, relax=False)
            return
        if lab is None:
            self.text[i.addr] = inst(i)
            self.stats["inst"] += 1
        else:
            # A branch that is wide although a narrow one would reach keeps its width: the
            # linker resolved it (tail calls), so gas would choose differently.
            relax = self.relax and not (kind == "branch" and wide_by_choice(i, target))
            self.use(i.addr, target, kind == "branch" and len(i.raw) == 4)
            if kind != "branch":
                self.pc_loads.add(self.file_of(i.addr))
            if relax and kind == "branch" and self.is_function(target) \
                    and self.file_of(i.addr) == self.file_of(target):
                # and a relaxed branch to a global symbol is always wide: use the local alias
                self.aliases.add(target)
                lab = f".La_{target:08x}"
            self.text[i.addr] = rewrite(i, kind, lab, relax)

    def emit(self):
        self.items = []             # (address, kind, size) of every emitted line
        self.padded = set()
        self.realign = False        # a pad since the last .p2align 2: data must align itself
        sources = {}
        starts = {start: name for start, _, name in self.files}

        def output(addr, out):
            """The list to emit addr into: a new file's when one starts there."""
            if addr not in starts:
                return out
            # code needs 2, and 4 with PC-relative loads: gas wants that for a safe
            # Align(PC, 4); data keeps its stock alignment
            if addr in self.insn_at:
                align = 2 if starts[addr] in self.pc_loads and addr % 4 == 0 else 1
            else:
                align = 2 if addr % 4 == 0 else 1 if addr % 2 == 0 else 0
            sources[starts[addr]] = out = [
                GENERATED, ".syntax unified", ".thumb",
                f".section .text.{starts[addr]}, \"ax\", %progbits", f".p2align {align}", ""]
            return out

        out = None
        for u in self.layout.units:
            out = output(u.start, out)
            if u.kind == "C":
                if self.unit_kind(u.start - 1) not in ("C", None):
                    self.emit_pad(out, u.start)
                    out.append("\t.p2align 1")       # code after data stays aligned
                for i in self.code[u.start]:
                    out = output(i.addr, out)
                    self.emit_label(out, i.addr)
                    self.emit_item(out, i.addr, "insn", len(i.raw), self.text[i.addr])
            else:
                self.emit_data(out, u)
        bad = []
        for start, end, name in self.files:
            need = 1 << max([int(line.split()[-1]) for line in sources[name]
                             if line.lstrip().startswith(".p2align ")]
                            + [2] * (name in self.pc_loads))
            if start % need or end % need:
                bad.append(f"{name} ({start:#010x}-{end:#010x})")
        if bad:
            # a section is aligned to its most aligned data, and gas pads its size to that
            raise ValueError(f"files that start or end less aligned than data or "
                             f"PC-relative loads in them: "
                             + ", ".join(bad))
        return {name: "\n".join(out) + "\n" for name, out in sources.items()}

    def local(self, text, src):
        """A local label in a line at src names its global twin when it lies in another
        file. In-file references stay local: gas makes a relaxed branch to a global wide."""
        def sub(m):
            return m.group()[1:] if self.file_of(int(m.group(1), 16)) != self.file_of(src) \
                else m.group()
        return LOCAL_LABEL.sub(sub, text)

    def emit_pad(self, out, addr):
        if addr in self.pads and addr not in self.padded:
            out.append(f"\t.space\t{self.pads[addr]}")
            self.padded.add(addr)
            self.realign = True

    def emit_item(self, out, addr, kind, size, text):
        if self.markers:
            out.append(f"{MARKER}{addr:08x}:")
        self.items.append((addr, kind, size))
        out.append(f"\t{self.local(text, addr)}")

    def emit_label(self, out, addr):
        self.emit_pad(out, addr)
        lab = self.labels.get(addr)
        if lab is None:
            return
        if self.is_function(addr):
            out += ["", f"\t.global {lab}", f"\t.thumb_func", f"\t.type {lab}, %function"]
        elif addr in self.symbols:
            out += ["", f"\t.global {lab}", f"\t.type {lab}, %object"]
        elif addr in self.exported and not lab.startswith("."):
            out.append(f"\t.global {lab}")
        out.append(f"{lab}:")
        if addr in self.aliases:
            out.append(f".La_{addr:08x}:")
        if addr in self.exported:               # global twins of local labels
            for twin in [lab] * lab.startswith(".") + [f".La_{addr:08x}"] * (addr in self.aliases):
                out += [f"\t.global {twin[1:]}", f"{twin[1:]}:"]

    def emit_data(self, out, u):
        pos = u.start
        self.emit_pad(out, pos)
        after_code = self.unit_kind(pos - 1) == "C"
        if (after_code or self.realign) and pos not in self.tables:
            # Data keeps its alignment when code moves (data after data moves with it,
            # unless bytes were inserted). At the stock address the directive adds no
            # bytes, or, for a pool after code, the same `nop` pad (00 bf) the compiler
            # put there.
            pad = after_code and pos % 4 == 2 and u.end >= pos + 2 \
                and pos not in self.labels \
                and self.image[pos - LOAD_ADDRESS:pos - LOAD_ADDRESS + 2] == b"\x00\xbf"
            if pos % 4 == 0 or pad:
                out.append("\t.p2align 2")
                self.realign = False
                pos += 2 if pad else 0
            elif pos % 2 == 0:
                out.append("\t.p2align 1")
        while pos < u.end:
            self.emit_label(out, pos)
            if pos in self.tables:
                directive, expr = self.tables[pos]
                size = 2 if directive == ".short" else 1
                self.emit_item(out, pos, "table", size, f"{directive}\t{expr}")
                pos += size
                continue
            if pos in self.words and pos + 4 <= u.end:
                self.emit_item(out, pos, "word", 4, f".word\t{self.words[pos]}")
                self.stats["words"] += 1
                pos += 4
                continue
            stop = pos + 1
            while stop < u.end and stop - pos < 16 and stop not in self.labels \
                    and stop not in self.words and stop not in self.tables \
                    and stop not in self.pads:
                stop += 1
            chunk = self.image[pos - LOAD_ADDRESS:stop - LOAD_ADDRESS]
            self.emit_item(out, pos, "bytes", len(chunk),
                           ".byte\t" + ", ".join(f"0x{b:02x}" for b in chunk))
            pos = stop


def linker_script(files, overlays=False):
    # overlay objects may have a section of the same name (gcc's .text.startup)
    sections = "\n".join(f"        KEEP(*(EXCLUDE_FILE(*{OVERLAY_PREFIX}*) .text.{name}))"
                         for _, _, name in files)
    if overlays:
        # overlay code after the image; read-only: the stock startup neither copies .data
        # nor clears .bss for it
        sections += """
        . = ALIGN(4);
        overlay_start = .;
        *(.text .text.* .rodata .rodata.*)
        overlay_end = .;
    }
    .data :
    {
        *(.data .data.* .bss .bss.* COMMON)"""
    checks = f"""
ASSERT(SIZEOF(.data) == 0, "overlays cannot use .data or .bss")
ASSERT(overlay_end <= {FLASH_END:#x}, "overlays do not fit in the flash")""" if overlays else ""
    return f"""\
/* Generated by tools/fwsrc.py. */
SECTIONS
{{
    . = {LOAD_ADDRESS:#x};
    .text :
    {{
{sections}
    }}
}}
ASSERT(image_header == {LOAD_ADDRESS + IMAGE_HEADER:#x},
       "image header moved: the bootloader reads it at image offset {IMAGE_HEADER:#x}"){checks}
"""


def plan_source(image_path, layout_path=LAYOUT, symbols_path=SYMBOLS, fixes=LAYOUT_FIXES,
                files_path=FILES, overlays=(), version=None, **options):
    """A Generator with every instruction and label planned; options go to Generator."""
    image, replace = apply_overlays(Path(image_path).read_bytes(), overlays, version)
    layout = load_layout(layout_path, fixes)
    check_layout(layout, len(image))
    files = load_files(files_path)
    layout.units = split_units(layout.units, [f[0] for f in files])
    code = disassemble(image_path, layout)
    gen = Generator(image, layout, load_symbols(symbols_path), code, files=files,
                    replace=replace, **options)
    with tempfile.TemporaryDirectory() as tmp:
        gen.plan(tmp)
    return gen


def write_source(gen, outdir, overlays=()):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for old in [*outdir.glob("*.s"), *outdir.glob("*.c")]:   # only files an earlier run
        with old.open() as f:                                # generated or copied
            if f.readline().rstrip("\n").startswith((GENERATED, OVERLAY_COPY, OVERLAY_COPY_C)):
                old.unlink()
    (outdir / OVERLAY_LIST).unlink(missing_ok=True)
    for name, text in gen.emit().items():
        (outdir / f"{name}.s").write_text(text)
    for ov in overlays:
        for src in ov.sources:
            # sorted after every generated file, so the overlays link in the given order
            dest = outdir / f"{OVERLAY_PREFIX}{ov.path.resolve().name}_{src}"
            orig = (ov.path / src).resolve()
            head = f"{OVERLAY_COPY} {orig}\n" if src.endswith(".s") else \
                f"{OVERLAY_COPY_C}\n#line 1 \"{orig}\"\n"     # errors name the original
            dest.write_text(head + orig.read_text())
    if overlays:
        dirs = [OVERLAY_INCLUDE] + [ov.path.resolve() for ov in overlays]
        (outdir / OVERLAY_LIST).write_text(
            "".join(f"replace {name}\n" for name, _ in gen.replaced.values())
            + "".join(f"include {d}\n" for d in dirs))
    (outdir / "firmware.ld").write_text(linker_script(gen.files, bool(overlays)))


def generate(image_path, outdir, layout_path=LAYOUT, symbols_path=SYMBOLS, fixes=LAYOUT_FIXES,
             files_path=FILES, relax=False, overlays=(), version=None):
    overlays = [load_overlay(d) if not isinstance(d, Overlay) else d for d in overlays]
    gen = plan_source(image_path, layout_path, symbols_path, fixes, files_path,
                      overlays=overlays, version=version, relax=relax)
    write_source(gen, outdir, overlays)
    return dict(gen.stats, files=len(gen.files), exported=len(gen.exported))


def overlay_list(srcdir, kind):
    path = Path(srcdir) / OVERLAY_LIST
    lines = path.read_text().splitlines() if path.exists() else []
    return [line.split(" ", 1)[1] for line in lines if line.startswith(kind + " ")]


def build(srcdir, out_bin):
    srcdir = Path(srcdir)
    objs = []
    for s in sorted(srcdir.glob("*.s")):
        o = s.with_suffix(".o")
        subprocess.run([TOOL + "as", *CPU_FLAGS, "--fatal-warnings", "-o", str(o), str(s)],
                       check=True)
        objs.append(str(o))
    libs = []
    c_sources = sorted(srcdir.glob(f"{OVERLAY_PREFIX}*.c"))
    if c_sources:
        includes = [f"-I{d}" for d in overlay_list(srcdir, "include")]
        for c in c_sources:
            o = c.with_suffix(".o")
            subprocess.run([TOOL + "gcc", *CPU_FLAGS, *CC_FLAGS, *includes, "-c", "-o", str(o),
                            str(c)], check=True)
            objs.append(str(o))
        # helpers gcc calls (64-bit division, double arithmetic): only those the image lacks
        libs.append(subprocess.run([TOOL + "gcc", *CPU_FLAGS, "-print-libgcc-file-name"],
                                   check=True, capture_output=True, text=True).stdout.strip())
    elf = srcdir / "firmware.elf"
    subprocess.run([TOOL + "ld", "-T", str(srcdir / "firmware.ld"), "-o", str(elf), *objs,
                    *libs], check=True)
    check_overlays(srcdir, elf)
    subprocess.run([TOOL + "objcopy", "-O", "binary", str(elf), str(out_bin)], check=True)
    return Path(out_bin).read_bytes()


def check_overlays(srcdir, elf):
    """Each replaced name must be a Thumb function in the overlay code: otherwise bl would
    turn into blx to ARM state, or land in the stock image."""
    if not (srcdir / OVERLAY_LIST).exists():
        return
    names = overlay_list(srcdir, "replace")
    # readelf, not nm: nm clears the Thumb bit of .thumb_func symbols
    table = subprocess.run([TOOL + "readelf", "-sW", str(elf)], check=True,
                           capture_output=True, text=True).stdout
    syms = {f[7]: (int(f[1], 16), f[3]) for f in map(str.split, table.splitlines())
            if len(f) == 8 and f[6].isdigit()}
    start, end = syms["overlay_start"][0], syms["overlay_end"][0]
    bad = [name for name in names
           if name not in syms or syms[name][1] != "FUNC" or not syms[name][0] & 1
           or not start <= syms[name][0] - 1 < end]
    if bad:
        raise ValueError("overlay names that are not .thumb_func functions in overlay "
                         "code: " + ", ".join(bad))


# --- shift test: move code, check that every reference follows ----------------------------

def address_map(elf):
    """Stock address -> new address, from the shift test's line markers."""
    out = subprocess.run([TOOL + "nm", str(elf)], check=True, capture_output=True,
                         text=True).stdout
    amap = {}
    for line in out.splitlines():
        value, _, name = line.split()
        if name.startswith(MARKER):
            amap[int(name[len(MARKER):], 16)] = int(value, 16)
    return amap


def normalize(insn, kind):
    """Instruction text without addresses and offsets (and a branch without its width)."""
    t = re.sub(r"#-?\d+|0x[0-9a-f]+", "", COMMENT.sub("", insn.text))
    return re.sub(r"^(\w+)\.[nw]\t", r"\1\t", t) if kind == "branch" else t


def shift_check(gen, new_image, amap):
    """Differences between a moved build and what the stock image says it must be. gen is
    the Generator that emitted it (with markers); amap comes from address_map()."""
    old_image, errors = gen.image, []
    keys = sorted(amap)

    def moved(addr):
        if addr in amap:
            return amap[addr]
        k = keys[bisect.bisect_right(keys, addr) - 1]
        return amap[k] + addr - k

    def new_bytes(addr, size):
        n = addr - LOAD_ADDRESS
        return new_image[n:n + size]

    # disassemble the new image over each code unit's new range
    order = sorted(amap.values())
    ranges = []
    for u in gen.layout.units:
        if u.kind == "C":
            start = amap[u.start]
            n = bisect.bisect_right(order, amap[gen.code[u.start][-1].addr])
            ranges.append((start, order[n] if n < len(order) else LOAD_ADDRESS + len(new_image)))
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "moved.bin"
        path.write_bytes(new_image)
        with ThreadPoolExecutor() as pool:
            parts = list(pool.map(lambda r: objdump_range(path, *r), ranges))
    new_insn = {i.addr: i for part in parts for i in part}

    for addr, kind, size in gen.items:
        where = f"{addr:#010x} -> {amap[addr]:#010x}"
        if kind == "insn":
            old, new = gen.insn_at[addr], new_insn.get(amap[addr])
            if new is None:
                errors.append(f"{where}: no instruction")
                continue
            t, nt = pc_target(old), pc_target(new)
            if t is None or t[1] is None:
                if new.raw != old.raw:
                    errors.append(f"{where}: {old.text!r} became {new.text!r}")
            elif nt is None or nt[0] != t[0] or normalize(old, t[0]) != normalize(new, t[0]):
                errors.append(f"{where}: {old.text!r} became {new.text!r}")
            elif nt[1] != moved(t[1]):
                errors.append(f"{where}: {old.text!r} targets {nt[1]:#x}, "
                              f"want {moved(t[1]):#x}")
            elif old.text.startswith(("tbb\t", "tbh\t")) and addr + 4 in amap \
                    and amap[addr + 4] != amap[addr] + 4:
                errors.append(f"{where}: jump table no longer follows {old.text!r}")
        elif kind == "bytes":
            if new_bytes(amap[addr], size) != old_image[addr - LOAD_ADDRESS:][:size]:
                errors.append(f"{where}: data differs")
        else:
            value = int.from_bytes(old_image[addr - LOAD_ADDRESS:][:size], "little")
            got = int.from_bytes(new_bytes(amap[addr], size), "little")
            if kind == "word":
                want = moved(value & ~1) + (value & 1)
            else:       # table entry: (target - base) / 2, base = first entry
                base = max(b for b in gen.tables_base if b <= addr)
                want = (moved(base + 2 * value) - amap[base]) // 2
            if got != want:
                errors.append(f"{where}: {kind} {value:#x} became {got:#x}, want {want:#x}")
            if kind == "word" and addr % 4 == 0 and amap[addr] % 4:
                errors.append(f"{where}: pointer word no longer aligned")
    return errors


def shift_test(image_path, pads, relax=False, keep=None, files_path=FILES):
    """Build the source with bytes inserted at the given addresses; return
    (number of lines checked, errors)."""
    gen = plan_source(image_path, files_path=files_path, relax=relax, pads=pads, markers=True)
    with tempfile.TemporaryDirectory() as tmp:
        outdir = Path(keep or tmp)
        write_source(gen, outdir)
        lost = sorted(set(pads) - gen.padded)
        if lost:
            raise ValueError("no line starts at " + ", ".join(f"{a:#010x}" for a in lost))
        data = build(outdir, outdir / "firmware.bin")
        amap = address_map(outdir / "firmware.elf")
    return len(gen.items), shift_check(gen, data, amap)


def coverage(layout, names, doc_text):
    """(functions, unnamed, undocumented): the layout's function entries, those without a
    symbols.tsv name, and those whose address is not in the source map as `XXXXXXXX`."""
    functions = sorted(layout.functions)
    return (functions, [a for a in functions if a not in names],
            [a for a in functions if f"`{a:08X}`" not in doc_text])


def first_difference(a, b):
    for n, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return n
    return None if len(a) == len(b) else min(len(a), len(b))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="write the .s files and firmware.ld")
    g.add_argument("image", type=Path)
    g.add_argument("outdir", type=Path)
    g.add_argument("--layout", type=Path, default=LAYOUT)
    g.add_argument("--symbols", type=Path, default=SYMBOLS)
    g.add_argument("--files", type=Path, default=FILES)
    g.add_argument("--relax", action="store_true",
                   help="no fixed .n/.w widths: gas picks them (for code that moves)")
    g.add_argument("--overlay", type=Path, action="append", default=[], metavar="DIR",
                   help="overlay directory with overlay.toml (repeatable)")
    g.add_argument("--version", type=int, metavar="N",
                   help="header version for the build, overriding the overlays' own")
    t = sub.add_parser("shift", help="insert bytes, build, check every reference follows")
    t.add_argument("image", type=Path)
    t.add_argument("--pad", action="append", required=True, metavar="ADDR:N",
                   help="insert N bytes before the line at ADDR (repeatable)")
    t.add_argument("--relax", action="store_true")
    t.add_argument("--files", type=Path, default=FILES)
    t.add_argument("--keep", type=Path, help="leave the moved source and build here")
    b = sub.add_parser("build", help="assemble and link a generated tree")
    b.add_argument("srcdir", type=Path)
    b.add_argument("out", type=Path)
    b.add_argument("--compare", type=Path, help="image the build must equal")
    c = sub.add_parser("coverage", help="count functions with a name and a source-map entry")
    c.add_argument("--layout", type=Path, default=LAYOUT)
    c.add_argument("--symbols", type=Path, default=SYMBOLS)
    c.add_argument("--doc", type=Path, default=SOURCE_MAP)
    c.add_argument("--list", action="store_true", help="print the missing addresses")
    args = parser.parse_args(argv)

    if args.cmd == "generate":
        try:
            stats = generate(args.image, args.outdir, args.layout, args.symbols,
                             files_path=args.files, relax=args.relax, overlays=args.overlay,
                             version=args.version)
        except ValueError as e:
            sys.exit(str(e))
        print(f"{args.outdir}: {stats['insns']} instructions ({stats['inst']} as .inst), "
              f"{stats['words']} pointer words, {stats['files']} files "
              f"({stats['exported']} labels used across files)")
    elif args.cmd == "coverage":
        doc = args.doc.read_text() if args.doc.exists() else ""
        functions, unnamed, undocumented = coverage(load_layout(args.layout),
                                                    load_symbols(args.symbols), doc)
        print(f"{len(functions)} functions: {len(functions) - len(unnamed)} named, "
              f"{len(functions) - len(undocumented)} in {args.doc.name}")
        if args.list:
            for label, addrs in (("unnamed", unnamed), ("undocumented", undocumented)):
                for a in addrs:
                    print(f"{label} {a:#010x}")
    elif args.cmd == "shift":
        pads = {}
        for spec in args.pad:
            addr, n = spec.split(":")
            pads[int(addr, 16)] = int(n, 0)
        try:
            count, errors = shift_test(args.image, pads, args.relax, args.keep, args.files)
        except subprocess.CalledProcessError as e:
            sys.exit(f"{e.cmd[0]} failed: the moved source does not build")
        except ValueError as e:
            sys.exit(str(e))
        for e in errors[:40]:
            print(e)
        if errors:
            sys.exit(f"{len(errors)} of {count} lines wrong after the shift")
        print(f"{count} lines checked: every reference follows the shift")
    else:
        try:
            data = build(args.srcdir, args.out)
        except ValueError as e:
            sys.exit(str(e))
        except subprocess.CalledProcessError as e:
            sys.exit(f"{Path(e.cmd[0]).name} failed")      # its messages are above
        print(f"{args.out}: {len(data)} bytes")
        if args.compare:
            ref = args.compare.read_bytes()
            diff = first_difference(data, ref)
            if diff is not None:
                sys.exit(f"differs from {args.compare} at {LOAD_ADDRESS + diff:#x}")
            print(f"identical to {args.compare}")


if __name__ == "__main__":
    main()
