import io
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import fetch_toolchain  # noqa: E402


def tar_with(*entries):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, kind, link in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = link
            t.addfile(info, io.BytesIO(b"") if kind == tarfile.REGTYPE else None)
    buf.seek(0)
    return tarfile.open(fileobj=buf)


class SafeMembersTest(unittest.TestCase):
    def setUp(self):
        self.dest = Path(tempfile.mkdtemp())

    def check(self, *entries):
        return [m.name for m in fetch_toolchain.safe_members(tar_with(*entries), self.dest)]

    def test_accepts_plain_tree(self):
        self.assertEqual(self.check(("a/bin/gcc", tarfile.REGTYPE, ""),
                                    ("a/bin/cc", tarfile.SYMTYPE, "gcc")),
                         ["a/bin/gcc", "a/bin/cc"])

    def test_refuses_traversal(self):
        with self.assertRaisesRegex(SystemExit, "outside"):
            self.check(("../evil", tarfile.REGTYPE, ""))

    def test_refuses_symlink_out(self):
        with self.assertRaisesRegex(SystemExit, "link outside"):
            self.check(("a/x", tarfile.SYMTYPE, "../../../etc/passwd"))

    def test_refuses_devices(self):
        with self.assertRaisesRegex(SystemExit, "special"):
            self.check(("a/dev", tarfile.CHRTYPE, ""))


if __name__ == "__main__":
    unittest.main()
