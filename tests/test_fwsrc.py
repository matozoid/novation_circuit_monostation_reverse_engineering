import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fwpatch  # noqa: E402
import fwsrc  # noqa: E402
import fwsyx  # noqa: E402

FIRMWARE_800 = ROOT / "firmware" / "monostation-firmware-800.syx"
HELLO = ROOT / "overlays" / "hello"
HELLO_C = ROOT / "overlays" / "hello_c"
RANDOMISE = ROOT / "overlays" / "randomise"
TRANSPOSE = ROOT / "overlays" / "transpose"
NOTESINK_CPP = ROOT / "overlays" / "notesink_cpp"
HAVE_GCC = shutil.which("arm-none-eabi-gcc") is not None
HAVE_GXX = shutil.which("arm-none-eabi-g++") is not None
HAVE_REFERENCE_CC = fwsrc.reference_cc() != fwsrc.TOOL
HAVE_BINUTILS = all(shutil.which(f"arm-none-eabi-{tool}")
                    for tool in ("as", "ld", "objcopy", "objdump"))


class LayoutTest(unittest.TestCase):
    def test_override_splits_units(self):
        units = [fwsrc.Unit("C", 0x100, 0x200, None)]
        out = fwsrc.override(units, fwsrc.Unit("D", 0x140, 0x150, "byte"))
        self.assertEqual([(u.kind, u.start, u.end) for u in out],
                         [("C", 0x100, 0x140), ("D", 0x140, 0x150), ("C", 0x150, 0x200)])

    def test_layout_fixes_add_and_drop_functions(self):
        with tempfile.TemporaryDirectory() as tmp:
            layout, fixes = Path(tmp) / "layout.tsv", Path(tmp) / "fixes.tsv"
            layout.write_text("C 08000000 08000010\nF 08000002 FUN_08000002\n")
            fixes.write_text("F 08000000 # scheduled first instruction\nX 08000002\n")
            self.assertEqual(fwsrc.load_layout(layout, fixes).functions, {0x08000000: "FUN_08000000"})

    def test_checked_in_layout_covers_image(self):
        layout = fwsrc.load_layout()
        units = layout.units
        self.assertEqual(units[0].start, fwsrc.LOAD_ADDRESS)
        for a, b in zip(units, units[1:]):
            self.assertEqual(a.end, b.start)
        self.assertEqual(units[-1].end - fwsrc.LOAD_ADDRESS, 129824)

    def test_files_load_and_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "files.tsv"
            path.write_text("# comment\n08000010 08000020 b  why\n08000000 08000008 a\n")
            files = fwsrc.load_files(path)
            self.assertEqual(files, [(0x08000000, 0x08000008, "a"), (0x08000010, 0x08000020, "b")])
            base = fwsrc.LOAD_ADDRESS
            self.assertEqual(fwsrc.cover_files([(base + 8, base + 16, "a")], base + 32),
                             [(base, base + 8, f"gap_{base:08x}"), (base + 8, base + 16, "a"),
                              (base + 16, base + 32, f"gap_{base + 16:08x}")])
            for bad in ("08000000 08000010 a\n08000008 08000018 b\n",   # overlap
                        "08000000 08000008 a\n08000010 08000018 a\n",   # name twice
                        "08000000 08000008 gap_x\n",                     # reserved name
                        "08000000 08000008 a/b\n",                       # not a file name
                        "08000008 08000008 a\n"):                        # empty
                path.write_text(bad)
                with self.assertRaises(ValueError, msg=bad):
                    fwsrc.load_files(path)

    def test_split_units(self):
        units = [fwsrc.Unit("U", 0x100, 0x200, None), fwsrc.Unit("D", 0x200, 0x300, "byte")]
        out = fwsrc.split_units(units, [0x140, 0x200, 0x240])
        self.assertEqual([(u.kind, u.start, u.end) for u in out],
                         [("U", 0x100, 0x140), ("U", 0x140, 0x200), ("D", 0x200, 0x300)])

    def test_symbols_load(self):
        names = fwsrc.load_symbols()
        self.assertEqual(names[0x08017F1C], "clock_engine")
        self.assertEqual(names[0x080202A0], "sysex_dispatch")

    def test_every_function_named_and_documented(self):
        functions, unnamed, undocumented = fwsrc.coverage(
            fwsrc.load_layout(), fwsrc.load_symbols(), fwsrc.SOURCE_MAP.read_text())
        self.assertGreater(len(functions), 800)
        self.assertEqual([f"{a:#010x}" for a in unnamed], [])
        self.assertEqual([f"{a:#010x}" for a in undocumented], [])

    def test_symbols_rejects_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "symbols.tsv"
            path.write_text("08000000 a\n08000004 a\n")
            with self.assertRaises(ValueError):
                fwsrc.load_symbols(path)
            path.write_text("08000000 FUN_08000000\n")
            with self.assertRaises(ValueError):
                fwsrc.load_symbols(path)

