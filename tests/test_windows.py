"""Windows/NTFS-only behavior. Skipped elsewhere; runs in CI on windows-latest.

Everything happens in temp folders. Nothing touches E:\\.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest

from samplelib import executor, inventory, winfs

IS_WIN = os.name == "nt"


def _sha(p):
    with open(winfs.long_path(p), "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _write(p, data):
    os.makedirs(winfs.long_path(os.path.dirname(p)), exist_ok=True)
    with open(winfs.long_path(p), "wb") as f:
        f.write(data)
    return p


def _lock(path, share):
    """Open with a Windows share mode via CreateFileW. share=0 denies everything."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    h = k32.CreateFileW(path, 0x80000000, share, None, 3, 0x80, None)  # GENERIC_READ, OPEN_EXISTING
    if h in (None, wintypes.HANDLE(-1).value):
        raise OSError(ctypes.get_last_error(), "CreateFileW failed")
    return h, k32


@unittest.skipUnless(IS_WIN, "Windows/NTFS only")
class WindowsNtfsTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="slo-win-")
        self.root = os.path.join(self.d, "Sample Packs")
        self.dest_root = os.path.join(self.root, "Samples")
        os.makedirs(self.dest_root)

    def tearDown(self):
        shutil.rmtree(winfs.long_path(self.d), ignore_errors=True)

    def _move_plan(self, src, dest, opid="o1"):
        st = os.stat(winfs.long_path(src))
        return {"plan_hash": "h" + opid, "root": self.root, "dest_root": self.dest_root,
                "ops": [{"id": opid, "action": "move", "item_id": "i" + opid, "source": src,
                         "dest": dest, "sha256": _sha(src), "size": st.st_size,
                         "mtime_ns": st.st_mtime_ns}]}

    def _run(self, plan):
        return executor.execute(plan, os.path.join(self.d, plan["plan_hash"] + ".jsonl"), None)

    def test_volume_is_ntfs_like(self):
        a = _write(os.path.join(self.d, "a.bin"), b"x")
        os.link(a, os.path.join(self.d, "b.bin"))
        self.assertEqual(os.stat(a).st_nlink, 2)

    def test_case_insensitive_collision_never_overwrites(self):
        existing = _write(os.path.join(self.dest_root, "One-Shots", "Drums", "kick.wav"), b"EXISTING")
        src = _write(os.path.join(self.root, "Pack", "Kick.wav"), b"INCOMING")
        res = self._run(self._move_plan(src, os.path.join(self.dest_root, "One-Shots", "Drums", "KICK.WAV")))
        self.assertEqual(res[0]["status"], "held", res)
        with open(existing, "rb") as f:
            self.assertEqual(f.read(), b"EXISTING")
        self.assertTrue(os.path.exists(src))

    def test_exclusively_locked_source_is_held(self):
        src = _write(os.path.join(self.root, "Pack", "Locked.wav"), b"LOCKED" * 100)
        dest = os.path.join(self.dest_root, "One-Shots", "Drums", "Locked.wav")
        plan = self._move_plan(src, dest)
        h, k32 = _lock(src, 0)
        try:
            res = self._run(plan)
        finally:
            k32.CloseHandle(h)
        self.assertEqual(res[0]["status"], "held", res)
        self.assertTrue(os.path.exists(src))
        self.assertFalse(os.path.exists(dest))

    def test_read_shared_lock_blocks_delete_and_rolls_back(self):
        # Like a DAW holding a sample open: reads allowed, delete denied.
        src = _write(os.path.join(self.root, "Pack", "Open.wav"), b"OPEN" * 100)
        dest = os.path.join(self.dest_root, "One-Shots", "Drums", "Open.wav")
        plan = self._move_plan(src, dest)
        h, k32 = _lock(src, 0x1)  # FILE_SHARE_READ only
        try:
            res = self._run(plan)
        finally:
            k32.CloseHandle(h)
        self.assertIn(res[0]["status"], ("held", "failed"), res)
        self.assertTrue(os.path.exists(src))
        self.assertEqual(_sha(src), plan["ops"][0]["sha256"])
        self.assertFalse(os.path.exists(dest), "rollback must remove the new destination link")

    def test_move_preserves_identity_and_link_shares_it(self):
        src = _write(os.path.join(self.root, "Pack A", "Kit", "Stem.wav"), b"STEM" * 50)
        dup = _write(os.path.join(self.root, "Pack B", "Kit", "Stem.wav"), b"STEM" * 50)
        ino = os.stat(src).st_ino
        d1 = os.path.join(self.dest_root, "Complete Kits", "A - Kit", "Stem.wav")
        d2 = os.path.join(self.dest_root, "Complete Kits", "B - Kit", "Stem.wav")
        plan = self._move_plan(src, d1)
        plan["ops"].append({"id": "o2", "action": "link", "item_id": "i2", "source": dup, "dest": d2,
                            "link_to": d1, "depends_on": "o1", "sha256": plan["ops"][0]["sha256"],
                            "size": plan["ops"][0]["size"], "mtime_ns": 0})
        res = self._run(plan)
        self.assertEqual([r["status"] for r in res], ["done", "done"], res)
        self.assertEqual(os.stat(d1).st_ino, ino, "move must keep the same NTFS file")
        self.assertEqual(os.stat(d2).st_ino, ino)
        self.assertEqual(os.stat(d1).st_nlink, 2)
        self.assertFalse(os.path.exists(src))
        self.assertTrue(os.path.exists(dup), "redundant source stays in place")
        out = subprocess.run(["fsutil", "hardlink", "list", d1], capture_output=True, text=True)
        if out.returncode == 0:
            self.assertEqual(len([l for l in out.stdout.splitlines() if l.strip()]), 2, out.stdout)

    def test_junctions_are_not_followed(self):
        outside = os.path.join(self.d, "Outside")
        _write(os.path.join(outside, "Secret.wav"), b"OUT")
        _write(os.path.join(self.root, "Pack", "Real.wav"), b"IN")
        _write(os.path.join(self.dest_root, "One-Shots", "Drums", "Lib.wav"), b"LIB")
        for name, target in (("Junction Out", outside), ("Junction To Samples", self.dest_root)):
            r = subprocess.run(["cmd", "/c", "mklink", "/J", os.path.join(self.root, name), target],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(winfs.is_reparse_point(os.path.join(self.root, "Junction Out")))
        res = inventory.scan(self.root, self.dest_root, lambda p: None)
        rels = {i["rel_path"]: i["origin"] for i in res["items"]}
        self.assertIn("Pack/Real.wav", rels)
        self.assertFalse(any("Secret" in r for r in rels), rels)
        self.assertFalse(any(r.startswith("Junction To Samples") for r in rels), rels)
        self.assertTrue(any("Junction" in s["path"] for s in res["skipped"]), res["skipped"])

    def test_scan_excludes_samples_case_insensitively(self):
        _write(os.path.join(self.root, "SAMPLES", "x.wav") if False else
               os.path.join(self.dest_root, "x.wav"), b"L")
        _write(os.path.join(self.root, "Pack", "y.wav"), b"I")
        res = inventory.scan(self.root, os.path.join(self.root, "samples"), lambda p: None)
        origin = {i["name"]: i["origin"] for i in res["items"]}
        self.assertEqual(origin, {"x.wav": "library", "y.wav": "incoming"})

    def test_long_paths_over_260_chars(self):
        deep = os.path.join(self.root, "Pack", *(["Very Long Folder Name For Testing"] * 8))
        src = _write(os.path.join(deep, "Deep Sample.wav"), b"DEEP")
        self.assertGreater(len(src), 260)
        res = inventory.scan(self.root, self.dest_root, lambda p: None)
        it = [i for i in res["items"] if i["name"] == "Deep Sample.wav"][0]
        self.assertEqual(it["sha256"], hashlib.sha256(b"DEEP").hexdigest())
        dest = os.path.join(self.dest_root, "Complete Kits", "P - K", *(["Another Long Folder Name"] * 8), "Deep Sample.wav")
        r = self._run(self._move_plan(src, dest))
        self.assertEqual(r[0]["status"], "done", r)
        self.assertTrue(os.path.exists(winfs.long_path(dest)))

    def test_reveal_does_not_raise(self):
        # Launches explorer; only checks we build a valid command without error.
        p = _write(os.path.join(self.root, "Pack, With Comma", "a b.wav"), b"x")
        winfs.reveal_in_explorer(p)


if __name__ == "__main__":
    unittest.main()
