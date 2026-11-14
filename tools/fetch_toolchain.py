#!/usr/bin/env python3
"""Install the toolchain and ST libraries the stock firmware was built with.

GNU Tools for ARM Embedded Processors 5-2016-q1-update (GCC 5.3.1 20160307), found by
matching newlib-nano code in the image (docs/source-build.md *Compiler version*).
`fwsrc.py match` uses it from toolchains/ when present.

ST's STM32F4 Standard Peripheral Library V1.8.0 (STSW-STM32065) and USB OTG
host/device library V2.2.1 (STSW-STM32046): `fwsrc.py build` compiles the drivers
libraries.tsv links (docs/source-build.md *ST libraries*). st.com needs a login to
download them, so put the zips in toolchains/ (or pass --archive) and run this.

    python3 tools/fetch_toolchain.py [--dest toolchains] [--archive FILE]...

Linux x86-64 only: Launchpad has no other host build of this release for us to use.
Every archive is checked against a pinned hash before it is unpacked.
"""
import argparse
import hashlib
import shutil
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "gcc-arm-none-eabi-5_3-2016q1"
ARCHIVE = NAME + "-20160330-linux.tar.bz2"
URL = "https://launchpad.net/gcc-arm-embedded/5.0/5-2016-q1-update/+download/" + ARCHIVE
MD5 = "5a261cac18c62d8b7e8c70beba2004bd"
# ST packages: name -> (directory in dest, archive name st.com serves, SHA-256). Each
# unpacks into its directory (the USB zip has no top-level one of its own).
ST_PACKAGES = {
    "stdperiph": ("STM32F4xx_DSP_StdPeriph_Lib_V1.8.0", "stm32f4_dsp_stdperiph_lib.zip",
                  "6c215f9847c2d7229ea6eeeb545ad27437fcc0d8c0d5a0e5d792c4aba08cbbb0"),
    "usb": ("STM32_USB-Host-Device_Lib_V2.2.1", "stm32_f105-07_f2_f4_usb-host-device_lib.zip",
            "ece9d4715c69f68b61f1cf7f9b6645150cdf43c1bfd7649d9662e1c9f803d16b"),
}


def prefix(dest=ROOT / "toolchains"):
    """The arm-none-eabi- prefix inside dest, or None when it is not installed."""
    p = Path(dest) / NAME / "bin" / "arm-none-eabi-"
    return str(p) if Path(str(p) + "gcc").is_file() else None


def package_dir(name, dest=ROOT / "toolchains"):
    """Where the ST package name is installed in dest, or None when it is not."""
    d = Path(dest) / ST_PACKAGES[name][0]
    return d if (d / ".installed").is_file() else None


def file_hash(path, algorithm):
    h = hashlib.new(algorithm)
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_zip_names(z, dest):
    dest = dest.resolve()
    for info in z.infolist():
        target = (dest / info.filename).resolve()
        if dest != target and dest not in target.parents:
            raise SystemExit(f"refusing archive entry outside {dest}: {info.filename}")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise SystemExit(f"refusing symlink in archive: {info.filename}")
    return z.namelist()


def install_st(name, archive, dest):
    """Unpack the ST package name from archive (checked against its SHA-256) into dest."""
    directory, _, sha = ST_PACKAGES[name]
    dest = Path(dest)
    if package_dir(name, dest):
        print(f"already installed: {package_dir(name, dest)}")
        return package_dir(name, dest)
    got = file_hash(archive, "sha256")
    if got != sha:
        raise SystemExit(f"{archive}: SHA-256 mismatch: {got} != {sha}")
    with tempfile.TemporaryDirectory(dir=dest) as tmp:
        out = Path(tmp) / directory
        out.mkdir()
        with zipfile.ZipFile(archive) as z:
            z.extractall(out, safe_zip_names(z, out))
        inner = out / directory     # the StdPeriph zip has its own top-level directory
        if inner.is_dir() and len(list(out.iterdir())) == 1:
            out = inner
        (out / ".installed").write_text(sha + "\n")
        out.rename(dest / directory)
    print(f"installed: {dest / directory}")
    return dest / directory


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
        got = file_hash(archive, "md5")
        if got != MD5:
            raise SystemExit(f"MD5 mismatch: {got} != {MD5}")
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
    ap.add_argument("--archive", action="append", default=[],
                    help="an already downloaded archive (the compiler's tarball or an ST zip, "
                         "told apart by name); repeatable")
    a = ap.parse_args()
    dest = Path(a.dest)
    by_name = {Path(p).name: Path(p) for p in a.archive}
    unknown = set(by_name) - {ARCHIVE} - {v[1] for v in ST_PACKAGES.values()}
    if unknown:
        raise SystemExit("unknown archives: " + ", ".join(sorted(unknown)))
    fetch(dest, by_name.get(ARCHIVE))
    for name, (directory, archive, _) in ST_PACKAGES.items():
        path = by_name.get(archive, dest / archive)
        if package_dir(name, dest):
            print(f"already installed: {package_dir(name, dest)}")
        elif path.is_file():
            install_st(name, path, dest)
        else:
            print(f"missing {archive}: download it from st.com into {dest} ({directory})")


if __name__ == "__main__":
    sys.exit(main())