class LibrariesTest(unittest.TestCase):
    def test_load_and_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "libraries.tsv"
            path.write_text("file a   libc_nano.a:lib_a-x.o(.text.x .text.y)  # two\n"
                            "file d@20000010 crtbegin.o(.data)\n"
                            "at 20000100 libc_nano.a:lib_a-x.o(.bss.z)\n"
                            "symbol s_start a\nsymbol s_ram 20000200\n")
            libs = fwsrc.load_libraries(path, ["a", "b", "d"])
            self.assertEqual(libs.files["a"], (None, [("libc_nano.a:lib_a-x.o",
                                                       [".text.x", ".text.y"])]))
            self.assertEqual(libs.files["d"][0], 0x20000010)
            self.assertEqual(libs.symbols, {"s_start": "a", "s_ram": 0x20000200})
            script = fwsrc.linker_script(
                [(0x0800C000, 0x0800C010, "a"), (0x0800C010, 0x0800C020, "b"),
                 (0x0800C020, 0x0800C024, "d")], libraries=libs)
            # one KEEP per section, in order; d runs from RAM, loaded after .text
            self.assertLess(script.index("KEEP(*/libc_nano.a:lib_a-x.o(.text.x))"),
                            script.index("KEEP(*/libc_nano.a:lib_a-x.o(.text.y))"))
            self.assertIn(".d 0x20000010 : AT(LOADADDR(.text) + SIZEOF(.text))", script)
            self.assertIn(".ram_20000100 0x20000100 (NOLOAD)", script)
            self.assertIn("s_start = .;", script)
            self.assertIn("/DISCARD/", script)
            with self.assertRaisesRegex(ValueError, "not files.tsv files: a"):
                fwsrc.load_libraries(path, ["b", "d"])
            path.write_text("file a lib_a-x.o(.text)\n")      # no archive
            with self.assertRaisesRegex(ValueError, ":1: bad library line"):
                fwsrc.load_libraries(path)
        self.assertEqual(fwsrc.load_libraries(Path(tmp) / "absent.tsv").files, {})

    def test_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "libraries.tsv"
            path.write_text("source usb_core.o usb/Libraries/src/usb_core.c\n"
                            "cflag -DUSE_DEVICE_MODE\ncflag -Iusb/Libraries/inc\n"
                            "file a usb_core.o(.text.f.isra.1)\n"
                            "export usb_core.o f.isra.1 f\nexport usb_core.o g\n")
            libs = fwsrc.load_libraries(path, ["a"])
            self.assertEqual(libs.sources, {"usb_core.o": "usb/Libraries/src/usb_core.c"})
            self.assertEqual(libs.cflags, ["-DUSE_DEVICE_MODE", "-Iusb/Libraries/inc"])
            self.assertEqual(libs.exports, [("usb_core.o", "f.isra.1", "f"),
                                            ("usb_core.o", "g", "g")])
            self.assertEqual(fwsrc.library_pattern("usb_core.o"), "*/st/usb_core.o")
            script = fwsrc.linker_script([(0x0800C000, 0x0800C010, "a")], libraries=libs)
            self.assertIn("KEEP(*/st/usb_core.o(.text.f.isra.1))", script)
            for bad in ["file a other.o(.text)",            # object without a source line
                        "source x.o not-a-package-path",
                        "cflag -o/tmp/out"]:
                path.write_text(bad + "\n")
                with self.assertRaisesRegex(ValueError, ":1: bad library line"):
                    fwsrc.load_libraries(path)
            path.write_text("export x.o f\n")
            with self.assertRaisesRegex(ValueError, "without a source line: x.o"):
                fwsrc.load_libraries(path)


