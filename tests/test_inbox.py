"""补采收件箱：检查规则；一局 --inbox 的二局范围过滤；导入时补采简历替换名册底稿。不联网，人物、机构全部虚构。"""

import json
import tempfile
import unittest
from pathlib import Path

from bureaus.dispatch import run
from tools import inbox
from zuzhibu import bureaus, importer, resume
from zuzhibu.store import REPO, Store

from tests.test_dispatch import _event
from tests.test_personnel import _career, _fact, _role, make_event, make_record

TODAY = "2026-10-18"
ROSTER_HEAD = "person_id,bureau,english_name,position_en,organization_en,since,official_bio_url,official_portrait_url,notes\n"


def _bundle(rec):
    return {"schema_version": "1.0", "date": TODAY, "items": [json.loads(json.dumps(rec["resume"]))]}


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = Store(root / "data").load()
        self.ceo = make_record("he-shan", "何山", [_role("青岚科技公司", "首席执行官", "enterprise", "2018-07")],
                               [_career("2018-07", "青岚科技公司", "首席执行官", ongoing=True)], bureaus.AI)
        self.store.save_person(self.ceo)
        self.task = root / "task"
        (self.task / "resumes").mkdir(parents=True)
        (self.task / "README.md").write_text("测试任务", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_roster_rules(self):
        (self.task / "roster.csv").write_text(
            ROSTER_HEAD
            + "lan-yu,whitehouse,Lan Yu,Secretary,Department of Harbors,2026-02-01,https://www.harbors.gov/lan-yu,,\n"
            + "x,whitehouse,Mo Hai,Director,Bureau of Tides,2026,https://en.wikipedia.org/wiki/Mo_Hai,,\n"
            + "he-shan,ai,He Shan,CEO,Qinglan,,https://www.qinglan.gov/about,,\n", encoding="utf-8")
        rep, found = inbox.check(self.task, self.store, TODAY)
        text = "\n".join(rep.errors)
        self.assertIn("person_id 须是 mo-hai", text)
        self.assertIn("一级来源", text)
        self.assertIn("库里已有此人 he-shan", text)
        self.assertFalse(any("Lan Yu" in e for e in rep.errors))
        self.assertIn("lan-yu", found["roster"])

    def test_resume_for_unknown_person_is_rejected(self):
        (self.task / "resumes" / "nobody.json").write_text(json.dumps(_bundle(self.ceo), ensure_ascii=False), encoding="utf-8")
        rep, _ = inbox.check(self.task, self.store, TODAY)
        self.assertTrue(any("库里没有这个编号" in e for e in rep.errors))

    def test_resume_schema_and_card_rules(self):
        broken = _bundle(self.ceo)
        del broken["items"][0]["person"]
        (self.task / "resumes" / "he-shan.json").write_text(json.dumps(broken, ensure_ascii=False), encoding="utf-8")
        rep, _ = inbox.check(self.task, self.store, TODAY)
        self.assertTrue(any("不合 bundle.schema.json" in e for e in rep.errors))
        # 合格式但缺出生年月：可交补采，提示未达完整简历卡。
        (self.task / "resumes" / "he-shan.json").write_text(json.dumps(_bundle(self.ceo), ensure_ascii=False), encoding="utf-8")
        rep, found = inbox.check(self.task, self.store, TODAY)
        self.assertEqual(found["resumes"], ["he-shan"])
        self.assertEqual(rep.errors, [])
        self.assertTrue(any("CR10" in w for w in rep.warnings))
        self.assertTrue(any("未达完整简历卡" in w for w in rep.warnings))

    def test_missing_facts_are_warnings_and_still_block_full_card(self):
        for key in ("gender", "nationality", "birth_date", "work_start"):
            with self.subTest(key=key):
                bundle = _bundle(self.ceo)
                person = bundle["items"][0]["person"]
                person["birth_date"] = _fact("1960-05")
                person["work_start"] = _fact("2018-07")
                person[key] = None
                path = self.task / "resumes" / "he-shan.json"
                path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
                rep, found = inbox.check(self.task, self.store, TODAY)
                self.assertEqual(rep.errors, [])
                self.assertEqual(found["resumes"], ["he-shan"])
                self.assertTrue(any("CR10" in w for w in rep.warnings))
                record = {**self.ceo, "resume": bundle["items"][0]}
                self.assertIsNone(resume.card_type(record)[0])
                self.assertFalse(resume.compose(record)["exportable"])
                self.assertIsNone(json.loads(path.read_text(encoding="utf-8"))["items"][0]["person"][key])

    def test_incomplete_resume_still_rejects_supplied_invalid_fields(self):
        for key, value, message in (
            ("nationality", "American", "国籍写"),
            ("birth_date", "1960", "出生年月写到月"),
            ("work_start", "2018", "参加工作时间写到月"),
        ):
            with self.subTest(key=key):
                bundle = _bundle(self.ceo)
                bundle["items"][0]["person"][key] = _fact(value)
                (self.task / "resumes" / "he-shan.json").write_text(
                    json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
                rep, _ = inbox.check(self.task, self.store, TODAY)
                self.assertTrue(any("CR10" in e and message in e for e in rep.errors), rep.errors)

    def test_incomplete_resume_still_rejects_other_lint_errors(self):
        bundle = _bundle(self.ceo)
        bundle["items"][0]["careers"][0]["role"] = "负责芯片业务"
        (self.task / "resumes" / "he-shan.json").write_text(
            json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
        rep, _ = inbox.check(self.task, self.store, TODAY)
        self.assertTrue(any("CR01" in e for e in rep.errors))

    def test_events_rules(self):
        leave = _event(kind="leave", person_id="he-shan", new_roles=[], other_current_roles=None,
                       ended_roles=[{"organization": "青岚科技公司", "role": "首席执行官", "organization_en": "Qinglan",
                                     "role_en": "CEO", "start": None, "end": "2026-10-01", "concurrent": False,
                                     "sector": "enterprise", "source_ids": ["e1"]}])
        ghost = _event(person_id="no-such-person")
        (self.task / "events.json").write_text(json.dumps({"events": [leave, ghost], "checked_candidates": [], "notes": ""},
                                                          ensure_ascii=False), encoding="utf-8")
        rep, _ = inbox.check(self.task, self.store, TODAY)
        self.assertTrue(any("no-such-person 库里没有" in e for e in rep.errors))
        self.assertTrue(any("other_current_roles" in w for w in rep.warnings))


class OutOfScopeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)).load()
        self.store.save_person(make_record("he-shan", "何山", [_role("青岚科技公司", "首席执行官", "enterprise", "2018-07")],
                                           [_career("2018-07", "青岚科技公司", "首席执行官", ongoing=True)], bureaus.AI))

    def tearDown(self):
        self.tmp.cleanup()

    def _record(self, org, role, org_en="", role_en=""):
        return {"person_id": None, "ended_roles": [],
                "new_roles": [{"organization": org, "role": role, "organization_en": org_en, "role_en": role_en}]}

    def test_board_seat_at_unlisted_company_is_out(self):
        self.assertIsNotNone(run.out_of_scope(self._record("白石数据公司", "独立董事", "Baishi Data", "Independent Director"), self.store))

    def test_top_role_or_listed_company_is_in(self):
        self.assertIsNone(run.out_of_scope(self._record("白石数据公司", "首席执行官"), self.store))
        self.assertIsNone(run.out_of_scope(self._record("青岚科技公司", "董事"), self.store))

    def test_known_person_is_always_in(self):
        rec = self._record("白石数据公司", "独立董事")
        rec["person_id"] = "he-shan"
        self.assertIsNone(run.out_of_scope(rec, self.store))


class UseBundleTest(unittest.TestCase):
    def test_bundle_replaces_seed_and_marks_pending_name(self):
        rec = make_record("lan-yu", "", [], [], bureaus.WHITEHOUSE, tier="seed")
        rec["origin"] = "名册首发：bureaus/personnel-whitehouse/roster/roster.csv"
        donor = make_record("lan-yu", "蓝雨", [_role("海港部", "部长", "politics", "2026-02")],
                            [_career("2026-02", "海港部", "部长", ongoing=True)], bureaus.WHITEHOUSE)
        bundle = {**_bundle(donor), "source_task": "sanju-test"}
        importer.use_bundle(rec, REPO / "bureaus" / "personnel-whitehouse" / "data" / "verified" / "lan-yu.json", bundle)
        self.assertEqual(rec["tier"], "draft")
        self.assertEqual(rec["resume"]["tags"][0], "draft")
        self.assertEqual((rec["name_zh"], rec["name_zh_pending"]), ("蓝雨", True))
        self.assertIn("补采简历 bureaus/personnel-whitehouse/data/verified/lan-yu.json（sanju-test", rec["origin"])
        self.assertEqual(rec["resume"]["current_roles"][0]["appointment_state"], "正式")

    def test_bundle_keeps_pending_roles_written_by_dispatch(self):
        path = REPO / "bureaus" / "personnel-others" / "data" / "verified" / "lan-yu.json"
        nominee = _role("美国海军部", "部长", "politics", "2026-09-14")
        nominee.update(appointment_state="提名人选", source_ids=["s2"])
        old = make_record("lan-yu", "蓝雨", [_role("美国海军部", "副部长", "politics", "2025-03"), nominee], [], bureaus.MILITARY)
        old["resume"]["sources"].append({"id": "s2", "url": "https://www.whitehouse.gov/nominations", "title": "Nominations",
                                         "published_at": "2026-09-14", "checked_at": "2026-10-17", "excerpt": "Lan Yu, to be Secretary", "tier": 1})
        donor = make_record("lan-yu", "蓝雨", [_role("美国海军部", "代理部长", "politics", "2026-04")], [], bureaus.MILITARY)
        rec = json.loads(json.dumps(old))
        notes = importer.use_bundle(rec, path, _bundle(donor), keep_from=old, event_urls={"https://www.whitehouse.gov/nominations"},
                                    today=TODAY)
        roles = {(r["role"], r["appointment_state"]) for r in rec["resume"]["current_roles"]}
        self.assertIn(("部长", "提名人选"), roles, "补采事实包没写提名，一局写回的提名人选不能丢")
        self.assertTrue(notes)
        moved = [r for r in rec["resume"]["current_roles"] if r["appointment_state"] == "提名人选"][0]
        urls = {s["id"]: s["url"] for s in rec["resume"]["sources"]}
        self.assertEqual([urls[i] for i in moved["source_ids"]], ["https://www.whitehouse.gov/nominations"])
        seated = make_record("lan-yu", "蓝雨", [_role("美国海军部", "部长", "politics", "2026-10-01")], [], bureaus.MILITARY)
        rec = json.loads(json.dumps(old))
        importer.use_bundle(rec, path, _bundle(seated), keep_from=old, event_urls={"https://www.whitehouse.gov/nominations"}, today=TODAY)
        self.assertEqual([(r["role"], r["appointment_state"]) for r in rec["resume"]["current_roles"]], [("部长", "正式")],
                         "提名之后已到任的，不再补回提名人选")

    def test_bundle_older_than_latest_event_is_not_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)).load()
            store.save_person(make_record("he-shan", "何山", [], [], bureaus.AI))
            store.save_event(make_event(store, "he-shan", "appoint", date="2026-10-10"))
            self.assertFalse(importer.bundle_is_current(store, "he-shan", {"date": "2026-10-01"}))
            self.assertTrue(importer.bundle_is_current(store, "he-shan", {"date": TODAY}))


if __name__ == "__main__":
    unittest.main()
