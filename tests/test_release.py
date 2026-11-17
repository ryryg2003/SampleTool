"""Release packaging: the zip holds only what is needed to run, and the version is set once."""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools"))
import build_release  # noqa: E402

from samplelib import __version__  # noqa: E402


class ReleaseZipTest(unittest.TestCase):
    def test_zip_contents(self):
        with tempfile.TemporaryDirectory() as d:
            p = build_release.build(d)
            self.assertTrue(p.endswith(f"SampleOrganizer-{__version__}.zip"))
            with zipfile.ZipFile(p) as z:
                names = z.namelist()
                self.assertIsNone(z.testzip())
            top = f"SampleOrganizer-{__version__}/"
            self.assertTrue(all(n.startswith(top) for n in names))
            rel = [n[len(top):] for n in names]
            for need in ("samplelib/__main__.py", "samplelib/server.py", "ui/index.html", "ui/app.js",
                         "Launch Sample Organizer.bat", "README.md", "NOTICE.txt",
                         "python/python.exe", "python/pythonw.exe"):
                self.assertIn(need, rel)
            allowed_top = {"samplelib", "ui", "python"} | set(build_release.INCLUDE_FILES)
            for r in rel:
                self.assertIn(r.split("/")[0], allowed_top, r)
                self.assertNotIn("__pycache__", r)
                self.assertFalse(r.endswith(".pyc"), r)

    def test_deterministic(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            pa, pb = build_release.build(a), build_release.build(b)
            self.assertEqual(open(pa, "rb").read(), open(pb, "rb").read())

    def test_version_defined_once(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")
        hits = []
        for base, dirs, files in os.walk(os.path.join(REPO, "samplelib")):
            for f in files:
                if f.endswith(".py"):
                    text = open(os.path.join(base, f), encoding="utf-8").read()
                    if re.search(r"__version__\s*=", text):
                        hits.append(f)
        self.assertEqual(hits, ["__init__.py"])


if __name__ == "__main__":
    unittest.main()