class OverlayTest(unittest.TestCase):
    def write(self, tmp, toml):
        (tmp / "overlay.toml").write_text(toml)
        return tmp

    def test_load(self):
        ov = fwsrc.load_overlay(HELLO)
        self.assertEqual((ov.base, ov.version, ov.sources, ov.replace),
                         (800, 801, ["hello.s"], ["main"]))
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for bad in ('base = 800\nsources = ["a.s"]\nreplce = ["main"]\n',   # typo
                        'sources = ["a.s"]\n',                                  # no base
                        'base = 800\nsources = []\n',
                        'base = 800\nsources = ["../a.s"]\n',
                        'base = 800\nversion = 10000\nsources = ["a.s"]\n'):
                with self.assertRaises(ValueError, msg=bad):
                    fwsrc.load_overlay(self.write(tmp, bad))

    def test_apply(self):
        image = bytes(fwsrc.IMAGE_HEADER) + b"MAIN\x00\x08" + bytes(10)
        a = fwsrc.Overlay(Path("a"), 800, 801, ["a.s"], ["f"])
        b = fwsrc.Overlay(Path("b"), 800, None, ["b.s"], ["g"])
        out, replace = fwsrc.apply_overlays(image, [a, b])
        self.assertEqual(fwsrc.image_version(out), 801)
        self.assertEqual(replace, ["f", "g"])
        self.assertEqual(fwsrc.apply_overlays(image, [b])[0], image)
        for bad in ([a._replace(base=801)], [a, a._replace(replace=["h"], version=802)],
                    [a, b._replace(replace=["f"])]):
            with self.assertRaises(ValueError):
                fwsrc.apply_overlays(image, bad)
        c = a._replace(path=Path("c"), replace=["h"], version=802)
        self.assertEqual(fwsrc.image_version(fwsrc.apply_overlays(image, [a, c], 803)[0]), 803)
        self.assertEqual(fwsrc.image_version(fwsrc.apply_overlays(image, [b], 804)[0]), 804)
        with self.assertRaises(ValueError):
            fwsrc.apply_overlays(image, [b], 10000)


