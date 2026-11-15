"""Filesystem layer tests: winfs, inventory, executor. Synthetic temp files only."""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import threading
import unittest

from samplelib import executor, inventory, models, winfs


def _write(path: str, data: bytes) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot(top: str) -> dict:
    snap = {}
    for dirpath, dirnames, filenames in os.walk(top):
        for n in dirnames + filenames:
            p = os.path.join(dirpath, n)
            st = os.lstat(p)
            snap[os.path.relpath(p, top)] = (st.st_mode, st.st_size, st.st_mtime_ns)
    snap["."] = os.lstat(top).st_mtime_ns
    return snap


class _Crash(BaseException):
    """Simulated process death (not caught by the executor)."""


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="samplelib-fs-")
        self.root = os.path.join(self.tmp, "Sample Packs")
        self.dest_root = os.path.join(self.root, models.DEST_DIRNAME)
        os.makedirs(self.root)
        self.journal = os.path.join(self.tmp, "data", "journal.jsonl")
        self._patches = []

    def tearDown(self):
        for obj, name, old in reversed(self._patches):
            setattr(obj, name, old)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def patch(self, obj, name, new):
        self._patches.append((obj, name, getattr(obj, name)))
        setattr(obj, name, new)

    # -- plan helpers ---------------------------------------------------
    def move_op(self, src: str, dest: str, **kw) -> dict:
        st = os.stat(src)
        with open(src, "rb") as f:
            sha = _sha(f.read())
        iid = models.item_id("incoming", os.path.relpath(src, self.root).replace(os.sep, "/"))
        op = {"id": models.op_id("move", iid, dest), "action": "move", "item_id": iid,
              "source": src, "dest": dest, "link_to": None, "depends_on": None,
              "sha256": sha, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
              "reason": "test", "flags": []}
        op.update(kw)
        return op

    def link_op(self, src: str, link_to: str, dest: str, depends_on=None, sha=None) -> dict:
        st = os.stat(src)
        with open(src, "rb") as f:
            data = f.read()
        iid = models.item_id("incoming", os.path.relpath(src, self.root).replace(os.sep, "/"))
        return {"id": models.op_id("link", iid, dest), "action": "link", "item_id": iid,
                "source": src, "dest": dest, "link_to": link_to, "depends_on": depends_on,
                "sha256": sha or _sha(data), "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                "reason": "test", "flags": ["duplicate"]}

    def plan(self, ops: list) -> dict:
        return {"plan_hash": models.plan_hash(ops), "created": "", "root": self.root,
                "dest_root": self.dest_root, "ops": ops, "summary": {}}

    def run_plan(self, ops: list) -> dict:
        res = executor.execute(self.plan(ops), self.journal, lambda d: None)
        return {r["op_id"]: r for r in res}


# ---------------------------------------------------------------------------
class WinfsTests(TempCase):
    def test_path_key_case_insensitive(self):
        a = os.path.join(self.root, "Kick.WAV")
        b = os.path.join(self.root, "kick.wav")
        self.assertEqual(winfs.path_key(a), winfs.path_key(b))
        self.assertEqual(winfs.path_key(self.root + os.sep), winfs.path_key(self.root))

    def test_is_within(self):
        self.assertTrue(winfs.is_within(os.path.join(self.root, "A", "b.wav"), self.root))
        self.assertTrue(winfs.is_within(self.root.upper() if os.name == "nt" else self.root,
                                        self.root))
        self.assertTrue(winfs.is_within(os.path.join(self.root, "SAMPLES", "x"),
                                        self.dest_root))
        self.assertFalse(winfs.is_within(self.root + "2", self.root))
        self.assertFalse(winfs.is_within(self.tmp, self.root))

    def test_identity_and_volume(self):
        p = _write(os.path.join(self.root, "a.wav"), b"x")
        self.assertIsNone(winfs.file_identity(os.path.join(self.root, "nope")))
        self.assertIsNotNone(winfs.file_identity(p))
        self.assertTrue(winfs.same_volume(p, os.path.join(self.dest_root, "not", "yet")))

    def test_long_path_identity_off_windows(self):
        if os.name == "nt":
            lp = winfs.long_path("C:\\" + "a" * 300)
            self.assertTrue(lp.startswith("\\\\?\\"))
        else:
            self.assertEqual(winfs.long_path("/x/" + "a" * 300), "/x/" + "a" * 300)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_reparse_detection(self):
        d = os.path.join(self.tmp, "target")
        os.makedirs(d)
        link = os.path.join(self.root, "lnk")
        try:
            os.symlink(d, link, target_is_directory=True)
        except OSError:
            self.skipTest("cannot create symlink")
        self.assertTrue(winfs.is_reparse_point(link))
        self.assertFalse(winfs.is_reparse_point(d))
        with os.scandir(self.root) as it:
            entries = {e.name: e for e in it}
        self.assertTrue(winfs.is_reparse_point(entries["lnk"]))


# ---------------------------------------------------------------------------
class ScanTests(TempCase):
    def _populate(self):
        _write(os.path.join(self.root, "PackA", "Drums", "Kick.wav"), b"kick" * 100)
        _write(os.path.join(self.root, "PackA", "Drums", "Snare.aif"), b"snare")
        _write(os.path.join(self.root, "PackB", "readme.txt"), b"hello")
        _write(os.path.join(self.root, "loose.mp3"), b"mp3data")
        _write(os.path.join(self.dest_root, "One-Shots", "Drums", "Old.wav"), b"old")
        _write(os.path.join(self.dest_root, "Catalog", "Sample_Catalog.csv"), b"a,b")

    def test_scan_is_read_only_and_fields(self):
        self._populate()
        before = _snapshot(self.root)
        events = []
        out = inventory.scan(self.root, self.dest_root, events.append)
        self.assertEqual(before, _snapshot(self.root))
        self.assertFalse(out["cancelled"])
        by_rel = {i["rel_path"]: i for i in out["items"]}
        kick = by_rel["PackA/Drums/Kick.wav"]
        self.assertEqual(kick["origin"], "incoming")
        self.assertEqual(kick["pack"], "PackA")
        self.assertEqual(kick["sha256"], _sha(b"kick" * 100))
        self.assertEqual(kick["preview"], "native")
        self.assertEqual(kick["kind"], "audio")
        self.assertEqual(kick["id"], models.item_id("incoming", "PackA/Drums/Kick.wav"))
        self.assertEqual(by_rel["PackA/Drums/Snare.aif"]["preview"], "unsupported")
        self.assertEqual(by_rel["PackB/readme.txt"]["preview"], "none")
        self.assertIsNone(by_rel["loose.mp3"]["pack"])
        phases = {e["phase"] for e in events}
        self.assertTrue({"walking", "hashing"} <= phases)
        self.assertEqual(events[-1]["done"], events[-1]["total"])

    def test_dest_excluded_from_incoming_library_scanned_catalog_excluded(self):
        self._populate()
        out = inventory.scan(self.root, self.dest_root, None)
        inc = [i for i in out["items"] if i["origin"] == "incoming"]
        lib = [i for i in out["items"] if i["origin"] == "library"]
        self.assertFalse(any(winfs.is_within(i["abs_path"], self.dest_root) for i in inc))
        self.assertEqual([i["rel_path"] for i in lib], ["Samples/One-Shots/Drums/Old.wav"])
        self.assertIsNone(lib[0]["pack"])

    def test_dest_exclusion_case_insensitive(self):
        self._populate()
        out = inventory.scan(self.root, os.path.join(self.root, "SAMPLES"), None)
        inc = [i for i in out["items"] if i["origin"] == "incoming"]
        self.assertFalse(any("Old.wav" in i["abs_path"] for i in inc))

    def test_hash_cache_reused(self):
        self._populate()
        first = inventory.scan(self.root, self.dest_root, None)
        calls = []
        orig = inventory.sha256_file
        self.patch(inventory, "sha256_file",
                   lambda p, *a, **k: (calls.append(p), orig(p, *a, **k))[1])
        second = inventory.scan(self.root, self.dest_root, None,
                                hash_cache=first["hash_cache"])
        self.assertEqual(calls, [])
        self.assertEqual({i["sha256"] for i in first["items"]},
                         {i["sha256"] for i in second["items"]})
        # a changed file (new mtime) is re-hashed
        p = os.path.join(self.root, "loose.mp3")
        st = os.stat(p)
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000_000))
        inventory.scan(self.root, self.dest_root, None, hash_cache=first["hash_cache"])
        self.assertEqual(calls, [p])

    def test_unreadable_file_recorded_not_fatal(self):
        self._populate()
        bad = os.path.join(self.root, "PackA", "Drums", "Kick.wav")
        orig = inventory.sha256_file

        def flaky(p, *a, **k):
            if p == bad:
                raise PermissionError(13, "Permission denied", p)
            return orig(p, *a, **k)
        self.patch(inventory, "sha256_file", flaky)
        events = []
        out = inventory.scan(self.root, self.dest_root, events.append)
        item = next(i for i in out["items"] if i["abs_path"] == bad)
        self.assertIsNone(item["sha256"])
        self.assertIn("permission denied", item["hash_error"])
        self.assertTrue(item["warnings"])
        self.assertEqual(events[-1]["errors"][0]["path"], bad)

    def test_cancel(self):
        self._populate()
        ev = threading.Event()
        ev.set()
        out = inventory.scan(self.root, self.dest_root, None, cancel=ev)
        self.assertTrue(out["cancelled"])

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_reparse_points_not_followed(self):
        self._populate()
        outside = os.path.join(self.tmp, "outside")
        _write(os.path.join(outside, "secret.wav"), b"zzz")
        try:
            os.symlink(outside, os.path.join(self.root, "PackA", "linkdir"),
                       target_is_directory=True)
            os.symlink(os.path.join(outside, "secret.wav"),
                       os.path.join(self.root, "PackB", "linkfile.wav"))
        except OSError:
            self.skipTest("cannot create symlink")
        out = inventory.scan(self.root, self.dest_root, None)
        names = {i["name"] for i in out["items"]}
        self.assertNotIn("secret.wav", names)
        self.assertNotIn("linkfile.wav", names)
        reasons = [s for s in out["skipped"] if "reparse" in s["reason"]]
        self.assertEqual(len(reasons), 2)


