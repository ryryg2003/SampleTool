"""Undo a completed run: round trip, holds, resume, approval, executor scoping.

Every test uses a disposable synthetic fixture (tools/make_fixtures.py) in a
temp folder via tests.test_e2e.Env, or plain temp files for executor tests.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import unittest

from samplelib import executor, models
from tests.test_e2e import Env, key, sha256_file


class Crash(BaseException):
    """Simulated process death (not caught by the executor)."""


def files_outside(root: str, dest_root: str) -> dict:
    out = {}
    for dp, dn, fn in os.walk(root):
        for n in fn:
            p = os.path.join(dp, n)
            if key(p).startswith(key(dest_root) + os.sep.casefold()) or key(p) == key(dest_root):
                continue
            out[key(p)] = sha256_file(p)
    return out


def files_inside(dest_root: str) -> dict:
    out = {}
    cat = key(os.path.join(dest_root, models.CATALOG_DIR))
    for dp, dn, fn in os.walk(dest_root):
        if key(dp).startswith(cat):
            continue
        for n in fn:
            p = os.path.join(dp, n)
            out[key(p)] = sha256_file(p)
    return out


class UndoCase(unittest.TestCase):
    def setUp(self):
        self.env = Env()
        self.addCleanup(self.env.close)
        self._restore = []
        self.addCleanup(self._unpatch)

    def _unpatch(self):
        for obj, name, old in reversed(self._restore):
            setattr(obj, name, old)

    def patch(self, obj, name, new):
        self._restore.append((obj, name, getattr(obj, name)))
        setattr(obj, name, new)

    def forward(self):
        env = self.env
        env.scan()
        self.before_out = files_outside(env.root, env.dest_root)
        self.before_in = files_inside(env.dest_root)
        plan = env.build_plan()
        env.approve(plan["plan_hash"])
        env.execute(plan["plan_hash"])
        res = {r["op_id"]: r for r in env.log()}
        self.assertTrue(any(r["status"] == "done" for r in res.values()))
        return plan, res

    def build_undo(self, run_id=None):
        body = {"run_id": run_id} if run_id else {}
        self.env.post("/api/undo/build", body)
        self.env.wait()
        return self.env.plan()

    def ops_of(self, plan, action):
        return [o for o in plan["ops"] if o["action"] == action]


class RoundTripTest(UndoCase):
    def test_run_then_undo_restores_everything(self):
        env = self.env
        plan, res = self.forward()
        fwd_links = [o for o in plan["ops"] if o["action"] == "link" and res[o["id"]]["status"] == "done"]
        fwd_moves = [o for o in plan["ops"] if o["action"] == "move" and res[o["id"]]["status"] == "done"]
        self.assertTrue(fwd_links, "fixture should produce at least one kit link")
        runs = env.get("/api/runs")
        self.assertEqual(len(runs["runs"]), 1)
        r0 = runs["runs"][0]
        self.assertTrue(r0["undoable"] and r0["is_current"], r0)
        self.assertEqual(r0["moved"], len(fwd_moves))
        self.assertEqual(r0["linked"], len(fwd_links))

        up = self.build_undo(r0["run_id"])
        st = env.get("/api/state")
        self.assertEqual(st["plan"]["summary"]["kind"], "undo")
        self.assertEqual(st["undo"]["plan_undo_of"], r0["run_id"])
        self.assertFalse(st["plan"]["approved"])
        self.assertEqual(self.ops_of(up, "hold"), [], [o["reason"] for o in self.ops_of(up, "hold")])
        self.assertEqual(len(self.ops_of(up, "move")), len(fwd_moves))
        self.assertEqual(len(self.ops_of(up, "unlink")), len(fwd_links))
        # unlinks are listed (and run) before moves back
        acts = [o["action"] for o in up["ops"]]
        self.assertLess(max(i for i, a in enumerate(acts) if a == "unlink"),
                        min(i for i, a in enumerate(acts) if a == "move"))
        self.assertEqual(up["plan_hash"], models.plan_hash(env.store.plan["ops"]))

        env.approve(up["plan_hash"])
        env.execute(up["plan_hash"])
        log = env.log()
        self.assertTrue(all(r["status"] in ("done", "already-done", "skipped") for r in log),
                        [r for r in log if r["status"] not in ("done", "already-done", "skipped")])
        # every original path restored with the same SHA-256; nothing lost
        self.assertEqual(files_outside(env.root, env.dest_root), self.before_out)
        # links and moved files gone from Samples; existing library files untouched
        self.assertEqual(files_inside(env.dest_root), self.before_in)
        for o in fwd_links:
            self.assertFalse(os.path.lexists(o["dest"]), o["dest"])
        for o in fwd_moves:
            self.assertEqual(os.stat(o["source"]).st_nlink, 1, o["source"])
        # report, run marked undone, rescan prompt
        self.assertTrue(os.path.isfile(os.path.join(env.dest_root, "Catalog", "Undo_Report.csv")))
        st = env.get("/api/state")
        self.assertTrue(st["undo"]["rescan_needed"])
        self.assertIn("Undo_Report.csv", st["execution"]["reports"])
        r0 = env.get("/api/runs")["runs"][0]
        self.assertEqual(r0["undo"]["status"], "undone")
        self.assertFalse(r0["undoable"])
        code, j = env.post("/api/undo/build", {"run_id": r0["run_id"]}, expect=None)
        self.assertEqual(code, 409, j)
        # the forward catalog reports were not rewritten by the undo
        env.scan()
        self.assertFalse(env.get("/api/state")["undo"]["rescan_needed"])

    def test_history_survives_rescan_restart_and_backfills_old_data(self):
        env = self.env
        self.forward()
        rid = env.get("/api/runs")["runs"][0]["run_id"]
        env.scan()  # deletes execution.json.gz
        self.assertIsNone(env.get("/api/state")["execution"])
        env.stop()
        env.start()
        runs = env.get("/api/runs")["runs"]
        self.assertEqual([r["run_id"] for r in runs], [rid])
        self.assertTrue(runs[0]["undoable"])
        # data from the previous version: no runs/ folder; rebuilt from the journal
        env.stop()
        shutil.rmtree(os.path.join(env.data, "runs"))
        env.start()
        runs = env.get("/api/runs")["runs"]
        self.assertEqual([r["run_id"] for r in runs], [rid])
        up = self.build_undo()
        self.assertTrue(self.ops_of(up, "move"))


class HoldTest(UndoCase):
    def test_changed_and_occupied_are_held(self):
        env = self.env
        plan, res = self.forward()
        moves = [o for o in plan["ops"] if o["action"] == "move" and res[o["id"]]["status"] == "done"
                 and not o.get("kit_id")]
        changed, occupied, late = moves[0], moves[1], moves[2]
        with open(changed["dest"], "ab") as f:
            f.write(b"edited in a DAW")
        changed_sha = sha256_file(changed["dest"])
        with open(occupied["source"], "wb") as f:
            f.write(b"a new file at the old path")
        up = self.build_undo()
        ops = {o["undo_of_op"]: o for o in up["ops"]}
        self.assertEqual(ops[changed["id"]]["action"], "hold")
        self.assertIn("changed since the run", ops[changed["id"]]["reason"])
        self.assertEqual(ops[occupied["id"]]["action"], "hold")
        self.assertIn("original location is occupied", ops[occupied["id"]]["reason"])
        self.assertEqual(ops[late["id"]]["action"], "move")
        env.approve(up["plan_hash"])
        # changed after approval: revalidated at run time and held, not moved
        st = os.stat(late["dest"])
        with open(late["dest"], "r+b") as f:
            f.seek(st.st_size - 1)
            b = f.read(1)
            f.seek(st.st_size - 1)
            f.write(bytes([b[0] ^ 0xFF]))
        os.utime(late["dest"], ns=(st.st_atime_ns, st.st_mtime_ns))
        env.execute(up["plan_hash"])
        log = {r["op_id"]: r for r in env.log()}
        self.assertEqual(log[ops[late["id"]]["id"]]["status"], "held")
        self.assertTrue(os.path.isfile(late["dest"]) and not os.path.exists(late["source"]))
        self.assertEqual(sha256_file(changed["dest"]), changed_sha)
        self.assertFalse(os.path.exists(changed["source"]))
        with open(occupied["source"], "rb") as f:
            self.assertEqual(f.read(), b"a new file at the old path")
        self.assertEqual(sha256_file(occupied["dest"]), occupied["sha256"])
        r0 = env.get("/api/runs")["runs"][0]
        self.assertEqual(r0["undo"]["status"], "partly-undone")
        self.assertTrue(r0["undoable"], "a partly undone run can be undone again")
        # rebuilding marks completed moves as already back
        up2 = self.build_undo()
        kept = [o for o in up2["ops"] if o["action"] == "keep"]
        self.assertTrue(kept and all("Already back" in o["reason"] or "already removed" in o["reason"]
                                     for o in kept))


class ResumeTest(UndoCase):
    def test_crash_mid_undo_resumes_without_repeating(self):
        env = self.env
        self.forward()
        up = self.build_undo()
        h = up["plan_hash"]
        env.approve(h)
        moves = self.ops_of(up, "move")
        victim = moves[3]
        orig_link, orig_unlink = executor._link, executor._unlink
        run1, run2 = [], []

        def link1(s, d):
            run1.append(key(d))
            return orig_link(s, d)

        def crash_unlink(p):
            if key(p.replace("\\\\?\\", "")) == key(victim["source"]):
                raise Crash()
            return orig_unlink(p)

        self.patch(executor, "_link", link1)
        self.patch(executor, "_unlink", crash_unlink)
        with env.store.lock:
            plan = env.store.plan
        with self.assertRaises(Crash):
            executor.execute(plan, env.store.journal_path(h), None, None)
        self.assertTrue(os.path.exists(victim["source"]) and os.path.exists(victim["dest"]))

        def link2(s, d):
            run2.append(key(d))
            return orig_link(s, d)

        executor._link = link2
        executor._unlink = orig_unlink
        env.execute(h)
        log = {r["op_id"]: r for r in env.log()}
        self.assertFalse(set(run1) & set(run2), "completed undo operations were repeated")
        self.assertIn(log[victim["id"]]["status"], ("done", "already-done"))
        self.assertTrue(all(log[o["id"]]["status"] in ("done", "already-done") for o in up["ops"]
                            if o["action"] in ("move", "unlink")))
        done_counts: dict = {}
        for rec in executor.read_journal(env.store.journal_path(h)):
            if rec.get("phase") == "done":
                done_counts[rec["op_id"]] = done_counts.get(rec["op_id"], 0) + 1
        self.assertTrue(all(v == 1 for v in done_counts.values()), done_counts)
        self.assertEqual(files_outside(env.root, env.dest_root), self.before_out)
        self.assertEqual(files_inside(env.dest_root), self.before_in)
        # the undo journal is not the forward run's journal
        fwd = env.get("/api/runs")["runs"][0]["plan_hash"]
        self.assertNotEqual(env.store.journal_path(h), env.store.journal_path(fwd))


class ApprovalTest(UndoCase):
    def test_undo_plan_needs_its_own_approval(self):
        env = self.env
        plan, _ = self.forward()
        up = self.build_undo()
        h = up["plan_hash"]
        self.assertNotEqual(h, plan["plan_hash"])
        self.assertEqual(env.post("/api/plan/approve", {"plan_hash": plan["plan_hash"]}, expect=None)[0], 409)
        self.assertEqual(env.post("/api/execute", {"plan_hash": h}, expect=None)[0], 409)
        self.assertEqual(env.post("/api/execute", {"plan_hash": plan["plan_hash"]}, expect=None)[0], 409)
        env.approve(h)
        self.assertTrue(env.get("/api/state")["plan"]["approved"])
        # any edit clears approval
        sid = env.all_samples(origin="incoming")[0]["id"]
        env.post("/api/items/edit", {"ids": [sid], "changes": {"genre": "Edited"}})
        st = env.get("/api/state")
        self.assertTrue(st["plan"]["stale"])
        self.assertFalse(st["plan"]["approved"])
        self.assertEqual(env.post("/api/execute", {"plan_hash": h}, expect=None)[0], 409)
        self.assertEqual(env.post("/api/plan/approve", {"plan_hash": h}, expect=None)[0], 409)
        # rebuilding gives a fresh, unapproved undo plan
        up2 = self.build_undo()
        st = env.get("/api/state")
        self.assertFalse(st["plan"]["approved"] or st["plan"]["stale"])
        self.assertEqual(up2["plan_hash"], h)
        env.approve(h)
        # nothing changed on disk while all of this happened
        self.assertTrue(all(os.path.exists(o["source"]) and not os.path.exists(o["dest"])
                            for o in self.ops_of(up2, "move")))


class ExecutorScopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="slo-undo-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = os.path.join(self.tmp, "Sample Packs")
        self.dest_root = os.path.join(self.root, "Samples")
        os.makedirs(os.path.join(self.root, "Pack"))
        os.makedirs(os.path.join(self.dest_root, "Loops"))
        self.journal = os.path.join(self.tmp, "data", "j.jsonl")

    def _file(self, p, data=b"RIFF....WAVEdata"):
        with open(p, "wb") as f:
            f.write(data)
        return p

    def _op(self, action, src, dest=None, link_to=None):
        st = os.stat(src)
        return {"id": models.op_id(action, src, dest), "action": action, "item_id": "x",
                "source": src, "dest": dest, "link_to": link_to, "sha256": sha256_file(src),
                "size": st.st_size, "mtime_ns": st.st_mtime_ns}

    def _run(self, ops, kind="undo"):
        plan = {"kind": kind, "root": self.root, "dest_root": self.dest_root, "ops": ops,
                "plan_hash": models.plan_hash(ops)}
        return {r["op_id"]: r for r in executor.execute(plan, self.journal, None)}

    def test_undo_source_outside_library_refused(self):
        src = self._file(os.path.join(self.root, "Pack", "a.wav"))
        op = self._op("move", src, os.path.join(self.root, "Other", "a.wav"))
        r = self._run([op])[op["id"]]
        self.assertEqual(r["status"], "held")
        self.assertIn("outside the library folder", r["error"])
        self.assertTrue(os.path.isfile(src))
        self.assertFalse(os.path.exists(op["dest"]))

    def test_undo_dest_inside_library_refused(self):
        src = self._file(os.path.join(self.dest_root, "Loops", "a.wav"))
        op = self._op("move", src, os.path.join(self.dest_root, "Back", "a.wav"))
        r = self._run([op])[op["id"]]
        self.assertEqual(r["status"], "held")
        self.assertIn("inside the library folder", r["error"])
        self.assertTrue(os.path.isfile(src))

    def test_undo_move_back_and_unlink(self):
        src = self._file(os.path.join(self.dest_root, "Loops", "a.wav"))
        link = os.path.join(self.dest_root, "Loops", "kit a.wav")
        os.link(src, link)
        back = os.path.join(self.root, "Pack", "a.wav")
        un = self._op("unlink", link, link_to=src)
        mv = self._op("move", src, back)
        res = self._run([mv, un])
        self.assertEqual(res[un["id"]]["status"], "done", res[un["id"]])
        self.assertEqual(res[mv["id"]]["status"], "done", res[mv["id"]])
        self.assertFalse(os.path.exists(link) or os.path.exists(src))
        self.assertEqual(os.stat(back).st_nlink, 1)

    def test_unlink_never_removes_last_name_and_only_in_undo_plans(self):
        a = self._file(os.path.join(self.dest_root, "Loops", "a.wav"))
        b = self._file(os.path.join(self.dest_root, "Loops", "b.wav"))  # same bytes, other file
        op = self._op("unlink", a, link_to=b)
        r = self._run([op])[op["id"]]
        self.assertEqual(r["status"], "held")
        self.assertTrue(os.path.isfile(a))
        os.link(a, os.path.join(self.dest_root, "Loops", "c.wav"))
        op2 = self._op("unlink", os.path.join(self.dest_root, "Loops", "c.wav"), link_to=a)
        r2 = self._run([op2], kind=None)[op2["id"]]
        self.assertEqual(r2["status"], "held")
        self.assertIn("only allowed in undo plans", r2["error"])
        self.assertTrue(os.path.isfile(op2["source"]))


if __name__ == "__main__":
    unittest.main()
