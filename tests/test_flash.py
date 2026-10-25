"""Tests for the stdlib helpers in tools/flash.py (no MIDI device needed)."""
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import flash  # noqa: E402
import fwsyx  # noqa: E402

FIRMWARE_800 = ROOT / "firmware" / "monostation-firmware-800.syx"


def fake_image(sp=0x10010000, reset=0x0800C101, version=b"\x00\x08", size=0x400):
    image = bytearray(size)
    struct.pack_into("<II", image, 0, sp, reset)
    image[0x1AC:0x1B2] = b"MAIN" + version
    return bytes(image)


def fake_syx(image=None, version=800, product_id=fwsyx.MONOSTATION_PRODUCT_ID):
    return fwsyx.encode_firmware(image or fake_image(), product_id, version)


def ack(counter):
    return bytes([0xF0, 0x00, 0x20, 0x29, 0x00, 0x72,
                  counter & 0x7F, (counter >> 7) & 0x7F, counter >> 14, 0xF7])


def status(code):
    return bytes([0xF0, 0x00, 0x20, 0x29, 0x00, 0x73, code, 0, 0, 0xF7])


class CheckTest(unittest.TestCase):
    def test_fake(self):
        packets, info = flash.check_syx(fake_syx())
        self.assertEqual(info["version"], 800)
        self.assertEqual(info["blocks"], 0x400 // 32)
        self.assertEqual(len(packets), 1 + info["blocks"])
        self.assertEqual(fwsyx.command(packets[0]), fwsyx.CMD_START)
        self.assertEqual(fwsyx.command(packets[-1]), fwsyx.CMD_DATA_FIRST)

    def test_header_version(self):
        self.assertEqual(flash.header_version(fake_image(version=b"\x01\x08")), 801)
        with self.assertRaises(ValueError):
            flash.header_version(fake_image(version=b"\x0a\x08"))

    def rejects(self, data, reason):
        with self.assertRaisesRegex(ValueError, reason):
            flash.check_syx(data)

    def test_rejects_product(self):
        self.rejects(fake_syx(product_id=(0x00, 0x7E)), "product id")

    def test_rejects_bad_checksum(self):
        msgs = fwsyx.split_messages(fake_syx())
        msgs[-1] = fwsyx.build_trailer(1234)
        self.rejects(b"".join(msgs), "checksum")

    def test_rejects_missing_trailer(self):
        self.rejects(b"".join(fwsyx.split_messages(fake_syx())[:-1]), "trailer")

    def test_rejects_order(self):
        msgs = fwsyx.split_messages(fake_syx())
        msgs[-2], msgs[-3] = msgs[-3], msgs[-2]   # 0x73 no longer last data block
        self.rejects(b"".join(msgs), "0x73")

    def test_rejects_vectors(self):
        self.rejects(fake_syx(fake_image(sp=0x08000000)), "stack pointer")
        self.rejects(fake_syx(fake_image(reset=0x0800C100)), "reset vector")
        self.rejects(fake_syx(fake_image(reset=0x08000101)), "reset vector")

    def test_rejects_missing_header(self):
        image = bytearray(fake_image())
        image[0x1AC:0x1B0] = b"XXXX"
        self.rejects(fake_syx(bytes(image)), "MAIN")

    @unittest.skipUnless(FIRMWARE_800.exists(), "firmware/monostation-firmware-800.syx not present")
    def test_stock_800(self):
        data = FIRMWARE_800.read_bytes()
        packets, info = flash.check_syx(data)
        self.assertEqual((info["version"], info["container_version"]), (800, 800))
        self.assertEqual(len(packets), 4058)          # all but the 0x76 trailer
        self.assertEqual(packets, fwsyx.split_messages(data)[:-1])


class AckTest(unittest.TestCase):
    def test_short(self):
        self.assertEqual(flash.parse_ack(ack(0)), (0, True))
        self.assertEqual(flash.parse_ack(ack(4057)), (4057, True))

    def test_long(self):
        msg = bytes([0xF0, 0x00, 0x20, 0x29, 0x00, 0x72, 0, 0, 0, 0, 0, 1, 2, 3, 0xF7])
        self.assertEqual(flash.parse_ack(msg), (0x123, False))

    def test_other(self):
        self.assertIsNone(flash.parse_ack(status(127)))
        self.assertEqual(flash.parse_status(status(127)), 127)
        self.assertIsNone(flash.parse_status(ack(1)))


class FakeBootloader:
    """Acks every packet with its index; `replies` overrides what follows packet i."""
    def __init__(self, replies=None):
        self.sent, self.queue, self.replies = [], [], replies or {}

    def send(self, msg):
        i = len(self.sent)
        self.sent.append(msg)
        self.queue += self.replies.get(i, [ack(i)])

    def next_msg(self, timeout):
        return self.queue.pop(0) if self.queue else None


class SendTest(unittest.TestCase):
    def setUp(self):
        self.packets = flash.check_syx(fake_syx())[0]

    def test_all_sent_in_order(self):
        dev = FakeBootloader()
        flash.send_firmware(self.packets, dev.send, dev.next_msg)
        self.assertEqual(dev.sent, self.packets)

    def test_stale_ack_ignored(self):
        # packet 3 gets a repeated ack 2 first, then its own; a status 127 acks packet 5
        dev = FakeBootloader({3: [ack(2), ack(3)], 5: [status(0), status(127)]})
        flash.send_firmware(self.packets, dev.send, dev.next_msg)
        self.assertEqual(dev.sent, self.packets)

    def test_missing_ack(self):
        dev = FakeBootloader({4: [ack(3)]})
        with self.assertRaisesRegex(flash.FlashError, "no ack"):
            flash.send_firmware(self.packets, dev.send, dev.next_msg, timeout=0.01)
        self.assertEqual(len(dev.sent), 5)

    def test_failure_status(self):
        dev = FakeBootloader({2: [status(124)]})
        with self.assertRaisesRegex(flash.FlashError, "not viable"):
            flash.send_firmware(self.packets, dev.send, dev.next_msg)
        self.assertEqual(len(dev.sent), 3)


if __name__ == "__main__":
    unittest.main()
