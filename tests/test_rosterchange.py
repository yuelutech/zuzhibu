"""名单换人：同一岗位和换人证据分开判断（副职顶不了正职、大法官不因职称相同互换、代理要有原任离开的证据、代理状态不抹掉、
国会按议席、商界按公司和首席执行官、提名和确认不换名单）；一组文件一起写，出错全部退回。人物、机构全部虚构。"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zuzhibu import bureaus, rosterchange, store as store_mod
from zuzhibu.store import Store, write_json

from tests.test_personnel import _role, make_record

TODAY = "2026-10-24"


def roster(rid, *groups):
    return {"id": rid, "title": rid, "groups": [{"title": "", "note": "", "style": "", "members": list(g)} for g in groups]}


def member(person, post, **kw):
    return {"person": person, "post": post, **kw}


def record(kind="appoint", pid=None, name_en="Qing Feng", date="2026-10-20", effective="2026-10-20", new=(), ended=(), eid=None):
    return {"id": eid or f"{date}-{pid or 'x'}-{kind}", "kind": kind, "date": date, "effective_at": effective, "person_id": pid,
            "person": {"name_zh": "", "name_en": name_en}, "new_roles": list(new), "ended_roles": list(ended)}


def role(org, title="", sector="politics", **kw):
    return {"organization": org, "role": title, "sector": sector, **kw}


class RosterChangeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)).load()
        people = [
            ("hai-lan", "海岚", [_role("美国国防部", "部长", start="2025-01")], bureaus.MILITARY),
            ("shan-he", "山禾", [_role("美国陆军部", "代理部长", start="2026-02")], bureaus.MILITARY),
            ("lin-mu", "林木", [_role("美国最高法院", "大法官", start="2006-01")], bureaus.JUSTICE),
            ("bai-yuan", "白远", [_role("美国最高法院", "大法官", start="2010-08")], bureaus.JUSTICE),
            ("he-shan", "何山", [_role("美国最高法院", "首席大法官", start="2005-09")], bureaus.JUSTICE),
            ("yu-tian", "雨田", [_role("美国联邦参议员（南湖州）", start="2015-01")], bureaus.CONGRESS),
            ("feng-ye", "枫叶", [_role("美国联邦参议员（南湖州）", start="2021-01")], bureaus.CONGRESS),
            ("shi-jian", "石坚", [_role("美国联邦众议员（南湖州第3选区）", start="2023-01")], bureaus.CONGRESS),
            ("jiang-yue", "江月", [_role("青岚科技公司", "董事长兼首席执行官", "enterprise", "2018-07")], bureaus.AI),
            ("song-lin", "松林", [_role("美国国务院", "国务卿", start="2025-01")], bureaus.WHITEHOUSE),
        ]
        for pid, name, roles, bureau in people:
            self.store.save_person(make_record(pid, name, roles, [], bureau))
        self.rosters = {
            "whitehouse": roster("whitehouse", [member("song-lin", "国务卿兼国家安全事务助理")],
                                 [member("hai-lan", "国防部部长")]),
            "defense": roster("defense", [member("hai-lan", "国防部部长")], [member("shan-he", "陆军部代理部长")]),
            "justice": roster("justice", [member("he-shan", "首席大法官")],
                              [member("lin-mu", "大法官"), member("bai-yuan", "大法官")]),
            "congress": roster("congress", [member("yu-tian", "南湖州", party="共和党", state="南湖州"),
                                            member("feng-ye", "南湖州", party="民主党", state="南湖州")],
                               [member("shi-jian", "南湖州第3选区", party="共和党", state="南湖州", district=3)]),
            "business": roster("business", [member("jiang-yue", "青岚科技公司董事长兼首席执行官")]),
        }

    def tearDown(self):
        self.tmp.cleanup()

    def find(self, rec, batch=None):
        return rosterchange.find(self.store, self.rosters, rec, batch if batch is not None else [rec], TODAY)

    def slot(self, section, gi, mi):
        return self.rosters[section]["groups"][gi]["members"][mi]

    def test_same_post_is_judged_by_post_not_by_similar_text(self):
        same = lambda section, gi, mi, r: rosterchange.same_post(section, self.slot(section, gi, mi), r, self.store)  # noqa: E731
        self.assertTrue(same("defense", 0, 0, role("美国国防部", "部长")))
        self.assertFalse(same("defense", 0, 0, role("美国国防部", "副部长")), "副职顶不了正职")
        self.assertFalse(same("defense", 0, 0, role("美国国防部", "常务副部长")))
        self.assertTrue(same("defense", 1, 0, role("美国陆军部", "部长")), "代理和正式是同一岗位")
        self.assertTrue(same("justice", 1, 0, role("美国最高法院", "大法官")))
        self.assertFalse(same("justice", 1, 0, role("南湖州最高法院", "大法官")), "只有职称相同、机构不同的不算")
        self.assertFalse(same("justice", 0, 0, role("美国最高法院", "大法官")), "大法官不是首席大法官")
        self.assertFalse(same("whitehouse", 0, 0, role("美国国务院", "国务卿")), "一个位置两项职务的不认，交维护者")
        self.assertTrue(same("congress", 1, 0, role("美国联邦众议员（南湖州第3选区）")), "众议员按选区")
        self.assertFalse(same("congress", 1, 0, role("美国联邦众议员（南湖州第4选区）")))
        self.assertTrue(same("congress", 0, 0, role("美国联邦参议员（南湖州）")))
        self.assertTrue(same("business", 0, 0, role("青岚科技公司", "首席执行官", "enterprise")), "商界按公司和首席执行官")
        self.assertFalse(same("business", 0, 0, role("青岚科技公司", "首席运营官", "enterprise")))
        self.assertFalse(same("business", 0, 0, role("蓝湾资本", "首席执行官", "enterprise")))

    def test_formal_arrival_replaces_the_single_holder_in_every_section(self):
        changes = self.find(record(new=[role("美国国防部", "部长")]))
        self.assertEqual(sorted((c["section"], c["kind"], c["old"], c["post_after"]) for c in changes),
                         [("defense", "replace", "hai-lan", "国防部部长"), ("whitehouse", "replace", "hai-lan", "国防部部长")])

    def test_nominations_confirmations_and_future_dates_do_not_change_the_roster(self):
        post = [role("美国国防部", "部长")]
        self.assertEqual(self.find(record("nominate", new=post)), [])
        self.assertEqual(self.find(record("confirm", new=post)), [], "参议院确认了也要等到任")
        self.assertEqual(self.find(record("announce", new=post)), [])
        self.assertEqual(self.find(record(new=post, effective="2026-11-01")), [], "生效日在未来的不换")

    def test_acting_needs_evidence_that_the_holder_left_and_keeps_its_acting_wording(self):
        acting = record("acting", new=[role("美国国防部", "代理部长")])
        self.assertEqual(self.find(acting), [], "代理可能只是暂代：没有原任离开的证据不换")
        left = record("leave", pid="hai-lan", name_en="Hai Lan", ended=[role("美国国防部", "部长")], eid="left")
        changes = self.find(acting, [left, acting])
        self.assertEqual({(c["section"], c["post_after"], c["state"]) for c in changes},
                         {("defense", "国防部代理部长", "代理"), ("whitehouse", "国防部代理部长", "代理")}, "代理的写法保留“代理”")
        stale = record("leave", pid="hai-lan", name_en="Hai Lan", date="2024-12-01", effective="2024-12-01",
                       ended=[role("美国国防部", "部长")], eid="stale")
        self.store.save_event(stale)
        self.assertEqual(self.find(acting), [], "他这次到任以前的离任不算")

    def test_acting_becomes_formal_only_changes_the_wording(self):
        changes = self.find(record(pid="shan-he", name_en="Shan He", new=[role("美国陆军部", "部长")]))
        self.assertEqual([(c["kind"], c["post_before"], c["post_after"]) for c in changes], [("restate", "陆军部代理部长", "陆军部部长")])
        self.assertEqual(self.find(record("acting", pid="hai-lan", name_en="Hai Lan", new=[role("美国国防部", "代理部长")])), [],
                         "正式的不改成代理")

    def test_justices_are_not_swapped_because_their_titles_match(self):
        justice = record(new=[role("美国最高法院", "大法官")])
        self.assertEqual(self.find(justice), [], "几位大法官职称相同：没有谁离开的证据就不动")
        retired = record("leave", pid="bai-yuan", name_en="Bai Yuan", ended=[role("美国最高法院", "大法官")], eid="retired")
        (change,) = self.find(justice, [retired, justice])
        self.assertEqual((change["old"], change["group"], change["index"]), ("bai-yuan", 1, 1), "只换已核实离开的那一位")
        both = record("leave", pid="lin-mu", name_en="Lin Mu", ended=[role("美国最高法院", "大法官")], eid="both")
        self.assertEqual(self.find(justice, [retired, both, justice]), [], "对上不止一位的不动")
        state = record(new=[role("南湖州最高法院", "大法官")])
        self.assertEqual(self.find(state, [retired, state]), [], "州最高法院的大法官顶不了联邦最高法院的位置")

    def test_congress_by_seat(self):
        house = record(new=[role("美国联邦众议员（南湖州第3选区）")])
        (change,) = self.find(house)
        self.assertEqual((change["section"], change["old"], change["post_after"]), ("congress", "shi-jian", "南湖州第3选区"))
        senator = record(new=[role("美国联邦参议员（南湖州）")])
        self.assertEqual(self.find(senator), [], "同一州两名参议员：没有谁离开的证据就不动")
        died = record("death", pid="yu-tian", name_en="Yu Tian", ended=[role("美国联邦参议员（南湖州）")], eid="died")
        (change,) = self.find(senator, [died, senator])
        self.assertEqual(change["old"], "yu-tian")

    def test_business_by_company_and_chief_executive(self):
        (change,) = self.find(record(new=[role("青岚科技公司", "首席执行官", "enterprise")]))
        self.assertEqual((change["old"], change["post_after"]), ("jiang-yue", "青岚科技公司首席执行官"))
        interim = record("acting", new=[role("青岚科技公司", "临时首席执行官", "enterprise")])
        self.assertEqual(self.find(interim), [])
        quit_ = record("leave", pid="jiang-yue", name_en="Jiang Yue", ended=[role("青岚科技公司", "董事长兼首席执行官", "enterprise")],
                       eid="quit")
        (change,) = self.find(interim, [quit_, interim])
        self.assertEqual(change["post_after"], "青岚科技公司临时首席执行官")

    def test_place_and_standing(self):
        (change,) = self.find(record(new=[role("美国联邦众议员（南湖州第3选区）")]))
        self.assertEqual(rosterchange.standing(self.rosters, {**change, "new": "qing-feng"}), "open")
        self.assertTrue(rosterchange.place(self.rosters, change, "qing-feng", "民主党"))
        self.assertEqual(self.slot("congress", 1, 0), member("qing-feng", "南湖州第3选区", party="民主党", state="南湖州", district=3))
        self.assertEqual(rosterchange.standing(self.rosters, {**change, "new": "qing-feng"}), "kept")
        self.assertFalse(rosterchange.place(self.rosters, change, "qing-feng"), "位置上已不是原任的不动")
        self.slot("congress", 1, 0)["person"] = "zhang-san"
        self.assertEqual(rosterchange.standing(self.rosters, {**change, "new": "qing-feng"}), "merged")

    def test_commit_writes_all_or_nothing(self):
        root = Path(self.tmp.name)
        keep, new, third = root / "rosters" / "defense.json", root / "people" / "qing-feng.json", root / "events" / "x.json"
        write_json(keep, {"v": 1})
        before = keep.read_bytes()
        real, calls = store_mod.write_json, []

        def flaky(path, obj):
            calls.append(path)
            if len(calls) == 3:
                raise OSError("磁盘满了")
            real(path, obj)

        with mock.patch.object(rosterchange, "write_json", flaky), self.assertRaises(OSError):
            rosterchange.commit({keep: {"v": 2}, new: {"id": "qing-feng"}, third: {"id": "x"}})
        self.assertEqual(keep.read_bytes(), before, "改过的退回原样")
        self.assertFalse(new.exists(), "新建的删掉")
        self.assertFalse(third.exists())
        rosterchange.commit({keep: {"v": 2}, new: {"id": "qing-feng"}})
        self.assertIn('"v": 2', keep.read_text(encoding="utf-8"))

    def test_ready_needs_full_card_and_photo(self):
        rec = make_record("qing-feng", "冯青", [_role("美国国防部", "部长", start="2026-10-20")], [], bureaus.MILITARY)
        self.assertTrue(rosterchange.ready(rec))
        rec["resume"]["person"]["birth_date"] = {"value": "1975", "source_ids": ["s1"], "inferred": False, "basis": None, "inference": None}
        rec["resume"]["person"]["work_start"] = {"value": "1997", "source_ids": ["s1"], "inferred": False, "basis": None, "inference": None}
        self.assertEqual(rosterchange.ready(rec), ["没有照片"])
        rec["portrait"] = {"url": "https://www.lakeview.gov/images/feng.jpg"}
        self.assertEqual(rosterchange.ready(rec), [])
        self.assertEqual(rosterchange.ready(None), ["库里没有这个人的记录"])


if __name__ == "__main__":
    unittest.main()
