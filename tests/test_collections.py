"""Favorites, collections, smart collections, "new since last run" and plan-only-new.

Runs the real HTTP server on the synthetic fixture (tools/make_fixtures.py) in a temp
dir, like test_e2e. Nothing here touches a real collection.
"""
from __future__ import annotations

import csv
import os
import shutil
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from test_e2e import Env, _IMPORT_ERROR, make_fixtures, native, sha256_file  # noqa: E402

NEW_PACK = "Zeta Sounds - New Arrivals"


def add_new_pack(root: str, m: dict) -> dict:
    """Write a new pack folder into the fixture. Returns rel_path -> sha256."""
    mf = make_fixtures
    old_hat = native(root, "Nightfall Audio - Midnight Trap/One Shots/Hats/Closed Hat 03.wav")
    old_kit_kick = native(root, m["kit_kit_dup"][0])
    with open(old_hat, "rb") as f:
        hat = f.read()
    with open(old_kit_kick, "rb") as f:
        kit_kick = f.read()
    kit = f"{NEW_PACK}/Construction Kits/Dawn 120 C"
    files = {
        f"{NEW_PACK}/One Shots/Kick Fresh 01.wav": mf.wav_bytes(mf.kick(77, 0.22)),
        f"{NEW_PACK}/One Shots/Snare Fresh.wav": mf.wav_bytes(mf.noise(0.2, 88, decay=18)),
        f"{NEW_PACK}/One Shots/Hat Dup.wav": hat,                  # duplicates an old file
        f"{kit}/Stems/Kick.wav": kit_kick,                       # duplicates an old kit stem
        f"{kit}/Stems/Pad.wav": mf.wav_bytes(mf.sine(261.6, 1.0, 0.3), extra_chunks=mf.acid(120, "C")),
        f"{kit}/MIDI/Pad.mid": mf.midi_bytes((60, 64, 67), 120),
        f"{kit}/Project.als": mf.als_bytes(["Stems/Kick.wav", "Stems/Pad.wav"], "Dawn 120 C"),
    }
    out = {}
    import hashlib
    for rel, data in files.items():
        p = native(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        out[rel] = hashlib.sha256(data).hexdigest()
    return out


class CollectionsCase(unittest.TestCase):
    def setUp(self):
        if _IMPORT_ERROR is not None:
            self.fail(f"samplelib import failed: {_IMPORT_ERROR!r}")
        self.env = Env()
        self.addCleanup(self.env.close)
        self.m = self.env.manifest

    def rows(self):
        return self.env.by_rel()

    def star(self, ids, on=None, expect=200):
        body = {"ids": ids}
        if on is not None:
            body["on"] = on
        return self.env.post("/api/collections/star", body, expect=expect)[1]

    # ------------------------------------------------------------- favorites
    def test_star_unstar_toggle_and_shared_content(self):
        env = self.env
        env.scan()
        rows = self.rows()
        kick = rows["Nightfall Audio - Midnight Trap/One Shots/Kicks/Kick 01.wav"]
        self.assertFalse(kick["starred"])
        r = self.star([kick["id"]])
        self.assertTrue(r["starred"])
        # identical content elsewhere is starred too (membership is by sha256)
        twins = [x for x in rows.values() if x["sha256"] == kick["sha256"]]
        self.assertGreater(len(twins), 1)
        self.assertEqual(set(r["affected_ids"]), {x["id"] for x in twins})
        fav = env.all_samples(fav=1)
        self.assertEqual({x["id"] for x in fav}, {x["id"] for x in twins})
        self.assertTrue(all(x["starred"] for x in fav))
        lst = env.get("/api/collections")
        self.assertEqual(lst["favorites"]["count"], len(twins))
        self.assertEqual(lst["favorites"]["hashes"], 1)
        self.assertEqual(env.get("/api/state")["collections"]["favorites"], 1)
        # toggle again unstars
        r = self.star([kick["id"]])
        self.assertFalse(r["starred"])
        self.assertEqual(env.all_samples(fav=1), [])
        # explicit on/off
        self.star([kick["id"]], on=True)
        self.star([kick["id"]], on=True)
        self.assertEqual(len(env.all_samples(fav=1)), len(twins))
        self.star([kick["id"]], on=False)
        self.assertEqual(env.all_samples(fav=1), [])
        # persisted in the data dir only
        self.assertTrue(os.path.isfile(os.path.join(env.data, "collections.json")))

    def test_item_without_hash_cannot_be_starred(self):
        env = self.env
        env.scan()
        rows = self.rows()
        it = rows["Lofi Lab - Dusty Keys/One Shots/Vocal Chop Hey.wav"]
        env.store.by_id[it["id"]]["sha256"] = None
        env.store.by_id[it["id"]]["hash_error"] = "permission denied"
        code, j = env.post("/api/collections/star", {"ids": [it["id"]]}, expect=None)
        self.assertEqual(code, 400)
        self.assertIn("fingerprint", j["error"])
        other = rows["Lofi Lab - Dusty Keys/Loops/Dark Keys 90 BPM F#m.wav"]
        r = self.star([it["id"], other["id"]])
        self.assertTrue(r["starred"])
        self.assertEqual([s["id"] for s in r["skipped"]], [it["id"]])

    # ------------------------------------------------------------ collections
    def test_collections_crud(self):
        env = self.env
        env.scan()
        rows = self.rows()
        a = rows["Nightfall Audio - Midnight Trap/One Shots/Snares/Snare Tight.wav"]
        b = rows["Prime Grooves - Bass Science/One Shots/Sub Bass C.wav"]
        c = env.post("/api/collections/create", {"name": "Trap drums", "ids": [a["id"]]})[1]["collection"]
        self.assertEqual((c["name"], c["kind"], c["members"]), ("Trap drums", "manual", 1))
        # duplicate / reserved / empty names refused
        for bad in ("trap DRUMS", "Favorites", "  "):
            code, _ = env.post("/api/collections/create", {"name": bad}, expect=None)
            self.assertEqual(code, 400, bad)
        r = env.post("/api/collections/add", {"id": c["id"], "ids": [a["id"], b["id"]]})[1]
        self.assertEqual(r["changed"], 1)
        got = {x["id"] for x in env.all_samples(collection=c["id"])}
        self.assertEqual(got, {a["id"], b["id"]})
        row = env.get(f"/api/samples/{a['id']}")
        self.assertEqual(row["collections"], [c["id"]])
        lst = {x["id"]: x for x in env.get("/api/collections")["collections"]}
        self.assertEqual(lst[c["id"]]["count"], 2)
        env.post("/api/collections/remove", {"id": c["id"], "ids": [a["id"]]})
        self.assertEqual({x["id"] for x in env.all_samples(collection=c["id"])}, {b["id"]})
        r = env.post("/api/collections/rename", {"id": c["id"], "name": "Current song"})[1]
        self.assertEqual(r["collection"]["name"], "Current song")
        env.post("/api/collections/delete", {"id": c["id"]})
        self.assertEqual(env.get("/api/collections")["collections"], [])
        code, _ = env.post("/api/collections/delete", {"id": c["id"]}, expect=None)
        self.assertEqual(code, 400)
        code, _ = env.raw("GET", "/api/samples", q={"collection": "nope"})[:2] + (None,)[:0] or (0, 0)
        self.assertEqual(env.raw("GET", "/api/samples", q={"collection": "nope"})[0], 404)

    def test_smart_collection_save_and_restore(self):
        env = self.env
        env.scan()
        params = {"q": "kick", "kind": "audio,midi,preset", "category": "One-Shots/Drums",
                  "junk": "ignored"}
        s = env.post("/api/collections/smart", {"name": "Kicks", "params": params})[1]["collection"]
        self.assertEqual(s["kind"], "smart")
        self.assertNotIn("junk", s["params"])
        self.assertEqual(s["params"]["q"], "kick")
        want = env.all_samples(q="kick", kind="audio,midi,preset", category="One-Shots/Drums")
        self.assertTrue(want)
        got = env.all_samples(collection=s["id"])
        self.assertEqual([x["id"] for x in got], [x["id"] for x in want])
        lst = {x["id"]: x for x in env.get("/api/collections")["collections"]}
        self.assertEqual(lst[s["id"]]["count"], len(want))
        self.assertEqual(env.get(f"/api/collections/{s['id']}")["params"], s["params"])
        # survives a restart
        env.stop()
        env.start()
        again = env.get(f"/api/collections/{s['id']}")
        self.assertEqual(again["params"], s["params"])
        # update and empty refused
        env.post("/api/collections/smart", {"id": s["id"], "params": {"q": "snare"}})
        self.assertEqual(env.get(f"/api/collections/{s['id']}")["params"], {"q": "snare"})
        code, _ = env.post("/api/collections/smart", {"name": "Empty", "params": {}}, expect=None)
        self.assertEqual(code, 400)
        code, _ = env.post("/api/collections/add", {"id": s["id"], "ids": [want[0]["id"]]}, expect=None)
        self.assertEqual(code, 400)

    def test_collections_survive_rescan_and_follow_moved_file(self):
        env, m = self.env, self.m
        env.scan()
        rows = self.rows()
        rel = "Nightfall Audio - Midnight Trap/One Shots/Snares/Snare Tight.wav"
        snare = rows[rel]
        self.star([snare["id"]])
        c = env.post("/api/collections/create", {"name": "Trap drums", "ids": [snare["id"]]})[1]["collection"]
        env.scan()   # rescan: still starred
        self.assertTrue(self.rows()[rel]["starred"])
        plan = env.build_plan()
        h = plan["plan_hash"]
        env.approve(h)
        # starring after approval must not clear it
        other = self.rows()["Prime Grooves - Bass Science/One Shots/Sub Bass C.wav"]
        self.star([other["id"]])
        env.post("/api/collections/add", {"id": c["id"], "ids": [other["id"]]})
        st = env.get("/api/state")
        self.assertTrue(st["plan"]["approved"])
        self.assertFalse(st["plan"]["stale"])
        env.execute(h)
        self.assertFalse(os.path.exists(native(env.root, rel)), "snare should have moved")
        # catalog has the collections column
        cat = os.path.join(env.dest_root, "Catalog", "Sample_Catalog.csv")
        with open(cat, "r", encoding="utf-8-sig", newline="") as f:
            rd = list(csv.DictReader(f))
        self.assertIn("collections", rd[0])
        by_sha = {r["sha256"]: r for r in rd}
        self.assertEqual(by_sha[snare["sha256"]]["collections"], "Favorites; Trap drums")
        # after a rescan the file is a library item at its new path, still in both
        env.scan()
        fav = env.all_samples(fav=1)
        moved = [x for x in fav if x["sha256"] == snare["sha256"]]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]["origin"], "library")
        self.assertTrue(os.path.isfile(moved[0]["abs_path"]))
        self.assertEqual(sha256_file(moved[0]["abs_path"]), m["sha256"][rel])
        self.assertIn(moved[0]["id"], {x["id"] for x in env.all_samples(collection=c["id"])})
        # and nothing is new after the run (moves keep identity; library paths are known)
        self.assertEqual(env.all_samples(new=1), [])


