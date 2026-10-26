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
HAVE_GCC = shutil.which("arm-none-eabi-gcc") is not None
HAVE_BINUTILS = all(shutil.which(f"arm-none-eabi-{tool}")
                    for tool in ("as", "ld", "objcopy", "objdump"))


class LayoutTest(unittest.TestCase):
    def test_override_splits_units(self):
        units = [fwsrc.Unit("C", 0x100, 0x200, None)]
        out = fwsrc.override(units, fwsrc.Unit("D", 0x140, 0x150, "byte"))
        self.assertEqual([(u.kind, u.start, u.end) for u in out],
                         [("C", 0x100, 0x140), ("D", 0x140, 0x150), ("C", 0x150, 0x200)])

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
        """overlays/randomise (#39) changes only the version and the ui_root vtable's
        button-down slot, which now points after the image."""
        fwsrc.generate(self.tmp / "stock.bin", self.tmp / "src", overlays=[RANDOMISE])
        built = fwsrc.build(self.tmp / "src", self.tmp / "out.bin")
        changed = [i for i in range(len(self.image)) if built[i] != self.image[i]]
        self.assertEqual(changed, [0x1B0, 0x1BB24, 0x1BB25, 0x1BB26])
        self.assertEqual(built[0x1B0], 0x06)
        slot = int.from_bytes(built[0x1BB24:0x1BB28], "little")
        self.assertEqual(slot, 0x0802BB20 | 1)

    @unittest.skipUnless(HAVE_GCC, "arm-none-eabi-gcc not installed")
    def test_c_overlay_has_no_bss(self):
        ov = self.tmp / "ov"
        shutil.copytree(HELLO_C, ov)
        (ov / "hello.c").write_text('#include "firmware.h"\nstatic int calls;\n'
                                    'void main(void) { calls++; stock_main(); }\n')
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
            amap = fwsrc.address_map(tmp / "src" / "firmware.elf")
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
            files = tmp / "files.tsv"
            # one function per file, f1 at 2 mod 4: the fixed and relaxed builds stay identical
            files.write_text("080114c0 080114e6 f0\n080114ee 08011508 f2\n"
                             "080114e6 080114ee f1\n")
            for relax in (False, True):
                fwsrc.generate(tmp / "stock.bin", tmp / "src", files_path=files, relax=relax)
                self.assertTrue((tmp / "src" / "f1.s").exists())
                built = fwsrc.build(tmp / "src", tmp / "out.bin")
                self.assertIsNone(fwsrc.first_difference(built, image))
            # a narrow branch (beq at 0x080126F0) would cross into the file
            files.write_text("080126f2 08012708 t\n")
            with self.assertRaisesRegex(ValueError, "reference across files: 0x080126f0"):
                fwsrc.plan_source(tmp / "stock.bin", files_path=files)
            # functions with literal loads need 4-aligned files: 0x080179A6 is 2 mod 4
            files.write_text("0801797c 080179a6 t\n080179a6 080179dc u\n")
            with self.assertRaisesRegex(ValueError, r"loads in them: t \(0x0801797c-0x080179a6\), "
                                                    r"u \(0x080179a6-0x080179dc\)$"):
                fwsrc.generate(tmp / "stock.bin", tmp / "src", files_path=files)

if __name__ == "__main__":
    unittest.main()
