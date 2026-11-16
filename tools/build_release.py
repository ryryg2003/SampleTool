"""Build the release zip: only what a producer needs to run the app.

Usage: python tools/build_release.py [--out DIR]

Creates <out>/SampleOrganizer-<version>.zip (default out: dist/) containing
samplelib/, ui/, the launcher .bat files, README.md, CHANGELOG.md and
NOTICE.txt, all under a top-level "SampleOrganizer-<version>/" folder.
Tests, tools, docs and caches are left out. The zip is deterministic: fixed
timestamps and sorted entries, so the same source gives the same bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INCLUDE_DIRS = ("samplelib", "ui")
INCLUDE_FILES = ("Launch Sample Organizer.bat", "Create Desktop Shortcut.bat",
                 "README.md", "CHANGELOG.md", "NOTICE.txt")
SKIP_PARTS = {"__pycache__", ".pytest_cache"}
SKIP_EXT = {".pyc", ".pyo"}
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def version() -> str:
    """Read __version__ from samplelib/__init__.py without importing the package."""
    text = open(os.path.join(REPO, "samplelib", "__init__.py"), encoding="utf-8").read()
    m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not m:
        raise SystemExit("samplelib/__init__.py has no __version__")
    return m.group(1)


def release_files() -> list[str]:
    """Repo-relative paths (with "/") that go into the zip, sorted."""
    out = []
    for name in INCLUDE_FILES:
        if not os.path.isfile(os.path.join(REPO, name)):
            raise SystemExit(f"Missing release file: {name}")
        out.append(name)
    for d in INCLUDE_DIRS:
        for base, dirs, files in os.walk(os.path.join(REPO, d)):
            dirs[:] = sorted(x for x in dirs if x not in SKIP_PARTS)
            for f in files:
                if os.path.splitext(f)[1] in SKIP_EXT:
                    continue
                out.append(os.path.relpath(os.path.join(base, f), REPO).replace(os.sep, "/"))
    return sorted(out)


def build(out_dir: str) -> str:
    """Write the zip and return its path."""
    ver = version()
    top = f"SampleOrganizer-{ver}"
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, top + ".zip")
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel in release_files():
            info = zipfile.ZipInfo(f"{top}/{rel}", FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(os.path.join(REPO, rel.replace("/", os.sep)), "rb") as f:
                z.writestr(info, f.read())
    os.replace(tmp, path)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=os.path.join(REPO, "dist"))
    a = ap.parse_args(argv)
    p = build(a.out)
    digest = hashlib.sha256(open(p, "rb").read()).hexdigest()
    with zipfile.ZipFile(p) as z:
        n = len(z.namelist())
    print(f"{p}\n{n} files, {os.path.getsize(p):,} bytes, sha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