class NewFilesCase(unittest.TestCase):
    def setUp(self):
        if _IMPORT_ERROR is not None:
            self.fail(f"samplelib import failed: {_IMPORT_ERROR!r}")
        self.env = Env()
        self.addCleanup(self.env.close)
        self.m = self.env.manifest

    def test_first_scan_marks_nothing_new(self):
        env = self.env
        env.scan()
        self.assertEqual(env.all_samples(new=1, kind=""), [])
        self.assertEqual(env.get("/api/state")["scan_summary"]["new_files"], 0)
        code, j = env.post("/api/plan/build", {"only_new": True}, expect=None)
        self.assertEqual(code, 400)

    def test_incremental_rescan_reuses_hashes_and_marks_new(self):
        from samplelib import inventory
        env = self.env
        env.scan()
        n_before = len(env.all_samples(kind=""))
        added = add_new_pack(env.root, self.m)
        calls = []
        real = inventory.sha256_file

        def counting(path, *a, **kw):
            calls.append(path)
            return real(path, *a, **kw)
        inventory.sha256_file = counting
        try:
            env.scan()
        finally:
            inventory.sha256_file = real
        hashed = sorted(os.path.relpath(p, env.root).replace(os.sep, "/") for p in calls)
        self.assertEqual(hashed, sorted(added), "only new files may be hashed on a rescan")
        allrows = env.all_samples(kind="")
        self.assertEqual(len(allrows), n_before + len(added))
        new = env.all_samples(new=1, kind="")
        self.assertEqual(sorted(x["rel_path"] for x in new), sorted(added))
        self.assertTrue(all(x["is_new"] for x in new))
        self.assertEqual(sum(1 for x in allrows if x["is_new"]), len(added))
        ss = env.get("/api/state")["scan_summary"]
        self.assertEqual(ss["new_files"], len(added))
        self.assertEqual(ss["new_packs"], 1)
        # a second rescan without a run keeps them new
        env.scan()
        self.assertEqual(len(env.all_samples(new=1, kind="")), len(added))
        # survives a restart (persisted in inventory)
        env.stop()
        env.start()
        self.assertEqual(len(env.all_samples(new=1, kind="")), len(added))

    def test_plan_only_new_files(self):
        env = self.env
        env.scan()
        added = add_new_pack(env.root, self.m)
        env.scan()
        rows = env.by_rel()
        env.post("/api/plan/build", {"only_new": True})
        env.wait()
        plan = env.plan()
        self.assertTrue(plan["summary"].get("only_new"))
        ops = plan["ops"]
        inc = [r for r in rows.values() if r["origin"] == "incoming"]
        # every incoming source exactly once
        self.assertEqual(sorted(o["item_id"] for o in ops), sorted(r["id"] for r in inc))
        by_item = {o["item_id"]: o for o in ops}
        for r in inc:
            o = by_item[r["id"]]
            if r["rel_path"] in added:
                continue
            self.assertEqual(o["action"], "keep", r["rel_path"])
            self.assertEqual(o["reason"], "not new since last run; left for a later plan")
        new_ops = [by_item[rows[rel]["id"]] for rel in added]
        self.assertTrue(any(o["action"] == "move" for o in new_ops))
        kick = by_item[rows[f"{NEW_PACK}/One Shots/Kick Fresh 01.wav"]["id"]]
        self.assertEqual(kick["action"], "move")
        # the new hat duplicates an old file that stays in place: keep, canonical is real
        hat = by_item[rows[f"{NEW_PACK}/One Shots/Hat Dup.wav"]["id"]]
        self.assertEqual(hat["action"], "keep")
        self.assertTrue(hat["canonical"] and os.path.isfile(hat["canonical"]), hat)
        # no write op may depend on or link to something that is not moved in this plan
        dests = {o["dest"] for o in ops if o["action"] == "move"}
        ids = {o["id"] for o in ops if o["action"] == "move"}
        for o in ops:
            if o["action"] == "link":
                self.assertTrue(o["link_to"] in dests or not o.get("depends_on"), o)
                if o.get("depends_on"):
                    self.assertIn(o["depends_on"], ids)
        # approval and execute stay bound to the hash
        h = plan["plan_hash"]
        code, _ = env.post("/api/execute", {"plan_hash": h}, expect=None)
        self.assertEqual(code, 409)
        code, _ = env.post("/api/plan/approve", {"plan_hash": "0" * 64}, expect=None)
        self.assertEqual(code, 409)
        env.approve(h)
        old_src = native(env.root, "Nightfall Audio - Midnight Trap/One Shots/Snares/Snare Tight.wav")
        env.execute(h)
        res = {r["op_id"]: r for r in env.log()}
        for o in ops:
            if o["action"] in ("move", "link"):
                self.assertEqual(res[o["id"]]["status"], "done", (o, res[o["id"]]))
                self.assertTrue(os.path.isfile(o["dest"]))
        self.assertTrue(os.path.isfile(old_src), "old files must stay where they are")
        for rel in added:
            o = by_item[rows[rel]["id"]]
            if o["action"] == "move":
                self.assertFalse(os.path.exists(native(env.root, rel)))
        # after the run, the baseline moves: nothing is new on the next scan
        env.scan()
        self.assertEqual(env.all_samples(new=1, kind=""), [])

    def test_old_app_data_without_new_fields_loads(self):
        env = self.env
        env.scan()
        # simulate data from the previous version: no is_new, no baseline, no collections
        import gzip
        import json
        p = os.path.join(env.data, "inventory.json.gz")
        with gzip.open(p, "rt", encoding="utf-8") as f:
            inv = json.load(f)
        for it in inv["items"]:
            it.pop("is_new", None)
        with gzip.open(p, "wt", encoding="utf-8") as f:
            json.dump(inv, f)
        env.stop()
        for n in ("known_files.json.gz", "collections.json"):
            try:
                os.remove(os.path.join(env.data, n))
            except OSError:
                pass
        env.start()
        rows = env.all_samples(kind="")
        self.assertTrue(rows)
        self.assertFalse(any(r["is_new"] or r["starred"] for r in rows))
        self.assertEqual(env.get("/api/collections")["collections"], [])
        # the previous inventory becomes the baseline: a pack added since then is new
        added = add_new_pack(env.root, self.m)
        env.scan()
        self.assertEqual(sorted(x["rel_path"] for x in env.all_samples(new=1, kind="")), sorted(added))


if __name__ == "__main__":
    unittest.main()
