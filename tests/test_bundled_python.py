"""The bundled Windows Python in python/ and the launchers that use it.

Cross-platform checks look at the files. On Windows the bundled python.exe is
started for real: it must import the whole app and report its version.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PYDIR = os.path.join(REPO, "python")
sys.path.insert(0, os.path.join(REPO, "tools"))
import build_release  # noqa: E402
import update_bundled_python as ubp  # noqa: E402

from samplelib import __version__  # noqa: E402

IS_WIN = os.name == "nt"


def _read(name: str) -> str:
    with open(os.path.join(REPO, name), encoding="utf-8") as f:
        return f.read()


def _pth() -> str:
    names = [n for n in os.listdir(PYDIR) if n.endswith("._pth")]
    assert len(names) == 1, names
    return names[0]


class BundledFilesTest(unittest.TestCase):
    def test_official_package_files_present(self):
        for need in ("python.exe", "pythonw.exe", "python3.dll", "vcruntime140.dll", "LICENSE.txt"):
            self.assertTrue(os.path.isfile(os.path.join(PYDIR, need)), need)
        pth = _pth()
        stem = pth[:-len("._pth")]
        self.assertRegex(stem, r"^python3\d+$")
        for need in (stem + ".dll", stem + ".zip"):
            self.assertTrue(os.path.isfile(os.path.join(PYDIR, need)), need)

    def test_windows_x64_executables(self):
        for exe in ("python.exe", "pythonw.exe"):
            with open(os.path.join(PYDIR, exe), "rb") as f:
                head = f.read(4096)
            self.assertEqual(head[:2], b"MZ", exe)
            pe = int.from_bytes(head[0x3C:0x40], "little")
            self.assertEqual(head[pe:pe + 4], b"PE\0\0", exe)
            machine = int.from_bytes(head[pe + 4:pe + 6], "little")
            self.assertEqual(machine, 0x8664, f"{exe} is not x64")

    def test_pth_finds_the_app(self):
        with open(os.path.join(PYDIR, _pth()), encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.read().splitlines()]
        stem = _pth()[:-len("._pth")]
        self.assertEqual(lines[0], stem + ".zip", "the stdlib zip stays first")
        self.assertIn(".", lines)
        self.assertIn("..", lines, "the app folder (one level up) must be importable")
        self.assertNotIn("import site", lines, "no site-packages: nothing gets installed")

    def test_stdlib_has_what_the_app_imports(self):
        stem = _pth()[:-len("._pth")]
        with zipfile.ZipFile(os.path.join(PYDIR, stem + ".zip")) as z:
            have = set(z.namelist())
        for mod in ("argparse", "csv", "gzip", "hashlib", "logging/handlers", "mimetypes",
                    "http/server", "urllib/request", "webbrowser", "subprocess", "ctypes/wintypes",
                    "uuid", "json/__init__", "unittest/__init__"):
            self.assertIn(mod + ".pyc", have, mod)

    def test_patch_pth_is_minimal_and_idempotent(self):
        orig = "python314.zip\r\n.\r\n\r\n# Uncomment to run site.main() automatically\r\n#import site\r\n"
        once = ubp.patch_pth(orig)
        self.assertEqual(once, "python314.zip\r\n.\r\n..\r\n\r\n# Uncomment to run site.main() automatically\r\n#import site\r\n")
        self.assertEqual(ubp.patch_pth(once), once)

    def test_install_refuses_nested_paths(self):
        import io
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("python.exe", b"MZ")
            z.writestr("../evil.txt", b"x")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                ubp.install(buf.getvalue(), os.path.join(d, "python"))

    def test_git_keeps_bytes_and_ships_it(self):
        attrs = _read(".gitattributes")
        self.assertIn("python/** binary", attrs)
        self.assertNotRegex(attrs, r"(?m)^python/?\S*\s+export-ignore")

    def test_release_zip_contains_bundled_python(self):
        rel = build_release.release_files()
        for need in ("python/python.exe", "python/pythonw.exe", "python/" + _pth()):
            self.assertIn(need, rel)
        with tempfile.TemporaryDirectory() as d:
            p = build_release.build(d)
            with zipfile.ZipFile(p) as z:
                top = f"SampleOrganizer-{__version__}/"
                data = z.read(top + "python/python.exe")
        with open(os.path.join(PYDIR, "python.exe"), "rb") as f:
            self.assertEqual(hashlib.sha256(data).hexdigest(), hashlib.sha256(f.read()).hexdigest())


class LauncherPrefersBundledTest(unittest.TestCase):
    def test_console_launcher(self):
        bat = _read("Launch Sample Organizer.bat")
        i_bundled = bat.index(r'python\python.exe')
        self.assertLess(i_bundled, bat.index("where py"), "bundled Python comes first")
        self.assertLess(bat.index("where py"), bat.index("where python"), "installed Python is the fallback")
        self.assertNotIn("E:\\", bat, "no E: drive default")

    def test_shortcut_launcher(self):
        bat = _read("Create Desktop Shortcut.bat")
        self.assertLess(bat.index(r"python\pythonw.exe"), bat.index("py -3"))
        self.assertIn("goto :make", bat)


@unittest.skipUnless(IS_WIN, "Windows only: runs the bundled python.exe")
class BundledRuntimeTest(unittest.TestCase):
    def _run(self, *args, cwd=None):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
        return subprocess.run([os.path.join(PYDIR, "python.exe"), *args], cwd=cwd or REPO, env=env,
                              capture_output=True, text=True, timeout=120)

    def test_version_from_any_folder(self):
        with tempfile.TemporaryDirectory() as d:
            r = self._run("-m", "samplelib", "--version", cwd=d)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(__version__, r.stdout + r.stderr)

    def test_imports_every_app_module(self):
        code = ("import pkgutil, importlib, sys, samplelib;"
                "[importlib.import_module('samplelib.' + m.name) for m in pkgutil.iter_modules(samplelib.__path__)];"
                "print(sys.prefix)")
        r = self._run("-c", code)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(os.path.normcase(r.stdout.strip()), os.path.normcase(PYDIR))


if __name__ == "__main__":
    unittest.main()
