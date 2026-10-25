import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fwpatch  # noqa: E402
import fwsyx  # noqa: E402

FIRMWARE_800 = ROOT / "firmware" / "monostation-firmware-800.syx"
HAVE_BINUTILS = all(shutil.which(f"arm-none-eabi-{tool}") for tool in ("as", "ld", "objcopy", "readelf"))


def halfwords(data):
    return struct.unpack("<HH", data)


class BranchEncodingTest(unittest.TestCase):
    # (site, target, link, halfwords) from objdump of the stock image and of ld output
    VECTORS = [
        (0x08025E86, 0x0801A848, True, (0xF7F4, 0xFCDF)),   # stock reset handler, backward
        (0x08025E8A, 0x08025EE0, True, (0xF000, 0xF829)),   # stock reset handler, forward
        (0x08025E8E, 0x0800C2D0, True, (0xF7E6, 0xFA1F)),   # stock call of main
        (0x0802BB22, 0x080202A0, True, (0xF7F4, 0xFBBD)),   # ld: bl
        (0x0802BB28, 0x080202A0, False, (0xF7F4, 0xBBBA)),  # ld: b.w
    ]

    def test_vectors(self):
        for at, target, link, expected in self.VECTORS:
            with self.subTest(at=hex(at)):
                self.assertEqual(halfwords(fwpatch.encode_branch(at, target, link)), expected)

    def test_thumb_bit_is_ignored(self):
        self.assertEqual(fwpatch.encode_branch(0x08025E8E, 0x0800C2D1, True),
                         fwpatch.encode_branch(0x08025E8E, 0x0800C2D0, True))

    def test_range(self):
        fwpatch.encode_branch(0x08000000, 0x08000004 + (1 << 24) - 2, True)
        fwpatch.encode_branch(0x08000000 + (1 << 24), 0x08000004, False)
        with self.assertRaises(fwpatch.PatchError):
            fwpatch.encode_branch(0x08000000, 0x08000004 + (1 << 24), True)
        with self.assertRaises(fwpatch.PatchError):
            fwpatch.encode_branch(0x08000001, 0x08001000, True)

    def test_version_bcd(self):
        self.assertEqual(fwpatch.version_bcd(800), b"\x00\x08")
        self.assertEqual(fwpatch.version_bcd(801), b"\x01\x08")
        self.assertEqual(fwpatch.version_bcd(1234), b"\x34\x12")


@unittest.skipUnless(FIRMWARE_800.exists(), "firmware/monostation-firmware-800.syx not present")
@unittest.skipUnless(HAVE_BINUTILS, "arm-none-eabi binutils not installed")
class BuildTest(unittest.TestCase):
    TOML = """
base = 800
version = 801
sources = ["wrap.s"]

[functions]
fw_main = 0x0800C2D0

[[hook]]
at = 0x08025E8E
kind = "bl"
target = "wrap_main"
expect = "e6 f7 1f fa"

[[hook]]
at = 0x0800C004
kind = "word"
target = "wrap_main"
expect = "61 5e 02 08"

[[hook]]
at = 0x0800C1B4
kind = "bytes"
data = "aa bb"
expect = "00 00"
"""
    SOURCE = """
    .syntax unified
    .thumb
    .text
    .global wrap_main
    .thumb_func
wrap_main:
    b.w fw_main
"""

    @classmethod
    def setUpClass(cls):
        cls.stock = fwsyx.decode_image(FIRMWARE_800.read_bytes())

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir)
        (self.dir / "wrap.s").write_text(self.SOURCE)

    def build(self, toml=TOML, source=None):
        (self.dir / "patch.toml").write_text(toml)
        if source is not None:
            (self.dir / "wrap.s").write_text(source)
        return fwpatch.build(self.dir, self.stock)

    def test_build(self):
        image, edits, symbols, origin, size = self.build()
        self.assertEqual(origin, 0x0802BB20)
        self.assertEqual(symbols["wrap_main"], origin | 1)
        self.assertEqual(symbols["fw_main"], 0x0800C2D1)
        self.assertEqual(size, 4)
        at = lambda address, n: image[address - fwsyx.LOAD_ADDRESS:][:n]
        self.assertEqual(at(origin, 4), fwpatch.encode_branch(origin, 0x0800C2D0, link=False))
        self.assertEqual(at(0x08025E8E, 4), fwpatch.encode_branch(0x08025E8E, origin, link=True))
        self.assertEqual(at(0x0800C004, 4), struct.pack("<I", origin | 1))
        self.assertEqual(at(0x0800C1B4, 2), b"\xAA\xBB")
        self.assertEqual(at(0x0800C1AC, 6), b"MAIN\x01\x08")
        self.assertEqual([e[1] for e in edits], ["bl", "word", "bytes", "version"])
        # every other byte of the stock image is unchanged
        changed = {a - fwsyx.LOAD_ADDRESS + i for a, _, _, new in edits for i in range(len(new))}
        self.assertTrue(all(image[i] == b for i, b in enumerate(self.stock) if i not in changed))

    def test_syx_round_trip(self):
        image = self.build()[0]
        decoded = fwsyx.decode_image(fwsyx.encode_firmware(image, fwsyx.MONOSTATION_PRODUCT_ID, 801))
        self.assertEqual(decoded[:len(image)], image)

    def test_expect_mismatch(self):
        with self.assertRaisesRegex(fwpatch.PatchError, "expected"):
            self.build(self.TOML.replace("e6 f7 1f fa", "e6 f7 1f fb"))

    def test_overlap(self):
        toml = self.TOML.replace('at = 0x0800C1B4', 'at = 0x0800C006').replace(
            'expect = "00 00"', 'expect = "02 08"')
        with self.assertRaisesRegex(fwpatch.PatchError, "overlap"):
            self.build(toml)

    def test_unknown_symbol(self):
        with self.assertRaisesRegex(fwpatch.PatchError, "unknown symbol"):
            self.build(self.TOML.replace('target = "wrap_main"\nexpect = "e6', 'target = "nope"\nexpect = "e6'))

    def test_data_section_rejected(self):
        with self.assertRaisesRegex(fwpatch.PatchError, ".data or .bss"):
            self.build(source=self.SOURCE + "\n    .data\ncounter: .word 0\n")

    def test_wrong_base_image(self):
        self.stock = bytes([self.stock[0] ^ 1]) + self.stock[1:]
        with self.assertRaisesRegex(fwpatch.PatchError, "not stock"):
            self.build()


if __name__ == "__main__":
    unittest.main()