# ---------------------------------------------------------------------------
class ExecutorTests(TempCase):
    def test_move_verifies_and_removes_source(self):
        src = _write(os.path.join(self.root, "PackA", "Kick.wav"), b"kick-bytes")
        ident = winfs.file_identity(src)
        dest = os.path.join(self.dest_root, "One-Shots", "Drums", "Kick.wav")
        op = self.move_op(src, dest)
        r = self.run_plan([op])[op["id"]]
        self.assertEqual(r["status"], "done", r)
        self.assertFalse(os.path.exists(src))
        self.assertEqual(winfs.file_identity(dest), ident)  # same file, not a copy
        self.assertEqual(r["file_id"], ident)
        self.assertTrue(r["timestamp"])
        phases = [(j["op_id"], j["phase"]) for j in executor.read_journal(self.journal)]
        self.assertEqual(phases, [(op["id"], "start"), (op["id"], "done")])

    def test_no_overwrite_existing_and_case_variant(self):
        src = _write(os.path.join(self.root, "PackA", "Kick.wav"), b"new")
        existing = _write(os.path.join(self.dest_root, "One-Shots", "kick.WAV"), b"old")
        for dest in (existing, os.path.join(self.dest_root, "One-Shots", "Kick.wav")):
            op = self.move_op(src, dest)
            r = self.run_plan([op])[op["id"]]
            self.assertEqual(r["status"], "held")
            self.assertIn("exists", r["error"])
            self.assertTrue(os.path.exists(src))
            with open(existing, "rb") as f:
                self.assertEqual(f.read(), b"old")
        self.assertEqual(winfs.path_key(existing),
                         winfs.path_key(os.path.join(self.dest_root, "ONE-SHOTS", "KICK.wav")))

    def test_link_after_move_shares_identity(self):
        a = _write(os.path.join(self.root, "PackA", "Snare.wav"), b"same")
        b = _write(os.path.join(self.root, "PackB", "Kit", "Snare.wav"), b"same")
        canon = os.path.join(self.dest_root, "One-Shots", "Drums", "Snare.wav")
        kitdest = os.path.join(self.dest_root, "Complete Kits", "Kit", "Snare.wav")
        mv = self.move_op(a, canon)
        ln = self.link_op(b, canon, kitdest, depends_on=mv["id"])
        res = self.run_plan([ln, mv])  # link listed first: executor must reorder
        self.assertEqual(res[mv["id"]]["status"], "done")
        self.assertEqual(res[ln["id"]]["status"], "done", res[ln["id"]])
        self.assertEqual(os.stat(canon).st_ino, os.stat(kitdest).st_ino)
        self.assertEqual(os.stat(canon).st_nlink, 2)
        self.assertTrue(os.path.exists(b))  # link never touches the duplicate source

    def test_changed_after_approval_is_held(self):
        src = _write(os.path.join(self.root, "PackA", "Kick.wav"), b"AAAA")
        op = self.move_op(src, os.path.join(self.dest_root, "One-Shots", "Kick.wav"))
        st = os.stat(src)
        with open(src, "r+b") as f:
            f.write(b"BBBB")
        os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns))  # same size and mtime
        r = self.run_plan([op])[op["id"]]
        self.assertEqual(r["status"], "held")
        self.assertIn("SHA-256", r["error"])
        self.assertTrue(os.path.exists(src))
        self.assertFalse(os.path.exists(op["dest"]))

    def test_size_change_held(self):
        src = _write(os.path.join(self.root, "PackA", "Kick.wav"), b"AAAA")
        op = self.move_op(src, os.path.join(self.dest_root, "One-Shots", "Kick.wav"))
        with open(src, "ab") as f:
            f.write(b"more")
        r = self.run_plan([op])[op["id"]]
        self.assertEqual(r["status"], "held")
        self.assertIn("size", r["error"])

    def test_locked_source_rolls_back(self):
        src = _write(os.path.join(self.root, "PackA", "Kick.wav"), b"locked")
        dest = os.path.join(self.dest_root, "One-Shots", "Kick.wav")
        op = self.move_op(src, dest)
        real_unlink = executor._unlink

        def locked(p):
            if p == src:
                e = PermissionError(13, "The process cannot access the file", p)
                e.winerror = 32
                raise e
            real_unlink(p)
        self.patch(executor, "_unlink", locked)
        r = self.run_plan([op])[op["id"]]
        self.assertEqual(r["status"], "held")
        self.assertIn("source locked", r["error"])
        self.assertTrue(os.path.exists(src))
        self.assertFalse(os.path.exists(dest))
        self.assertEqual(os.stat(src).st_nlink, 1)

    def test_interrupted_run_resumes_without_repeating(self):
        srcs = [_write(os.path.join(self.root, "P", f"s{i}.wav"), f"data{i}".encode())
                for i in range(4)]
        ops = [self.move_op(s, os.path.join(self.dest_root, "Loops", os.path.basename(s)))
               for s in srcs]
        real_unlink = executor._unlink
        count = [0]

        def crashing(p):
            count[0] += 1
            if count[0] == 3:  # die after linking op #3 but before unlinking its source
                raise _Crash()
            real_unlink(p)
        self.patch(executor, "_unlink", crashing)
        with self.assertRaises(_Crash):
            executor.execute(self.plan(ops), self.journal, None)
        # state after crash: ops 0,1 done; op 2 linked at dest, source still present
        self.assertTrue(os.path.exists(srcs[2]) and os.path.exists(ops[2]["dest"]))
        self.patch(executor, "_unlink", real_unlink)
        links = []
        real_link = executor._link
        self.patch(executor, "_link", lambda a, b: (links.append(b), real_link(a, b))[1])
        res = self.run_plan(ops)
        self.assertEqual([res[o["id"]]["status"] for o in ops],
                         ["already-done", "already-done", "done", "done"])
        self.assertEqual(links, [ops[3]["dest"]])  # completed ops not repeated
        for s, o in zip(srcs, ops):
            self.assertFalse(os.path.exists(s))
            self.assertTrue(os.path.exists(o["dest"]))
        # a third run is a no-op
        res = self.run_plan(ops)
        self.assertEqual({r["status"] for r in res.values()}, {"already-done"})

    def test_depends_on_held_when_canonical_failed(self):
        a = _write(os.path.join(self.root, "PackA", "Hat.wav"), b"hat")
        b = _write(os.path.join(self.root, "PackB", "Kit", "Hat.wav"), b"hat")
        canon = os.path.join(self.dest_root, "One-Shots", "Hat.wav")
        mv = self.move_op(a, canon)
        ln = self.link_op(b, canon, os.path.join(self.dest_root, "Complete Kits", "K",
                                                 "Hat.wav"), depends_on=mv["id"])
        os.unlink(a)  # canonical source vanished -> move held
        res = self.run_plan([mv, ln])
        self.assertEqual(res[mv["id"]]["status"], "held")
        self.assertEqual(res[ln["id"]]["status"], "held")
        self.assertIn("depends on", res[ln["id"]]["error"])
        self.assertFalse(os.path.exists(ln["dest"]))

    def test_non_write_actions(self):
        src = _write(os.path.join(self.root, "PackA", "x.wav"), b"x")
        before = _snapshot(self.root)
        ops = []
        for act in ("keep", "hold", "exclude"):
            op = self.move_op(src, None, action=act)
            op["id"] = models.op_id(act, op["item_id"], None)
            ops.append(op)
        res = self.run_plan(ops)
        self.assertEqual([res[o["id"]]["status"] for o in ops], ["skipped", "held", "skipped"])
        self.assertEqual(before, _snapshot(self.root))

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_reparse_source_and_dest_ancestor_refused(self):
        real = _write(os.path.join(self.tmp, "elsewhere", "r.wav"), b"real")
        lnk = os.path.join(self.root, "PackA", "r.wav")
        os.makedirs(os.path.dirname(lnk))
        try:
            os.symlink(real, lnk)
        except OSError:
            self.skipTest("cannot create symlink")
        op = self.move_op(lnk, os.path.join(self.dest_root, "One-Shots", "r.wav"))
        r = self.run_plan([op])[op["id"]]
        self.assertEqual(r["status"], "held")
        self.assertIn("reparse", r["error"])
        self.assertTrue(os.path.islink(lnk))
        # destination folder that is a symlink/junction to elsewhere
        src = _write(os.path.join(self.root, "PackA", "k.wav"), b"kk")
        outside = os.path.join(self.tmp, "outside_dir")
        os.makedirs(outside)
        os.makedirs(self.dest_root, exist_ok=True)
        os.symlink(outside, os.path.join(self.dest_root, "Loops"), target_is_directory=True)
        op = self.move_op(src, os.path.join(self.dest_root, "Loops", "Drum Loops", "k.wav"))
        r = self.run_plan([op])[op["id"]]
        self.assertEqual(r["status"], "held")
        self.assertIn("reparse", r["error"])
        self.assertEqual(os.listdir(outside), [])
        self.assertTrue(os.path.exists(src))

    def test_hardlink_unsupported_falls_back_to_staged_copy(self):
        src = _write(os.path.join(self.root, "PackA", "fat.wav"), b"fat32" * 50)
        dest = os.path.join(self.dest_root, "One-Shots", "fat.wav")
        op = self.move_op(src, dest)
        import errno

        def nolink(a, b):
            raise OSError(errno.EPERM, "Operation not permitted")
        self.patch(executor, "_link", nolink)
        r = self.run_plan([op])[op["id"]]
        self.assertEqual(r["status"], "done", r)
        self.assertFalse(os.path.exists(src))
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), b"fat32" * 50)
        self.assertEqual(os.listdir(os.path.dirname(dest)), ["fat.wav"])  # no .partial left

    def test_destination_outside_library_refused(self):
        src = _write(os.path.join(self.root, "PackA", "a.wav"), b"a")
        op = self.move_op(src, os.path.join(self.tmp, "elsewhere.wav"))
        r = self.run_plan([op])[op["id"]]
        self.assertEqual(r["status"], "held")
        self.assertTrue(os.path.exists(src))


if __name__ == "__main__":
    unittest.main()