@unittest.skipUnless(FIRMWARE_800.exists(), "firmware/monostation-firmware-800.syx not present")
@unittest.skipUnless(HAVE_BINUTILS, "arm-none-eabi binutils not installed")
class Overlay800Test(unittest.TestCase):
    def setUp(self):
        self.image = fwsyx.decode_image(FIRMWARE_800.read_bytes())
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "stock.bin").write_bytes(self.image)

    def test_hello_equals_fwpatch(self):
        """overlays/hello and patches/hello make the same image: the reset handler's
        bl main reaches the overlay after the image, which tail-calls stock_main."""
        fwsrc.generate(self.tmp / "stock.bin", self.tmp / "src", overlays=[HELLO])
        built = fwsrc.build(self.tmp / "src", self.tmp / "out.bin")
        patched = fwpatch.build(ROOT / "patches" / "hello", self.image)[0]
        self.assertIsNone(fwsrc.first_difference(built, patched))
        # without the overlay the same tree builds stock again
        fwsrc.generate(self.tmp / "stock.bin", self.tmp / "src")
        self.assertFalse(list((self.tmp / "src").glob("zz_overlay_*.s")))
        built = fwsrc.build(self.tmp / "src", self.tmp / "out.bin")
        self.assertIsNone(fwsrc.first_difference(built, self.image))

    @unittest.skipUnless(HAVE_GCC, "arm-none-eabi-gcc not installed")
    def test_hello_c_equals_hello(self):
        """The C twin: gcc's tail call (in .text.startup, the name of a stock section)
        gives the same image."""
        fwsrc.generate(self.tmp / "stock.bin", self.tmp / "src", overlays=[HELLO_C])
        built = fwsrc.build(self.tmp / "src", self.tmp / "out.bin")
        patched = fwpatch.build(ROOT / "patches" / "hello", self.image)[0]
        self.assertIsNone(fwsrc.first_difference(built, patched))

    @unittest.skipUnless(HAVE_GCC, "arm-none-eabi-gcc not installed")
    def test_randomise_hooks_button_down(self):
        """overlays/randomise (#13) changes only the version and the ui_root vtable's
        button-down slot, which now points after the image."""
        fwsrc.generate(self.tmp / "stock.bin", self.tmp / "src", overlays=[RANDOMISE])
        built = fwsrc.build(self.tmp / "src", self.tmp / "out.bin")
        changed = [i for i in range(len(self.image)) if built[i] != self.image[i]]
        self.assertEqual(changed, [0x1B0, 0x1B1, 0x1BB24, 0x1BB25, 0x1BB26])
        self.assertEqual(built[0x1B0:0x1B2], b"\x50\x07")
        slot = int.from_bytes(built[0x1BB24:0x1BB28], "little")
        self.assertEqual(slot & 1, 1)
        self.assertTrue(0x0802BB20 <= slot - 1 < 0x0800C000 + len(built))

    def test_patch_rules_within_param_table(self):
        """The patch randomiser's rules (#45, overlays/randomise) stay inside each param's
        raw range in the firmware table at 0x0802A62A (the range param_set clamps to:
        centre + min .. centre + max) and leave the kept params alone."""
        import re
        source = (RANDOMISE / "randomise.c").read_text()
        body = source[source.index("patch_rules[] = {"):]
        body = body[:body.index("};")]
        rules = [tuple(int(v, 0) for v in m)
                 for m in re.findall(r"\{\s*(0x[0-9a-f]+),\s*(\d+),\s*(\d+),\s*(\d+),\s*(\d+)\s*\}", body)]
        for m in re.findall(r"DEPTH\((0x[0-9a-f]+),\s*(\d+),\s*(\d+),\s*(\d+)\)", body):
            pid, centre, depth, rest = (int(v, 0) for v in m)
            rules.append((pid, centre - depth, centre + depth, centre, rest))
        ids = [r[0] for r in rules]
        self.assertEqual(len(ids), len(set(ids)))
        # category, genre, octaves, ranges, patch level, audio in, env to amp, routing
        kept = {0x00, 0x01, 0x03, 0x08, 0x0a, 0x0f, 0x12, 0x17, 0x2f, 0x4b}
        self.assertEqual(set(ids) | kept, set(range(0x4c)))
        self.assertFalse(set(ids) & kept)
        table = 0x0802A62A - 0x0800C000
        for pid, lo, hi, value, rest in rules:
            centre = self.image[table + 8 * pid + 3]
            low = centre + int.from_bytes(self.image[table + 8 * pid + 4:][:1], "little", signed=True)
            high = centre + self.image[table + 8 * pid + 5]
            with self.subTest(param=hex(pid)):
                self.assertTrue(low <= lo <= hi <= high, (lo, hi, low, high))
                self.assertTrue(low <= value <= high)
                self.assertLessEqual(rest, 8)

    @unittest.skipUnless(HAVE_GCC, "arm-none-eabi-gcc not installed")
    def test_transpose_hooks_pads_and_router(self):
        """overlays/transpose (#21) changes only the version, the router vtable's note-on
        and note-off slots, the ui_root vtable's pad-down slot, the Shift LED's subscriber
        slot, the LED frame and session-read callbacks and the calls of keyboard_leds_render."""
        fwsrc.generate(self.tmp / "stock.bin", self.tmp / "src", overlays=[TRANSPOSE])
        built = fwsrc.build(self.tmp / "src", self.tmp / "out.bin")
        self.assertEqual(built[0x1B0:0x1B2], b"\x51\x07")
        slots = (0x1B268, 0x1B26C, 0x1BB1C, 0x1B6E4, 0x3B1C, 0xD658)
        for slot in slots:
            target = int.from_bytes(built[slot:slot + 4], "little")
            self.assertGreaterEqual(target, 0x0802BB20, hex(slot))
        led_calls = (0x6FFC, 0x7140, 0x7160, 0x71FA, 0x16A6C, 0x19B66)
        changed = {i for i in range(len(self.image)) if built[i] != self.image[i]}
        allowed = {0x1B0, 0x1B1} | {s + k for s in slots + led_calls for k in range(4)}
        self.assertLessEqual(changed, allowed)

    @unittest.skipUnless(HAVE_GCC, "arm-none-eabi-gcc not installed")
    def test_c_overlay_has_no_bss(self):
        ov = self.tmp / "ov"
        shutil.copytree(HELLO_C, ov)
        (ov / "hello.c").write_text('#include "firmware.h"\nstatic int calls;\n'
                                    'void main(void) { calls++; stock_main(); }\n')
        fwsrc.generate(self.tmp / "stock.bin", self.tmp / "src", overlays=[ov])
        with self.assertRaises(fwsrc.subprocess.CalledProcessError):
            fwsrc.build(self.tmp / "src", self.tmp / "out.bin")

    @unittest.skipUnless(HAVE_GXX, "arm-none-eabi-g++ not installed")
    def test_notesink_cpp_replaces_vtable_slots(self):
        """overlays/notesink_cpp (C++) changes only the version and the two note-sink
        vtable slots; g++ makes note_on the same instructions as the stock body."""
        fwsrc.generate(self.tmp / "stock.bin", self.tmp / "src", overlays=[NOTESINK_CPP])
        built = fwsrc.build(self.tmp / "src", self.tmp / "out.bin")
        changed = {i for i in range(len(self.image)) if built[i] != self.image[i]}
        vtable = 0x08027980 - 0x0800C000
        self.assertLessEqual(changed, {0x1B0, 0x1B1} | set(range(vtable, vtable + 8)))
        self.assertEqual(built[0x1B0:0x1B2], b"\x02\x08")
        on = int.from_bytes(built[vtable:vtable + 4], "little") - 1
        stock_on = int.from_bytes(self.image[vtable:vtable + 4], "little") - 1
        self.assertGreaterEqual(on, 0x0802BB20)
        body = lambda img, addr: img[addr - 0x0800C000:addr - 0x0800C000 + 0x14]
        self.assertEqual(body(built, on), body(self.image, stock_on))

    @unittest.skipUnless(HAVE_GXX, "arm-none-eabi-g++ not installed")
    def test_cpp_overlay_has_no_static_constructor(self):
        ov = self.tmp / "ov"
        shutil.copytree(NOTESINK_CPP, ov)
        src = ov / "notesink.cpp"
        src.write_text(src.read_text() + "\nstruct Boot { Boot() { stock_main(); } };\n"
                       "static const Boot boot;\n")
        fwsrc.generate(self.tmp / "stock.bin", self.tmp / "src", overlays=[ov])
        with self.assertRaises(fwsrc.subprocess.CalledProcessError):
            fwsrc.build(self.tmp / "src", self.tmp / "out.bin")

    def test_overlay_must_be_thumb_function(self):
        ov = self.tmp / "ov"
        shutil.copytree(HELLO, ov)
        hello = ov / "hello.s"
        hello.write_text(hello.read_text().replace("    .thumb_func\n", "")
                         .replace("    .type main, %function\n", ""))
        fwsrc.generate(self.tmp / "stock.bin", self.tmp / "src", overlays=[ov])
        with self.assertRaisesRegex(ValueError, "not .thumb_func functions in overlay code: main"):
            fwsrc.build(self.tmp / "src", self.tmp / "out.bin")

    @unittest.skipUnless(HAVE_REFERENCE_CC, "GCC 5.3.1 not in toolchains/ (tools/fetch_toolchain.py)")
    def test_match_places_functions_at_stock_address(self):
        """match compiles each stock-named function at its own address with the stock
        compiler: both NoteSink functions are the stock bytes, and a function with a
        non-stock name is reported, not placed."""
        src = self.tmp / "ns.cpp"
        src.write_text((NOTESINK_CPP / "notesink.cpp").read_text()
                       + '\nextern "C" int not_stock(int x) { return x + 1; }\n')
        results, _ = fwsrc.match(self.tmp / "stock.bin", [src])
        status = {name: (addr, s) for name, addr, s, _ in results}
        self.assertEqual(status["note_sink_note_on"], (0x080189CE, "MATCH"))
        self.assertEqual(status["note_sink_note_off"], (0x080189BA, "MATCH"))
        self.assertEqual(status["not_stock"], (None, "EXTRA"))

    @unittest.skipUnless(HAVE_REFERENCE_CC, "reference compiler not installed (tools/fetch_toolchain.py)")
    def test_match_ends_at_named_entry(self):
        """A function ends at the next named entry, even one Ghidra folded into it: stock
        midi_ctl_song_position (0x08018568) is followed by its this-adjusting thunk in one code
        unit."""
        src = self.tmp / "m.cpp"
        src.write_text("struct T { virtual void a(); virtual void b(); virtual void c();\n"
                       "           virtual void song_position() = 0; };\n"
                       "struct M { char pad[36]; T *t; void sp() asm(\"midi_ctl_song_position\"); };\n"
                       "void M::sp() { t->song_position(); }\n")
        results, _ = fwsrc.match(self.tmp / "stock.bin", [src])
        status = {name: (addr, s) for name, addr, s, _ in results}
        self.assertEqual(status["midi_ctl_song_position"], (0x08018568, "MATCH"))

    @unittest.skipUnless(HAVE_GCC or HAVE_REFERENCE_CC, "arm-none-eabi-gcc not installed")
    def test_match_resolves_stock_calls(self):
        """A call to another stock function links to its stock address, so a wrapper that
        only tail-calls matches the stock bytes only if the stock does the same."""
        src = self.tmp / "w.c"
        src.write_text("void usb_midi_tx_watchdog(void);\n"
                       "void main(void) { usb_midi_tx_watchdog(); }\n")
        results, sections = fwsrc.match(self.tmp / "stock.bin", [src])
        (name, addr, _, _), = results
        self.assertEqual((name, addr), ("main", 0x0800C2D0))
        code = sections[".text.main"][1]
        # b.w to usb_midi_tx_watchdog (0x0801BDDC), resolved by PROVIDE
        self.assertEqual(len(code), 4)
        hi, lo = int.from_bytes(code[:2], "little"), int.from_bytes(code[2:], "little")
        self.assertEqual(hi >> 11, 0x1E)
        self.assertEqual(lo >> 14, 0x2)

    def test_narrow_branch_cannot_redirect(self):
        # pretend the beq target at 0x080126FE is a function
        fixes = self.tmp / "fixes.tsv"
        fixes.write_text(fwsrc.LAYOUT_FIXES.read_text() + "F 080126fe narrow_target\n")
        ov = fwsrc.Overlay(self.tmp, 800, None, ["a.s"], ["narrow_target"])
        with self.assertRaisesRegex(ValueError, "cannot redirect to the overlay: 0x080126f0 beq"):
            fwsrc.plan_source(self.tmp / "stock.bin", fixes=fixes, overlays=[ov])
        with self.assertRaisesRegex(ValueError, "unknown functions: no_such"):
            fwsrc.plan_source(self.tmp / "stock.bin", overlays=[ov._replace(replace=["no_such"])])


