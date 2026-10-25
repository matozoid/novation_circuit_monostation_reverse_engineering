import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fwsrc  # noqa: E402
import fwsyx  # noqa: E402

FIRMWARE_800 = ROOT / "firmware" / "monostation-firmware-800.syx"
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
            built = fwsrc.build(tmp / "src", tmp / "out.bin")
        self.assertIsNone(fwsrc.first_difference(built, image))


if __name__ == "__main__":
    unittest.main()
