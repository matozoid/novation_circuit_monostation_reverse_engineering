import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fwsyx  # noqa: E402

FIRMWARE_800 = ROOT / "firmware" / "monostation-firmware-800.syx"
# Novation Peak build 352 (Components catalogue id 10): same container family, used as a second data point.
PEAK_352 = ROOT / "firmware" / "peak-firmware-352.syx"


class ContainerChecks:
    path = None
    product_id = None
    version = None

    @classmethod
    def setUpClass(cls):
        cls.data = cls.path.read_bytes()
        cls.messages = fwsyx.split_messages(cls.data)
        cls.image = fwsyx.decode_image(cls.data)

    def test_start_message(self):
        start = self.messages[0]
        self.assertEqual(fwsyx.parse_start(start), (self.product_id, self.version))
        self.assertEqual(fwsyx.build_start(self.product_id, self.version), start)

    def test_trailer_checksum(self):
        trailer = self.messages[-1]
        self.assertEqual(fwsyx.parse_trailer(trailer), fwsyx.checksum(self.image))
        self.assertEqual(fwsyx.build_trailer(fwsyx.checksum(self.image)), trailer)


@unittest.skipUnless(PEAK_352.exists(), "firmware/peak-firmware-352.syx not downloaded")
class Peak352Test(ContainerChecks, unittest.TestCase):
    path = PEAK_352
    product_id = (0x00, 0x7E)
    version = 352


class BlockCodingTest(unittest.TestCase):
    def test_round_trip(self):
        payload = bytes(range(0xE0, 0x100))
        septets = fwsyx.encode_block(payload, 5)
        self.assertEqual(len(septets), fwsyx.BLOCK_SEPTETS)
        self.assertTrue(all(s <= 0x7F for s in septets))
        self.assertEqual(fwsyx.decode_block(septets), (payload, 5))

    def test_ff_fill_packs_to_7f(self):
        septets = fwsyx.encode_block(b"\xff" * 32, 7)
        self.assertEqual(septets, b"\x7f" * 37)


@unittest.skipUnless(FIRMWARE_800.exists(), "firmware/monostation-firmware-800.syx not extracted")
class Firmware800Test(ContainerChecks, unittest.TestCase):
    path = FIRMWARE_800
    product_id = fwsyx.MONOSTATION_PRODUCT_ID
    version = 800

    def test_image_size(self):
        self.assertEqual(len(self.image), 129824)

    def test_vector_table(self):
        initial_sp, reset = struct.unpack("<2I", self.image[:8])
        self.assertEqual(initial_sp, 0x10010000)
        self.assertEqual(reset, 0x08025E61)

    def test_spare_bits_are_next_block_lookahead(self):
        blocks = fwsyx.data_blocks(self.messages)
        for (_, _, spare), (_, next_payload, _) in zip(blocks, blocks[1:]):
            self.assertEqual(spare, next_payload[0] >> (8 - fwsyx.SPARE_BITS))
        self.assertEqual(blocks[-1][2], 0)

    def test_data_messages_reencode_exactly(self):
        for message in self.messages:
            if fwsyx.command(message) in (fwsyx.CMD_DATA, fwsyx.CMD_DATA_FIRST):
                body = message[len(fwsyx.NOVATION_HEADER) + 1:-1]
                self.assertEqual(fwsyx.encode_block(*fwsyx.decode_block(body)), body)


if __name__ == "__main__":
    unittest.main()
