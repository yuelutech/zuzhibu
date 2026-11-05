"""本机更新与维护者数据合并：本机立即更新写进的动态另记一份，维护者数据换新（整份换掉改过的人物文件）后重放回去；
维护者已有同一条的以维护者为准；维护者有更晚动态的不重放、标待核；没被换掉的不动。人物、机构全部虚构。"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zuzhibu import bureaus, datasync, localupdates, personnel, rosterchange
from zuzhibu.store import Store, read_json, write_json

from tests.test_personnel import _career, _role, make_event, make_record

TODAY = "2026-10-24"


def user_event(store, pid, kind="appoint", date="2026-10-20", run="20261021-100000", **kw):
    """本机立即更新写进的一条动态（写回前的样子）。"""
    ev = make_event(store, pid, kind, date=date, **kw)
    ev["person"] = {"name_zh": store.person(pid)["name_zh"], "name_en": store.person(pid)["name_en"]} if store.person(pid) else ev["person"]
    ev["verification"].update(run=run, method="api")
    ev["origin"] = localupdates.ORIGIN
    ev["headline"] = f"{pid} {kind}"
    ev["bureaus"] = []
    return ev


def apply_locally(store, journal, ev):
    """照一局流水线的做法写回并另记一份。"""
    personnel.apply_event(store, ev, TODAY)
    store.save_event(ev)
    journal.record(ev)
    return ev


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.v1 = root / "maintainer-v1"            # 维护者 10 月的数据
        self.local = root / "local-data"             # 软件本机数据目录（从 v1 铺底，立即更新写这里）
        self.journal = localupdates.Journal(root / "journal")
        store = Store(self.v1).load()
        store.save_person(make_record("he-shan", "何山", [_role("青岚科技公司", "首席执行官", "enterprise", "2018-07")],
                                      [_career("2018-07", "青岚科技公司", "首席执行官", ongoing=True)], bureaus.AI))
        store.save_person(make_record("lin-mu", "林木", [_role("美国商务部", "副部长", "politics", "2025-03")],
                                      [_career("2025-03", "美国商务部", "副部长", ongoing=True)], bureaus.WHITEHOUSE))
        store.save_person(make_record("bai-yuan", "白远", [_role("美国能源部", "副部长", "politics", "2025-05")],
                                      [_career("2025-05", "美国能源部", "副部长", ongoing=True)], bureaus.WHITEHOUSE))
        store.save_meta({"updated_at": "2026-10-01", "daily": {"last_window": ["2026-09-28", "2026-10-01"]}})
        shutil.copytree(self.v1, self.local)

    def tearDown(self):
        self.tmp.cleanup()

    def local_update(self):
        """本机立即更新：何山另任董事长、林木离任、白远调任，各写回人物记录并另记一份。"""
        store = Store(self.local).load()
        new_role = {"organization": "蓝湾资本", "role": "董事长", "organization_en": "Bluebay Capital", "role_en": "Chairman",
                    "start": "2026-10-20", "concurrent": True, "sector": "enterprise", "source_ids": ["e1"]}
        self.ev_he = apply_locally(store, self.journal, user_event(store, "he-shan", new=[new_role]))
        self.ev_lin = apply_locally(store, self.journal, user_event(
            store, "lin-mu", "leave", date="2026-10-15", ended=[{"organization": "美国商务部", "role": "副部长"}]))
        moved = {"organization": "美国内政部", "role": "副部长", "organization_en": "Department of the Interior",
                 "role_en": "Deputy Secretary", "start": "2026-10-18", "sector": "politics", "source_ids": ["e1"]}
        self.ev_bai = apply_locally(store, self.journal, user_event(
            store, "bai-yuan", "transfer", date="2026-10-18", ended=[{"organization": "美国能源部", "role": "副部长"}], new=[moved]))
        self.journal.save_state(last_run="2026-10-21T10:30:00", last_window=["2026-10-17", "2026-10-21"], last_applied=3)
        return store

    def sync(self, maintainer: Store):
        """模拟数据分发换快照：维护者改过、新增的文件整份换成维护者的；只有本机有的文件留着。"""
        for path in maintainer.root.rglob("*.json"):
            rel = path.relative_to(maintainer.root)
            old = self.v1 / rel
            if not old.exists() or old.read_bytes() != path.read_bytes():
                (self.local / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, self.local / rel)

    def maintainer_v2(self):
        """维护者 11 月的数据：林木离任他也查到了（日期差 2 天）；何山有一条更早的旧动态，人物文件因此改过；白远没动。"""
        v2 = Path(self.tmp.name) / "maintainer-v2"
        shutil.copytree(self.v1, v2)
        store = Store(v2).load()
        leave = make_event(store, "lin-mu", "leave", date="2026-10-17", ended=[{"organization": "美国商务部", "role": "副部长"}])
        leave["origin"] = "daily"
        leave["verification"]["run"] = "20261101-080000"
        personnel.apply_event(store, leave, "2026-11-01")
        board = make_event(store, "he-shan", "appoint", date="2026-09-30",
                           new=[{"organization": "青岚研究院", "role": "理事", "start": "2026-09-30", "concurrent": True,
                                 "sector": "advisory", "source_ids": ["e1"]}])
        board["origin"] = "daily"
        board["verification"]["run"] = "20261101-080000"
        personnel.apply_event(store, board, "2026-11-01")
        meta = store.meta()
        meta.update(updated_at="2026-11-01", daily={"last_window": ["2026-10-01", "2026-11-01"]})
        store.save_meta(meta)
        return store

    def test_local_changes_survive_maintainer_update(self):
        self.local_update()
        self.sync(self.maintainer_v2())
        report = localupdates.merge(Store(self.local), self.journal, TODAY)
        self.assertEqual((report["replayed"], report["merged"], report["kept"], report["pending"], report["failed"]), (1, 1, 1, 0, 0),
                         report["details"])
        store = Store(self.local).load()
        he = store.person("he-shan")
        roles = [r["organization"] + r["role"] for r in he["resume"]["current_roles"]]
        self.assertIn("蓝湾资本董事长", roles, "维护者换掉了何山的文件，本机这条重放回去")
        self.assertIn("青岚研究院理事", roles, "维护者的改动也在")
        self.assertIn(self.ev_he["id"], he["events"])
        self.assertIsNone(store.event(self.ev_lin["id"]), "维护者已有同一条：本机副本删掉，以维护者的为准")
        self.assertEqual([e["origin"] for e in store.events() if e.get("person_id") == "lin-mu"], ["daily"])
        bai = store.person("bai-yuan")
        self.assertEqual([r["organization"] for r in bai["resume"]["current_roles"]], ["美国内政部"], "没被换掉的不动")
        meta = read_json(self.local / "meta.json")
        self.assertEqual(meta["daily"]["last_window"], ["2026-10-01", "2026-11-01"], "维护者的每日更新状态照旧")
        self.assertEqual(meta["user_update"]["last_merge"]["replayed"], 1)
        self.assertEqual(meta["updated_at"], "2026-11-01", "重放不把数据日期改成今天")
        local = {e["id"]: e["local"]["status"] for e in self.journal.events()}
        self.assertEqual(local, {self.ev_he["id"]: "replayed", self.ev_lin["id"]: "merged", self.ev_bai["id"]: "kept"})

    def test_merge_twice_changes_nothing(self):
        self.local_update()
        self.sync(self.maintainer_v2())
        localupdates.merge(Store(self.local), self.journal, TODAY)
        snapshot = {p: p.read_bytes() for p in self.local.rglob("*.json")}
        again = localupdates.merge(Store(self.local), self.journal, TODAY)
        self.assertFalse(again["changed"], again["details"])
        self.assertEqual({p: p.read_bytes() for p in self.local.rglob("*.json")}, snapshot)

    def test_later_maintainer_event_is_not_overwritten(self):
        self.local_update()
        v2 = Path(self.tmp.name) / "maintainer-v2"
        shutil.copytree(self.v1, v2)
        store = Store(v2).load()
        back = make_event(store, "bai-yuan", "leave", date="2026-10-28", ended=[{"organization": "美国能源部", "role": "副部长"}])
        back["origin"] = "daily"
        personnel.apply_event(store, back, "2026-11-01")
        self.sync(store)
        report = localupdates.merge(Store(self.local), self.journal, TODAY)
        self.assertEqual(report["pending"], 1, report["details"])
        local = Store(self.local).load()
        ev = local.event(self.ev_bai["id"])
        self.assertEqual(ev["applied"]["status"], personnel.PENDING)
        self.assertIn("更晚的动态", ev["applied"]["reason"])
        self.assertNotIn("美国内政部", [r["organization"] for r in local.person("bai-yuan")["resume"]["current_roles"]],
                         "旧消息不盖掉维护者更晚的资料")

    def test_new_person_created_locally_is_carried_and_replayed(self):
        store = Store(self.local).load()
        ev = make_event(store, None, "appoint", date="2026-10-19", name="冯青",
                        new=[{"organization": "美国财政部", "role": "副部长", "organization_en": "Department of the Treasury",
                              "role_en": "Deputy Secretary", "start": "2026-10-19", "sector": "politics", "source_ids": ["e1"]}])
        ev["person"]["name_en"] = "Qing Feng"
        ev["verification"]["run"] = "20261021-100000"
        apply_locally(store, self.journal, ev)
        pid = ev["person_id"]
        self.assertEqual(pid, "qing-feng")
        (self.local / "people" / f"{pid}.json").unlink()   # 本机文件丢了（比如重新铺底）：照原件重建
        report = localupdates.merge(Store(self.local), self.journal, TODAY)
        self.assertEqual(report["replayed"], 1, report["details"])
        self.assertEqual(Store(self.local).load().person(pid)["resume"]["current_roles"][0]["organization"], "美国财政部")

    def test_after_sync_never_raises(self):
        self.assertIsNone(localupdates.after_sync(self.local, self.journal.root), "没有本机动态时什么也不做")
        self.local_update()
        (self.local / "people" / "he-shan.json").write_text("{坏文件", encoding="utf-8")
        out = localupdates.after_sync(self.local, self.journal.root, TODAY)
        self.assertIn("error", out)

    def test_home_follows_env(self):
        with mock.patch.dict("os.environ", {"ZUZHIBU_HOME": str(Path(self.tmp.name) / "home")}):
            self.assertEqual(localupdates.default_journal(), Path(self.tmp.name) / "home" / "local")


def full_card(pid, name, roles, bureau, photo="https://www.lakeview.gov/images/x.jpg"):
    """够上名单的人物记录：完整简历卡（五项有来源、现任职务有一级来源）、有照片。"""
    rec = make_record(pid, name, roles, [], bureau)
    rec["resume"]["person"]["birth_date"] = {"value": "1975", "source_ids": ["s1"], "inferred": False, "basis": None, "inference": None}
    rec["resume"]["person"]["work_start"] = {"value": "1997", "source_ids": ["s1"], "inferred": False, "basis": None, "inference": None}
    rec["portrait"] = {"url": photo, "source": "https://www.lakeview.gov/leadership/" + pid}
    return rec


def army_roster(*members):
    return {"id": "defense", "title": "国防", "groups": [{"title": "各军种", "note": "", "style": "", "members": list(members)}]}


class RosterMergeTest(unittest.TestCase):
    """名单变动和本机取到的标准简历：维护者数据还没跟上的重放（旧数据不盖掉已核实的新任命）；维护者已换人、换成别人、
    或有更晚变动的，以维护者为准；维护者已有完整简历卡的用维护者的。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.v1, self.local = root / "maintainer-v1", root / "local-data"
        self.journal = localupdates.Journal(root / "journal")
        store = Store(self.v1).load()
        store.save_person(make_record("lin-mu", "林木", [_role("美国陆军部", "部长", "politics", "2025-03")], [], bureaus.MILITARY))
        store.save_meta({"updated_at": "2026-10-01"})
        write_json(self.v1 / "rosters" / "defense.json", army_roster({"person": "lin-mu", "post": "陆军部部长"}))
        shutil.copytree(self.v1, self.local)
        # 本机立即更新：冯青到任陆军部部长，取到标准简历和官网照片，名单换人（照 bureaus/dispatch/run.py 的写法）
        local = Store(self.local).load()
        feng = full_card("qing-feng", "冯青", [_role("美国陆军部", "部长", "politics", "2026-10-20")], bureaus.MILITARY)
        feng["name_en"] = "Qing Feng"
        local._people["qing-feng"] = feng
        ev = user_event(local, "qing-feng", date="2026-10-20",
                        new=[{"organization": "美国陆军部", "role": "部长", "start": "2026-10-20", "sector": "politics", "source_ids": ["e1"]}])
        personnel.apply_event(local, ev, TODAY)
        local.save_event(ev)
        change = {"kind": "replace", "section": "defense", "group": 0, "index": 0, "old": "lin-mu", "new": "qing-feng",
                  "post_before": "陆军部部长", "post_after": "陆军部部长", "state": "正式", "evidence": "正式到任",
                  "event": ev["id"], "date": "2026-10-20"}
        write_json(self.local / "rosters" / "defense.json", army_roster({"person": "qing-feng", "post": "陆军部部长"}))
        self.journal.record({**ev, "roster": [change]})
        write_json(self.journal.person_path("qing-feng"), local.person("qing-feng"))
        self.ev = ev

    def tearDown(self):
        self.tmp.cleanup()

    def maintainer(self, roster, people=(), events=()):
        """维护者的新数据：v1 加上改过的名单、人物和动态；数据分发把改过的文件整份换成维护者的。"""
        v2 = Path(self.tmp.name) / "maintainer-v2"
        shutil.copytree(self.v1, v2)
        store = Store(v2).load()
        for rec in people:
            store.save_person(rec)
        for ev in events:
            personnel.apply_event(store, ev, "2026-11-01")
        write_json(v2 / "rosters" / "defense.json", roster)
        for path in v2.rglob("*.json"):
            rel = path.relative_to(v2)
            old = self.v1 / rel
            if not old.exists() or old.read_bytes() != path.read_bytes():
                (self.local / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, self.local / rel)
        return localupdates.merge(Store(self.local), self.journal, TODAY)

    def members(self):
        return [(m["person"], m["post"]) for g in read_json(self.local / "rosters" / "defense.json")["groups"] for m in g["members"]]

    def test_old_maintainer_roster_does_not_overwrite_the_verified_appointment(self):
        report = self.maintainer(army_roster({"person": "lin-mu", "post": "陆军部部长"}, {"person": "shan-he", "post": "陆军参谋长"}))
        self.assertEqual(report["roster"], {"kept": 0, "replayed": 1, "merged": 0}, report["details"])
        self.assertEqual(self.members(), [("qing-feng", "陆军部部长"), ("shan-he", "陆军参谋长")], "本机换人重放，维护者别处的改动也在")

    def test_maintainer_who_already_made_the_change_wins(self):
        theirs = full_card("qing-feng", "冯青", [_role("美国陆军部", "部长", "politics", "2026-10-20")], bureaus.MILITARY,
                           photo="https://www.lakeview.gov/images/maintainer.jpg")
        theirs["name_en"] = "Qing Feng"
        report = self.maintainer(army_roster({"person": "qing-feng", "post": "陆军部部长"}), people=[theirs])
        self.assertEqual(report["roster"]["kept"], 1, report["details"])
        self.assertEqual(Store(self.local).load().person("qing-feng")["portrait"]["url"], "https://www.lakeview.gov/images/maintainer.jpg",
                         "维护者已有完整简历卡的用维护者的")

    def test_maintainer_who_chose_someone_else_wins(self):
        report = self.maintainer(army_roster({"person": "zhang-san", "post": "陆军部代理部长"}))
        self.assertEqual(report["roster"]["merged"], 1, report["details"])
        self.assertEqual(self.members(), [("zhang-san", "陆军部代理部长")])

    def test_later_maintainer_change_is_not_overwritten(self):
        maintainer = Store(self.v1).load()
        later = make_event(maintainer, "lin-mu", "appoint", date="2026-10-28",
                           new=[{"organization": "美国陆军部", "role": "部长", "start": "2026-10-28", "sector": "politics", "source_ids": ["e1"]}])
        later["origin"] = "daily"
        report = self.maintainer(army_roster({"person": "lin-mu", "post": "陆军部部长"}, {"person": "shan-he", "post": "陆军参谋长"}),
                                 events=[later])
        self.assertEqual(report["roster"]["merged"], 1, report["details"])
        self.assertEqual(self.members()[0], ("lin-mu", "陆军部部长"))
        self.assertIn("更晚的变动", report["details"][0]["note"])

    def test_local_resume_is_installed_when_the_maintainer_only_has_a_seed(self):
        seed = make_record("qing-feng", "冯青", [_role("美国陆军部", "部长", "politics", "2026-10-20")], [], bureaus.MILITARY, tier="seed")
        seed["name_en"] = "Qing Feng"
        report = self.maintainer(army_roster({"person": "lin-mu", "post": "陆军部部长"}, {"person": "shan-he", "post": "陆军参谋长"}),
                                 people=[seed])
        rec = Store(self.local).load().person("qing-feng")
        self.assertEqual(rosterchange.ready(rec), [], "装上本机取到的标准简历和照片")
        self.assertEqual(report["roster"]["replayed"], 1, report["details"])
        again = localupdates.merge(Store(self.local), self.journal, TODAY)
        self.assertFalse(again["changed"], "再放一次什么也不变")


