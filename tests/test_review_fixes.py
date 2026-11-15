"""Regression tests for findings from the read-only safety review."""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import time
import unittest

from samplelib import executor, models
from tests.test_e2e import Env


def _sha(p):
    import hashlib
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class RootSwitchTest(unittest.TestCase):
    """Finding 1: a root change must never mix collections or bypass the job lock."""

    def setUp(self):
        self.env = Env()
        self.other = tempfile.mkdtemp(prefix="slo-other-")
        os.makedirs(os.path.join(self.other, "Pack B"))
        with open(os.path.join(self.other, "Pack B", "Kick B.wav"), "wb") as f:
            f.write(b"RIFF" + b"\0" * 64)

    def tearDown(self):
        self.env.close()
        shutil.rmtree(self.other, ignore_errors=True)

    def test_scan_request_with_new_root_refused_while_job_runs(self):
        env = self.env
        gate = threading.Event()
        orig = env.app.job_plan

        def slow_plan():
            gate.wait(10)
            orig()
        env.scan()
        env.app.job_plan = slow_plan
        env.post("/api/plan/build", {})
        code, j = env.post("/api/scan", {"root": self.other}, expect=None)
        self.assertEqual(code, 409, j)
        self.assertEqual(os.path.normcase(env.store.root), os.path.normcase(env.root))
        gate.set()
        env.wait()

    def test_root_switch_clears_plan_and_approval(self):
        env = self.env
        env.scan()
        p = env.build_plan()
        env.approve(p["plan_hash"])
        env.post("/api/scan", {"root": self.other})
        env.wait()
        st = env.get("/api/state")
        self.assertIsNone(st["plan"])
        rows = env.all_samples()
        self.assertTrue(rows)
        self.assertTrue(all(r["abs_path"].startswith(self.other) for r in rows))
        code, _ = env.post("/api/execute", {"plan_hash": p["plan_hash"]}, expect=None)
        self.assertEqual(code, 409)


class JournalTornLineTest(unittest.TestCase):
    """Finding 2: a torn final line must not swallow the next record."""

    def test_torn_line_is_terminated(self):
        d = tempfile.mkdtemp()
        try:
            jp = os.path.join(d, "j.jsonl")
            with open(jp, "w", encoding="utf-8") as f:
                f.write('{"op_id": "o1", "phase": "done", "ts": "x"}\n{"op_id": "o2", "pha')
            j = executor._Journal(jp)
            j.write("o3", "start")
            j.close()
            recs = executor.read_journal(jp)
            self.assertEqual([r["op_id"] for r in recs], ["o1", "o3"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class SourceLocationTest(unittest.TestCase):
    """Finding 3: sources outside root or below a reparse point are held."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.root = os.path.join(self.d, "root")
        self.dest_root = os.path.join(self.root, "Samples")
        self.outside = os.path.join(self.d, "outside")
        os.makedirs(self.root)
        os.makedirs(self.outside)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _plan(self, src):
        st = os.stat(src)
        op = {"id": "o1", "action": "move", "item_id": "i1", "source": src,
              "dest": os.path.join(self.dest_root, "One-Shots", "Drums", "x.wav"),
              "sha256": _sha(src), "size": st.st_size, "mtime_ns": st.st_mtime_ns}
        return {"plan_hash": "h", "root": self.root, "dest_root": self.dest_root, "ops": [op]}

    def test_source_outside_root_held(self):
        src = os.path.join(self.outside, "x.wav")
        with open(src, "wb") as f:
            f.write(b"data")
        res = executor.execute(self._plan(src), os.path.join(self.d, "j"), None)
        self.assertEqual(res[0]["status"], "held")
        self.assertTrue(os.path.exists(src))

    @unittest.skipUnless(hasattr(os, "symlink"), "needs symlinks")
    def test_source_below_symlinked_folder_held(self):
        real = os.path.join(self.outside, "pack")
        os.makedirs(real)
        with open(os.path.join(real, "x.wav"), "wb") as f:
            f.write(b"data")
        try:
            os.symlink(real, os.path.join(self.root, "Pack"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("cannot create symlink here")
        src = os.path.join(self.root, "Pack", "x.wav")
        res = executor.execute(self._plan(src), os.path.join(self.d, "j"), None)
        self.assertEqual(res[0]["status"], "held")
        self.assertIn("reparse", res[0]["error"])
        self.assertTrue(os.path.exists(os.path.join(real, "x.wav")))


class StagingFileTest(unittest.TestCase):
    """Finding 6: a pre-existing staging file is never deleted by a fresh attempt."""

    def test_foreign_partial_not_deleted(self):
        d = tempfile.mkdtemp()
        try:
            root = os.path.join(d, "root")
            src = os.path.join(root, "P", "x.wav")
            os.makedirs(os.path.dirname(src))
            with open(src, "wb") as f:
                f.write(b"abc")
            dest = os.path.join(root, "Samples", "x.wav")
            os.makedirs(os.path.dirname(dest))
            partial = executor._partial_path(dest, "o1")
            with open(partial, "wb") as f:
                f.write(b"someone else's file")
            op = {"id": "o1", "source": src, "dest": dest, "sha256": _sha(src)}
            with self.assertRaises(executor._Held):
                executor._staged_move(op, None)
            self.assertTrue(os.path.exists(partial))
            self.assertTrue(os.path.exists(src))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class EditLockDuringExportTest(unittest.TestCase):
    """Finding 4: every mutating endpoint is refused for the whole approved run."""

    def test_mutations_refused_while_executing_flag_set(self):
        env = Env()
        try:
            env.scan()
            sid = env.all_samples()[0]["id"]
            env.app.executing = True
            for path, body in (("/api/items/edit", {"ids": [sid], "changes": {"genre": "x"}}),
                               ("/api/kits/create", {"name": "K", "ids": [sid]}),
                               ("/api/plan/build", {}), ("/api/scan", {})):
                code, _ = env.post(path, body, expect=None)
                self.assertEqual(code, 409, path)
            env.app.executing = False
        finally:
            env.close()


class EditOrderTest(unittest.TestCase):
    """Finding 5: approval is invalidated before edits are persisted."""

    def test_crash_while_saving_edits_leaves_no_approval(self):
        env = Env()
        try:
            env.scan()
            p = env.build_plan()
            env.approve(p["plan_hash"])
            sid = env.all_samples()[0]["id"]

            def boom():
                raise OSError("disk full")
            env.store.save_edits = boom
            code, _ = env.post("/api/items/edit", {"ids": [sid], "changes": {"genre": "x"}}, expect=None)
            self.assertNotEqual(code, 200)
            self.assertFalse(os.path.exists(os.path.join(env.data, "approval.json")))
            self.assertIsNone(env.store.approved_hash)
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
