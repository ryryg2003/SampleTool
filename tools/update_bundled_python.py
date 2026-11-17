"""Refresh the bundled Windows Python in python/ (developer tool, not shipped).

Usage: python tools/update_bundled_python.py [VERSION]

Downloads the official "Windows embeddable package (64-bit)" from python.org,
checks its SHA-256 against the checksum python.org publishes in the matching
.spdx.json file, unpacks it into python/ (replacing what is there) and adds one
line, "..", to pythonXY._pth so `python\\python.exe -m samplelib` finds the app
next to the python folder. Nothing else in the official package is changed.

The launchers use python\\python.exe / python\\pythonw.exe when present and
fall back to an installed Python 3.10+ otherwise.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import sys
import urllib.request
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = os.path.join(REPO, "python")
DEFAULT_VERSION = "3.14.10"
BASE = "https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip"
APP_PATH_LINE = ".."


def _get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as r:
        return r.read()


def official_sha256(version: str) -> str:
    """The SHA-256 python.org publishes for the package (from its SPDX file)."""
    name = f"python-{version}-embed-amd64.zip"
    spdx = json.loads(_get(BASE.format(v=version) + ".spdx.json"))
    for pkg in spdx.get("packages", []):
        if pkg.get("packageFileName") == name or pkg.get("name") == name:
            for c in pkg.get("checksums", []):
                if c.get("algorithm") == "SHA256":
                    return c["checksumValue"].lower()
    for f in spdx.get("files", []):
        if f.get("fileName", "").endswith(name):
            for c in f.get("checksums", []):
                if c.get("algorithm") == "SHA256":
                    return c["checksumValue"].lower()
    raise SystemExit(f"No SHA-256 for {name} in the published SPDX file")


def patch_pth(text: str) -> str:
    """Add the app folder ("..") right after the stdlib zip line; keep everything else."""
    lines = text.replace("\r\n", "\n").split("\n")
    if APP_PATH_LINE in (ln.strip() for ln in lines):
        return text
    out = []
    for ln in lines:
        out.append(ln)
        if ln.strip() == ".":
            out.append(APP_PATH_LINE)
    if APP_PATH_LINE not in out:
        out.insert(1, APP_PATH_LINE)
    return "\r\n".join(out)


def install(blob: bytes, dest: str = DEST) -> list[str]:
    """Unpack the package into dest and patch its ._pth. Returns the file names."""
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(dest)
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        names = z.namelist()
        for n in names:
            if n.endswith("/") or "/" in n or "\\" in n or n.startswith("."):
                raise SystemExit(f"Unexpected entry in the package: {n}")
        z.extractall(dest)
    pth = [n for n in names if n.endswith("._pth")]
    if len(pth) != 1:
        raise SystemExit(f"Expected one ._pth file, found {pth}")
    p = os.path.join(dest, pth[0])
    with open(p, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(patch_pth(text))
    return sorted(names)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    version = argv[0] if argv else DEFAULT_VERSION
    url = BASE.format(v=version)
    print(f"Downloading {url}")
    blob = _get(url)
    digest = hashlib.sha256(blob).hexdigest()
    want = official_sha256(version)
    if digest != want:
        raise SystemExit(f"SHA-256 mismatch: got {digest}, python.org says {want}")
    print(f"SHA-256 {digest} matches python.org")
    names = install(blob)
    print(f"Installed {len(names)} files into {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