@unittest.skipUnless(FIRMWARE_800.exists(), "firmware/monostation-firmware-800.syx not present")
@unittest.skipUnless(HAVE_BINUTILS, "arm-none-eabi binutils not installed")
class RoundTrip800Test(unittest.TestCase):
    def test_generate_build_identical(self):
        image = fwsyx.decode_image(FIRMWARE_800.read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "stock.bin").write_bytes(image)
            stats = fwsrc.generate(tmp / "stock.bin", tmp / "src")
            self.assertEqual(stats["inst"], 0)
            # src/files.tsv splits the source; the rest becomes gap files
            self.assertTrue((tmp / "src" / "vectors.s").exists())
            self.assertEqual(stats["files"], len(list((tmp / "src").glob("*.s"))))
            built = fwsrc.build(tmp / "src", tmp / "out.bin")
        self.assertIsNone(fwsrc.first_difference(built, image))

    def test_relaxed_build_identical(self):
        image = fwsyx.decode_image(FIRMWARE_800.read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "stock.bin").write_bytes(image)
            fwsrc.generate(tmp / "stock.bin", tmp / "src", relax=True)
            built = fwsrc.build(tmp / "src", tmp / "out.bin")
        self.assertIsNone(fwsrc.first_difference(built, image))

    def test_shift(self):
        """Bytes inserted at a function entry, mid-function (past narrow branch range) and
        at an odd offset in data: every reference must follow."""
        image = fwsyx.decode_image(FIRMWARE_800.read_bytes())
        pads = {0x0800C2D0: 2, 0x08017F42: 4094, 0x0802A62E: 1}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "stock.bin").write_bytes(image)
            count, errors = fwsrc.shift_test(tmp / "stock.bin", pads, relax=True,
                                             keep=tmp / "src")
            self.assertGreater(count, 40000)
            self.assertEqual(errors, [])
            # the check notices a reference that did not follow
            gen = fwsrc.plan_source(tmp / "stock.bin", relax=True, pads=pads, markers=True)
            gen.emit()
            amap = fwsrc.address_map(tmp / "src" / "firmware.elf", fwsrc.library_names(gen))
            moved = bytearray((tmp / "src" / "firmware.bin").read_bytes())
            moved[amap[0x0800C004] - fwsrc.LOAD_ADDRESS] ^= 4       # reset vector
        errors = fwsrc.shift_check(gen, bytes(moved), amap)
        self.assertEqual(len(errors), 1)
        self.assertIn("0x0800c004", errors[0])

    def test_image_header_cannot_move(self):
        image = fwsyx.decode_image(FIRMWARE_800.read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "stock.bin").write_bytes(image)
            with self.assertRaises(fwsrc.subprocess.CalledProcessError):
                fwsrc.shift_test(tmp / "stock.bin", {0x0800C004: 4})

    def test_split_files(self):
        image = fwsyx.decode_image(FIRMWARE_800.read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "stock.bin").write_bytes(image)
            # no classes, no libraries: the test's files.tsv does not have their files
            files, none = tmp / "files.tsv", tmp / "none.tsv"
            # one function per file, f1 at 2 mod 4: the fixed and relaxed builds stay identical
            files.write_text("080114c0 080114e6 f0\n080114ee 08011508 f2\n"
                             "080114e6 080114ee f1\n")
            for relax in (False, True):
                fwsrc.generate(tmp / "stock.bin", tmp / "src", files_path=files, relax=relax,
                               classes_path=none, libraries_path=none)
                self.assertTrue((tmp / "src" / "f1.s").exists())
                built = fwsrc.build(tmp / "src", tmp / "out.bin")
                self.assertIsNone(fwsrc.first_difference(built, image))
            # a narrow branch (beq at 0x080126F0) would cross into the file
            files.write_text("080126f2 08012708 t\n")
            with self.assertRaisesRegex(ValueError, "reference across files: 0x080126f0"):
                fwsrc.plan_source(tmp / "stock.bin", files_path=files, classes_path=none,
                                  libraries_path=none)
            # functions with literal loads in files that start or end at 2 mod 4 (0x080179A6):
            # their loads are spelled out from the file's start, so both builds stay identical
            files.write_text("0801797c 080179a6 t\n080179a6 080179dc u\n")
            for relax in (False, True):
                fwsrc.generate(tmp / "stock.bin", tmp / "src", files_path=files, relax=relax,
                               classes_path=none, libraries_path=none)
                self.assertIn(".Lb_080179a6:", (tmp / "src" / "u.s").read_text())
                built = fwsrc.build(tmp / "src", tmp / "out.bin")
                self.assertIsNone(fwsrc.first_difference(built, image))

    def test_classes(self):
        """classes.tsv moves a function into its class file: a file of several sections
        that the linker puts back in stock order; a wrong name is an error."""
        image = fwsyx.decode_image(FIRMWARE_800.read_bytes())
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "stock.bin").write_bytes(image)
            classes = tmp / "classes.tsv"
            # two NoteSink methods, then one far away in another compile unit
            classes.write_text("080189ba k note_sink_note_off\n080189ce k note_sink_note_on\n"
                               "080114e6 k ui_root_clear_15d\n")
            stats = fwsrc.generate(tmp / "stock.bin", tmp / "src", classes_path=classes)
            text = (tmp / "src" / "k.s").read_text()
            self.assertEqual(text.count(".section "), 2)
            self.assertIn(".section .text.k.080189ba,", text)
            self.assertEqual(stats["files"], len(list((tmp / "src").glob("*.s"))))
            built = fwsrc.build(tmp / "src", tmp / "out.bin")
            self.assertIsNone(fwsrc.first_difference(built, image))
            classes.write_text("080189ba k note_sink_note_on\n")
            with self.assertRaisesRegex(ValueError, "0x080189ba note_sink_note_on"):
                fwsrc.plan_source(tmp / "stock.bin", classes_path=classes)

if __name__ == "__main__":
    unittest.main()
