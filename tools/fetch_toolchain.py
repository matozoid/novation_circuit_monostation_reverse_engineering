#!/usr/bin/env python3
"""Download the toolchain the stock firmware was built with.

GNU Tools for ARM Embedded Processors 5-2016-q1-update (GCC 5.3.1 20160307), found by
matching newlib-nano code in the image (docs/source-build.md *Compiler version*).
`fwsrc.py match` uses it from toolchains/ when present.

    python3 tools/fetch_toolchain.py [--dest toolchains]

Linux x86-64 only: Launchpad has no other host build of this release for us to use.
The archive is checked against Launchpad's MD5 before it is unpacked.
"""
import argparse
import hashlib
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "gcc-arm-none-eabi-5_3-2016q1"
ARCHIVE = NAME + "-20160330-linux.tar.bz2"
URL = "https://launchpad.net/gcc-arm-embedded/5.0/5-2016-q1-update/+download/" + ARCHIVE
MD5 = "5a261cac18c62d8b7e8c70beba2004bd"


def prefix(dest=ROOT / "toolchains"):
    """The arm-none-eabi- prefix inside dest, or None when it is not installed."""
    p = Path(dest) / NAME / "bin" / "arm-none-eabi-"
    return str(p) if Path(str(p) + "gcc").is_file() else None


def safe_members(tar, dest):
    dest = dest.resolve()
    for m in tar.getmembers():
        target = (dest / m.name).resolve()
        if dest != target and dest not in target.parents:
            raise SystemExit(f"refusing archive entry outside {dest}: {m.name}")
        if m.issym() or m.islnk():
            link = (target.parent / m.linkname).resolve() if m.issym() else (dest / m.linkname).resolve()
            if dest != link and dest not in link.parents:
                raise SystemExit(f"refusing link outside {dest}: {m.name} -> {m.linkname}")
        if not (m.isfile() or m.isdir() or m.issym() or m.islnk()):
            raise SystemExit(f"refusing special archive entry: {m.name}")
        yield m


def fetch(dest, archive=None):
    dest = Path(dest)
    if prefix(dest):
        print(f"already installed: {prefix(dest)}")
        return prefix(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest) as tmp:
        tmp = Path(tmp)
        if archive is None:
            archive = tmp / ARCHIVE
            print(f"downloading {URL}")
            with urllib.request.urlopen(URL) as r, open(archive, "wb") as f:
                shutil.copyfileobj(r, f)
        h = hashlib.md5()
        with open(archive, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != MD5:
            raise SystemExit(f"MD5 mismatch: {h.hexdigest()} != {MD5}")
        out = tmp / "x"
        out.mkdir()
        with tarfile.open(archive, "r:bz2") as t:
            t.extractall(out, members=safe_members(t, out))
        (out / NAME).rename(dest / NAME)
    print(f"installed: {prefix(dest)}")
    return prefix(dest)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dest", default=ROOT / "toolchains", help="install directory (default toolchains/)")
    ap.add_argument("--archive", help="use an already downloaded archive instead of fetching")
    a = ap.parse_args()
    fetch(a.dest, a.archive)


if __name__ == "__main__":
    sys.exit(main())
