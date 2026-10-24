import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fwsyx  # noqa: E402

FIRMWARE_800 = ROOT / "firmware" / "monostation-firmware-800.syx"


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
class Firmware800Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = FIRMWARE_800.read_bytes()
        cls.messages = fwsyx.split_messages(cls.data)
        cls.image = fwsyx.decode_image(cls.data)

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