class DataSyncTest(unittest.TestCase):
    """真走一遍数据分发换快照（附带数据铺底 → 本机立即更新 → 维护者发了改过此人的新数据 → apply），本机写回的不丢。"""

    def test_apply_replays_local_updates_before_switching(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            v1 = base / "v1"
            store = Store(v1).load()
            store.save_person(make_record("he-shan", "何山", [_role("青岚科技公司", "首席执行官", "enterprise", "2018-07")],
                                          [_career("2018-07", "青岚科技公司", "首席执行官", ongoing=True)], bureaus.AI))
            store.save_meta({"updated_at": "2026-10-01", "daily": {"last_window": ["2026-09-28", "2026-10-01"]}})
            self.publish(v1, "2026-10-01T00:05:00Z")
            home = datasync.DataHome(base / "app" / "data")
            datasync.ensure_seed(home, v1)
            journal = localupdates.Journal(base / "app" / "local")   # 软件本机目录下：data\ 旁边的 local\
            local = Store(home.current()).load()
            new_role = {"organization": "蓝湾资本", "role": "董事长", "start": "2026-10-20", "concurrent": True,
                        "sector": "enterprise", "source_ids": ["e1"]}
            ev = apply_locally(local, journal, user_event(local, "he-shan", new=[new_role]))

            v2 = base / "v2"
            shutil.copytree(v1, v2)
            (v2 / datasync.MANIFEST).unlink()
            maintainer = Store(v2).load()
            board = make_event(maintainer, "he-shan", "appoint", date="2026-09-30",
                               new=[{"organization": "青岚研究院", "role": "理事", "start": "2026-09-30", "concurrent": True,
                                     "sector": "advisory", "source_ids": ["e1"]}])
            board["origin"] = "daily"
            personnel.apply_event(maintainer, board, "2026-11-01")
            maintainer.save_meta({**maintainer.meta(), "updated_at": "2026-11-01"})
            manifest = self.publish(v2, "2026-11-01T00:05:00Z")

            result = datasync.apply(home, manifest, datasync.LocalSource(v2), origin="online")
            self.assertEqual(result["local"]["replayed"], 1, result["local"])
            switched = Store(home.current()).load()
            roles = [r["organization"] + r["role"] for r in switched.person("he-shan")["resume"]["current_roles"]]
            self.assertIn("蓝湾资本董事长", roles, "维护者换掉了此人的文件，本机写回的重放回来")
            self.assertIn("青岚研究院理事", roles)
            self.assertIsNotNone(switched.event(ev["id"]))
            self.assertEqual(read_json(v1 / "people" / "he-shan.json")["resume"]["current_roles"][0]["organization"], "青岚科技公司",
                             "重放写新文件，不改硬链接共用的附带数据")

    @staticmethod
    def publish(root: Path, generated_at: str) -> dict:
        manifest = {**datasync.build_manifest(root), "generated_at": generated_at}
        (root / datasync.MANIFEST).write_text(datasync.dumps_manifest(manifest), encoding="utf-8", newline="\n")
        return manifest


if __name__ == "__main__":
    unittest.main()
