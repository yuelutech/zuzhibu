"""动态写回人物记录：调任、转入七局的三项核对、七局复任、新建人物、卸任议员连带委员会职务。

人物、机构全部虚构。
"""

import copy
import tempfile
import unittest
from pathlib import Path

from zuzhibu import bureaus, personnel, resume
from zuzhibu.store import Store

TODAY = "2026-10-17"


def _fact(v, ids=("s1",)):
    return {"value": v, "source_ids": list(ids), "inferred": False, "basis": None, "inference": None}


def _career(start, org, role, end=None, ongoing=False):
    return {"kind": "work", "start": _fact(start), "end": _fact(end) if end else None, "organization": org, "role": role,
            "program": None, "degree_awarded": None, "concurrent": False, "source_ids": ["s1"], "ongoing": ongoing,
            "roles": [], "role_category": None, "role_basis": None, "study_level": None, "full_time": None,
            "annotations": [], "appointment_state": None, "stable_identity": False}


def _role(org, role="", sector="politics", start="2019-01", concurrent=False):
    return {"organization": org, "role": role, "start": _fact(start) if start else None, "concurrent": concurrent,
            "source_ids": ["s1"], "roles": [role] if role and sector == "committee" else [], "sector": sector,
            "appointment_state": "正式"}


def make_record(pid, name, roles, careers, bureau, tier="gold"):
    item = personnel.new_resume(pid, name, "")
    item["tags"] = [tier]
    item["sources"] = [{"id": "s1", "url": "https://example.com/" + pid, "title": "官方履历（示例）",
                        "published_at": None, "checked_at": "2026-06-01", "excerpt": "", "tier": 1}]
    item["person"]["gender"] = _fact("男")
    item["person"]["nationality"] = _fact("美国")
    item["current_roles"] = roles
    item["careers"] = careers
    item["unknowns"] = []
    return {"id": pid, "name_zh": name, "name_en": pid.replace("-", " ").title(), "bureau": bureau, "also_in": [],
            "tier": tier, "ids": {}, "resume": item, "data_cutoff": "2026-06-01", "updated_at": "2026-06-01",
            "events": [], "bureau_history": []}


def make_event(store, pid, kind, ended=(), new=(), others=None, appointment="absent", date="2026-09-01", name=""):
    ev = {"id": store.new_event_id(date, pid or "x", kind), "date": date, "kind": kind, "person_id": pid,
          "person": {"name_zh": name, "name_en": ""}, "announced_at": None, "effective_at": date,
          "ended_roles": list(ended), "new_roles": list(new),
          "sources": [{"id": "e1", "url": "https://example.com/release-" + kind, "title": "任免公告（示例）",
                       "published_at": date, "checked_at": TODAY, "excerpt": "示例摘录"}],
          "verification": {"status": "verified", "checked_at": TODAY, "by": "test"}}
    if others is not None:
        ev["other_current_roles"] = others
    if appointment != "absent":
        ev["new_appointment"] = appointment
        ev["new_appointment_checked"] = True
    return ev


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)).load()
        senator = make_record(
            "lin-mu", "林木",
            [_role("美国联邦参议员（北岸州）", start="2003-01"), _role("参议院预算委员会", "主席", "committee", None, True),
             _role("参议院司法委员会", "委员", "committee", None, True)],
            [_career("1995", "美国国会众议院", "联邦众议员（北岸州第3选区）", "2003"),
             _career("2003", "美国国会参议院", "联邦参议员（北岸州）", ongoing=True)],
            bureaus.CONGRESS)
        ceo = make_record("he-shan", "何山", [_role("青岚科技公司", "首席执行官", "enterprise", "2018-07")],
                          [_career("2018-07", "青岚科技公司", "首席执行官", ongoing=True)], bureaus.AI)
        self.store.save_person(senator)
        self.store.save_person(ceo)

    def tearDown(self):
        self.tmp.cleanup()

    def test_leave_with_both_checks_moves_to_retired(self):
        ev = make_event(self.store, "lin-mu", "leave", ended=[{"organization": "美国联邦参议员（北岸州）", "role": ""}],
                        others=[], appointment=None, date="2026-07-11")
        out = personnel.apply_event(self.store, ev, TODAY)
        rec = self.store.person("lin-mu")
        self.assertEqual(out["status"], personnel.APPLIED)
        self.assertEqual(rec["bureau"], bureaus.RETIRED)
        self.assertEqual(rec["resume"]["current_roles"], [], "议员席位和两个委员会职务都应移出")
        senate = [c for c in rec["resume"]["careers"] if "参议院" in c["organization"]][0]
        self.assertFalse(senate["ongoing"])
        self.assertEqual(senate["end"]["value"], "2026-07-11")
        self.assertEqual(rec["bureau_history"][-1]["to"], bureaus.RETIRED)
        self.assertIn(ev["id"], rec["events"])
        self.assertEqual(rec["updated_at"], TODAY)
        # 历史履历原样保留
        self.assertEqual(len(rec["resume"]["careers"]), 2)
        text = resume.compose(rec)["text"]
        self.assertIn("2003年－2026年7月", text)
        self.assertNotIn("现任", text)

    def test_leave_without_explicit_checks_stays_pending(self):
        ev = make_event(self.store, "lin-mu", "leave", ended=[{"organization": "美国联邦参议员（北岸州）", "role": ""}],
                        date="2026-07-11")  # 没写 other_current_roles、new_appointment
        out = personnel.apply_event(self.store, ev, TODAY)
        self.assertEqual(out["status"], personnel.PENDING)
        self.assertEqual(self.store.person("lin-mu")["bureau"], bureaus.CONGRESS)
        self.assertIn("没有其他现职", out["reason"])

    def test_leave_with_announced_appointment_stays_pending(self):
        ev = make_event(self.store, "lin-mu", "leave", ended=[{"organization": "美国联邦参议员（北岸州）", "role": ""}],
                        others=[], appointment={"organization": "美国驻北岸国大使馆", "role": "大使"}, date="2026-07-11")
        out = personnel.apply_event(self.store, ev, TODAY)
        self.assertEqual(out["status"], personnel.PENDING)
        self.assertEqual(self.store.person("lin-mu")["bureau"], bureaus.CONGRESS)

    def test_dropping_one_concurrent_post_keeps_bureau(self):
        ev = make_event(self.store, "lin-mu", "leave", ended=[{"organization": "参议院预算委员会", "role": "主席"}],
                        others=[{"organization": "美国联邦参议员（北岸州）", "role": ""}], appointment=None)
        out = personnel.apply_event(self.store, ev, TODAY)
        rec = self.store.person("lin-mu")
        self.assertEqual(out["status"], personnel.APPLIED)
        self.assertEqual(rec["bureau"], bureaus.CONGRESS)
        self.assertEqual(len(rec["resume"]["current_roles"]), 2)

    def test_transfer_between_bureaus(self):
        ev = make_event(self.store, "lin-mu", "transfer", ended=[{"organization": "美国联邦参议员（北岸州）", "role": ""}],
                        new=[{"organization": "美国国务院", "role": "国务卿", "organization_en": "Department of State"}],
                        others=[], appointment=None, date="2026-09-15")
        out = personnel.apply_event(self.store, ev, TODAY)
        rec = self.store.person("lin-mu")
        self.assertEqual(out["status"], personnel.APPLIED)
        self.assertEqual(rec["bureau"], bureaus.WHITEHOUSE)
        self.assertEqual(rec["resume"]["current_roles"][0]["organization"], "美国国务院")
        self.assertTrue(rec["resume"]["careers"][-1]["ongoing"])
        self.assertEqual(rec["bureau_history"][-1], {"from": bureaus.CONGRESS, "to": bureaus.WHITEHOUSE, "date": "2026-09-15",
                                                    "event": ev["id"], "reason": out["reason"]})

    def test_retired_person_reinstated(self):
        leave = make_event(self.store, "he-shan", "leave", ended=[{"organization": "青岚科技公司", "role": "首席执行官"}],
                           others=[], appointment=None, date="2026-08-01")
        personnel.apply_event(self.store, leave, TODAY)
        self.assertEqual(self.store.person("he-shan")["bureau"], bureaus.RETIRED)
        back = make_event(self.store, "he-shan", "appoint", new=[{"organization": "美国参谋长联席会议", "role": "主席"}],
                          date="2026-10-01")
        out = personnel.apply_event(self.store, back, TODAY)
        rec = self.store.person("he-shan")
        self.assertEqual(rec["bureau"], bureaus.MILITARY)
        self.assertIn("重新任职", "".join(out["changes"]))
        self.assertEqual([h["to"] for h in rec["bureau_history"]], [bureaus.RETIRED, bureaus.MILITARY])

    def test_new_role_outside_scope_is_undecided(self):
        ev = make_event(self.store, "lin-mu", "transfer", ended=[{"organization": "美国联邦参议员（北岸州）", "role": ""}],
                        new=[{"organization": "北岸律师事务所", "role": "合伙人", "organization_en": "Northshore Law LLP"}],
                        others=[], appointment=None)
        out = personnel.apply_event(self.store, ev, TODAY)
        self.assertEqual(out["status"], personnel.UNDECIDED)
        self.assertEqual(self.store.person("lin-mu")["bureau"], bureaus.CONGRESS)

    def test_ai_leader_moving_to_unlisted_company_stays_in_ai(self):
        ev = make_event(self.store, "he-shan", "transfer", ended=[{"organization": "青岚科技公司", "role": "首席执行官"}],
                        new=[{"organization": "远帆公司", "role": "联合创始人", "organization_en": "Yuanfan Inc."}],
                        others=[], appointment=None)
        out = personnel.apply_event(self.store, ev, TODAY)
        self.assertEqual(out["status"], personnel.APPLIED)
        self.assertEqual(self.store.person("he-shan")["bureau"], bureaus.AI)

    def test_unknown_person_creates_seed_record(self):
        ev = make_event(self.store, None, "appoint", new=[{"organization": "美国联邦众议员（南湖州第13选区）", "role": ""}],
                        name="白远")
        ev["person"]["name_en"] = "Yuan Bai"
        ev["person"]["ids"] = {"bioguide": "X000009"}
        out = personnel.apply_event(self.store, ev, TODAY)
        rec = self.store.person(out["person_id"])
        self.assertEqual(rec["tier"], "seed")
        self.assertEqual(rec["ids"], {"bioguide": "X000009"})
        self.assertTrue(rec["name_zh_pending"])
        self.assertEqual(rec["bureau"], bureaus.CONGRESS)
        self.assertFalse(resume.compose(rec)["exportable"])
        self.assertEqual(ev["person_id"], rec["id"])

    def test_nomination_then_taking_office(self):
        ev = make_event(self.store, "he-shan", "nominate", new=[{"organization": "美国商务部", "role": "部长"}], date="2026-09-01")
        out = personnel.apply_event(self.store, ev, TODAY)
        rec = self.store.person("he-shan")
        self.assertEqual(out["status"], personnel.RECORDED)
        nominee = [r for r in rec["resume"]["current_roles"] if r["organization"] == "美国商务部"][0]
        self.assertEqual(nominee["appointment_state"], "提名人选")
        self.assertEqual(rec["bureau"], bureaus.AI, "提名人选不改所属局，到任才改")
        self.assertIn("美国商务部部长提名人选", resume.compose(rec)["text"])
        took = make_event(self.store, "he-shan", "appoint", new=[{"organization": "美国商务部", "role": "部长"}], date="2026-10-02")
        personnel.apply_event(self.store, took, TODAY)
        rec = self.store.person("he-shan")
        seated = [r for r in rec["resume"]["current_roles"] if r["organization"] == "美国商务部"]
        self.assertEqual(len(seated), 1)
        self.assertEqual(seated[0]["appointment_state"], "正式")
        self.assertEqual(seated[0]["start"]["value"], "2026-10-02")
        careers = [c for c in rec["resume"]["careers"] if c["organization"] == "美国商务部"]
        self.assertEqual([(c["start"]["value"], c["appointment_state"]) for c in careers], [("2026-10-02", "正式")])
        self.assertEqual(rec["resume"]["person"]["status"], "在任")

    def test_leaving_the_chair_keeps_the_board_seat(self):
        rec = self.store.person("he-shan")
        rec["resume"]["current_roles"] = [_role("青岚科技公司", "董事", "enterprise", "2016-02")]
        rec["resume"]["careers"] = [_career("2016-02", "青岚科技公司", "董事", ongoing=True)]
        self.store.save_person(rec)
        ev = make_event(self.store, "he-shan", "leave", ended=[{"organization": "青岚科技公司", "role": "董事长"}],
                        others=[{"organization": "青岚科技公司", "role": "董事"}], appointment=None, date="2026-06-01")
        out = personnel.apply_event(self.store, ev, TODAY)
        rec = self.store.person("he-shan")
        self.assertEqual([r["role"] for r in rec["resume"]["current_roles"]], ["董事"], "卸任董事长不能移走董事")
        self.assertTrue(rec["resume"]["careers"][0]["ongoing"])
        self.assertIn("找不到对应项", "".join(out["notes"]))
        self.assertEqual(rec["resume"]["person"]["status"], "在任")

    def test_director_becoming_chair_is_a_new_role(self):
        rec = self.store.person("he-shan")
        rec["resume"]["current_roles"] = [_role("青岚科技公司", "董事", "enterprise", "2016-02")]
        self.store.save_person(rec)
        ev = make_event(self.store, "he-shan", "appoint", new=[{"organization": "青岚科技公司", "role": "董事长"}])
        personnel.apply_event(self.store, ev, TODAY)
        roles = [r["role"] for r in self.store.person("he-shan")["resume"]["current_roles"]]
        self.assertEqual(roles, ["董事长", "董事"])

    def test_acting_role_stored_two_ways_is_one_role(self):
        rec = self.store.person("he-shan")
        acting = _role("美国移民和海关执法局", "局长", start="2026-06-01")
        acting["appointment_state"] = "代理"
        rec["resume"]["current_roles"] = [acting]
        self.store.save_person(rec)
        ev = make_event(self.store, "he-shan", "acting", new=[{"organization": "美国移民和海关执法局", "role": "代理局长"}],
                        date="2026-06-01")
        out = personnel.apply_event(self.store, ev, TODAY)
        roles = self.store.person("he-shan")["resume"]["current_roles"]
        self.assertEqual(len(roles), 1, out)
        self.assertIn("已在现任职务里", "".join(out["notes"]))

    def test_late_arriving_event_does_not_revive_an_ended_tenure(self):
        rec = self.store.person("he-shan")
        rec["resume"]["careers"].append(_career("2026-06-19", "美国国家情报总监办公室", "代理国家情报总监", "2026-08-03"))
        self.store.save_person(rec)
        ev = make_event(self.store, "he-shan", "acting", new=[{"organization": "美国国家情报总监办公室", "role": "代理国家情报总监"}],
                        date="2026-06-19")
        out = personnel.apply_event(self.store, ev, TODAY)
        rec = self.store.person("he-shan")
        self.assertFalse([r for r in rec["resume"]["current_roles"] if "情报" in r["organization"]])
        self.assertEqual(sum(1 for c in rec["resume"]["careers"] if "情报" in c["organization"]), 1)
        self.assertIn("这段任职已结束", "".join(out["notes"]))

    def test_title_repeating_the_organization_is_the_same_title(self):
        self.assertTrue(personnel.same_title({"organization": "白宫", "role": "总统助理兼白宫法律顾问"},
                                             {"organization": "美国白宫", "role": "总统助理、法律顾问"}))

    def test_titles_conflict(self):
        def r(role):
            return {"organization": "青岚科技公司", "role": role}
        for a, b in (("董事", "董事长"), ("秘书", "秘书长"), ("法官", "首席法官"), ("部长", "代理部长"), ("局长", "副局长"),
                     ("部长", "常务副部长")):
            self.assertTrue(personnel.titles_conflict(r(a), r(b)), (a, b))
        for a, b in (("首席执行官", "总裁兼首席执行官"), ("大使", "特命全权大使"), ("董事", "董事"), ("", "董事长")):
            self.assertFalse(personnel.titles_conflict(r(a), r(b)), (a, b))

    def test_earlier_confirmation_after_taking_office_adds_no_pending_role(self):
        rec = self.store.person("he-shan")
        rec["resume"]["current_roles"].append(_role("美国驻北岸国大使馆", "大使", start="2026-10-15"))
        self.store.save_person(rec)
        ev = make_event(self.store, "he-shan", "confirm", new=[{"organization": "美国驻北岸国大使馆", "role": "特命全权大使"}],
                        date="2026-08-07")
        ev["announced_at"] = "2026-08-07"
        out = personnel.apply_event(self.store, ev, TODAY)
        roles = [(r["role"], r["appointment_state"]) for r in self.store.person("he-shan")["resume"]["current_roles"]
                 if r["organization"] == "美国驻北岸国大使馆"]
        self.assertEqual(roles, [("大使", "正式")], "已到任的不再挂一条提名人选")
        self.assertIn("阶段不另写", "".join(out["notes"]))
        later = make_event(self.store, "he-shan", "nominate", new=[{"organization": "美国驻北岸国大使馆", "role": "公使"}],
                           date="2026-10-16")
        later["announced_at"], later["effective_at"] = "2026-10-16", None
        personnel.apply_event(self.store, later, TODAY)
        states = {r["role"]: r["appointment_state"] for r in self.store.person("he-shan")["resume"]["current_roles"]}
        self.assertEqual(states.get("公使"), "提名人选", "到任之后的新提名照写")

    def test_state_word_in_role_name_is_not_doubled(self):
        ev = make_event(self.store, "he-shan", "nominate", new=[{"organization": "美国驻北岸国大使馆", "role": "大使提名人选"}])
        personnel.apply_event(self.store, ev, TODAY)
        rec = self.store.person("he-shan")
        nominee = [r for r in rec["resume"]["current_roles"] if r["organization"] == "美国驻北岸国大使馆"][0]
        self.assertEqual((nominee["role"], nominee["appointment_state"]), ("大使", "提名人选"))
        text = resume.compose(rec)["text"]
        self.assertIn("美国驻北岸国大使馆大使提名人选", text)
        self.assertNotIn("提名人选提名人选", text)

    def test_assign_bureau_after_rules_change(self):
        rec = make_record("yuan-bai", "白远", [_role("美国全国信用合作社管理局", "主席", start="2026-08-24")], [], None, tier="seed")
        rec["events"] = ["2026-08-24-yuan-bai-appoint"]
        self.store.save_person(rec)
        self.store.save_event({"id": "2026-08-24-yuan-bai-appoint", "date": "2026-08-24", "kind": "appoint", "person_id": "yuan-bai",
                               "bureaus": [], "applied": {"status": personnel.UNDECIDED, "bureau_before": None, "bureau_after": None,
                                                          "changes": [], "notes": []}})
        self.assertEqual(personnel.assign_bureau(self.store, rec, TODAY, "规则补充"), bureaus.WHITEHOUSE)
        rec = self.store.person("yuan-bai")
        self.assertEqual(rec["bureau"], bureaus.WHITEHOUSE)
        self.assertEqual(rec["bureau_history"][-1]["to"], bureaus.WHITEHOUSE)
        ev = self.store.event("2026-08-24-yuan-bai-appoint")
        self.assertEqual((ev["applied"]["status"], ev["applied"]["bureau_after"]), (personnel.APPLIED, bureaus.WHITEHOUSE))
        self.assertIn(bureaus.WHITEHOUSE, ev["bureaus"])
        self.assertIsNone(personnel.assign_bureau(self.store, rec, TODAY, "规则补充"), "已有所属局的不动")

    def test_confirmation_with_first_tier_sworn_date_is_taking_office(self):
        def confirm(url, excerpt, effective="2026-08-21"):
            ev = make_event(self.store, None, "confirm", new=[{"organization": "美国功绩制保护委员会", "role": "主席"}],
                            date=effective, name="白远")
            ev["person"]["name_en"], ev["announced_at"], ev["effective_at"] = "Yuan Bai", "2026-08-07", effective
            ev["sources"] = [{"id": "e1", "url": url, "title": "Members", "published_at": None, "checked_at": TODAY, "excerpt": excerpt}]
            return ev
        ev = confirm("https://www.mspb.gov/About/members.htm", "he was sworn in to that role on August 21, 2026.")
        out = personnel.apply_event(self.store, ev, TODAY)
        rec = self.store.person(out["person_id"])
        self.assertEqual(out["status"], personnel.APPLIED)
        self.assertEqual(rec["resume"]["current_roles"][0]["appointment_state"], "正式")
        self.assertEqual(rec["resume"]["current_roles"][0]["start"]["value"], "2026-08-21")
        self.assertEqual(rec["bureau"], bureaus.WHITEHOUSE)
        self.assertIn("到任（一级来源）", "".join(out["notes"]))
        media = confirm("https://news.example.com/x", "he was sworn in on August 21, 2026.")
        media["person"]["name_en"] = "Shan He"
        self.assertEqual(personnel.apply_event(self.store, media, TODAY)["status"], personnel.RECORDED, "只有媒体报道的宣誓日不算")
        future = confirm("https://www.mspb.gov/About/members.htm", "will be sworn in on December 1, 2026.", "2026-12-01")
        future["person"]["name_en"] = "Mu Lin"
        self.assertEqual(personnel.apply_event(self.store, future, TODAY)["status"], personnel.RECORDED, "宣誓日在将来的不算")

    def test_future_announcement_is_planned_not_current(self):
        ev = make_event(self.store, None, "announce", new=[{"organization": "青岚科技公司", "role": "执行董事长"}], name="白远")
        ev["person"]["name_en"], ev["announced_at"], ev["effective_at"] = "Yuan Bai", "2026-09-03", "2026-12-01"
        out = personnel.apply_event(self.store, ev, TODAY)
        rec = self.store.person(out["person_id"])
        self.assertEqual(out["status"], personnel.RECORDED)
        self.assertEqual(rec["resume"]["current_roles"], [], "生效日之前不改现任")
        self.assertEqual(rec["resume"]["careers"], [])
        self.assertEqual([(p["action"], p["role"], p["date"]) for p in rec["planned_roles"]], [("任", "执行董事长", "2026-12-01")])
        self.assertEqual(rec["resume"]["person"]["status"], "拟任")
        self.assertIn("拟于2026年12月1日任青岚科技公司执行董事长。", resume.compose(rec)["doc"]["lead"])
        took = make_event(self.store, rec["id"], "appoint", new=[{"organization": "青岚科技公司", "role": "执行董事长"}], date="2026-12-01")
        personnel.apply_event(self.store, took, "2026-12-02")
        rec = self.store.person(rec["id"])
        self.assertNotIn("planned_roles", rec, "到任后删去拟任")
        self.assertEqual(rec["resume"]["current_roles"][0]["appointment_state"], "正式")

    def test_nomination_after_announcement_moves_state_forward(self):
        announce = make_event(self.store, "he-shan", "announce", new=[{"organization": "美国海军部", "role": "部长"}], date="2026-09-01")
        announce["effective_at"] = None
        personnel.apply_event(self.store, announce, TODAY)
        nominate = make_event(self.store, "he-shan", "nominate", new=[{"organization": "美国海军部", "role": "部长提名人选"}],
                              date="2026-09-14")
        nominate["effective_at"] = None
        out = personnel.apply_event(self.store, nominate, TODAY)
        navy = [r for r in self.store.person("he-shan")["resume"]["current_roles"] if r["organization"] == "美国海军部"]
        self.assertEqual([(r["role"], r["appointment_state"]) for r in navy], [("部长", "提名人选")])
        self.assertIn("由候任改为提名人选", "".join(out["changes"]))

    def test_death_sets_status_and_date(self):
        ev = make_event(self.store, "lin-mu", "death", ended=[{"organization": "美国联邦参议员（北岸州）", "role": ""}],
                        others=[], appointment=None, date="2026-07-11")
        personnel.apply_event(self.store, ev, TODAY)
        person = self.store.person("lin-mu")["resume"]["person"]
        self.assertEqual(person["status"], "逝世")
        self.assertEqual(person["death_date"]["value"], "2026-07-11")

    def test_unverified_event_is_not_applied(self):
        ev = make_event(self.store, "he-shan", "leave", ended=[{"organization": "青岚科技公司", "role": "首席执行官"}],
                        others=[], appointment=None)
        ev["verification"]["status"] = "held"
        out = personnel.apply_event(self.store, ev, TODAY)
        self.assertEqual(out["status"], personnel.SKIPPED)
        self.assertEqual(self.store.person("he-shan")["bureau"], bureaus.AI)

    def test_saved_files_reload(self):
        ev = make_event(self.store, "he-shan", "leave", ended=[{"organization": "青岚科技公司", "role": "首席执行官"}],
                        others=[], appointment=None)
        personnel.apply_event(self.store, ev, TODAY)
        again = Store(self.store.root).load()
        self.assertEqual(again.person("he-shan")["bureau"], bureaus.RETIRED)
        self.assertEqual(again.event(ev["id"])["applied"]["status"], personnel.APPLIED)


class ClassifyTest(unittest.TestCase):
    def test_rules(self):
        cases = [
            ({"organization": "美国联邦参议员（北岸州）"}, bureaus.CONGRESS),
            ({"organization": "北岸州议会众议院", "role": "州众议员"}, None),
            ({"organization": "美国副总统"}, bureaus.WHITEHOUSE),
            ({"organization_en": "Department of War", "role_en": "Secretary of War"}, bureaus.MILITARY),
            ({"organization_en": "U.S. Space Force", "role_en": "Chief of Space Operations"}, bureaus.MILITARY),
            ({"organization_en": "Supreme Court of the United States", "role_en": "Associate Justice"}, bureaus.JUSTICE),
            ({"organization_en": "Federal Bureau of Investigation", "role_en": "Director"}, bureaus.JUSTICE),
            ({"organization_en": "Central Intelligence Agency", "role_en": "Director"}, bureaus.WHITEHOUSE),
            ({"organization_en": "Board of Governors of the Federal Reserve System", "role_en": "Governor"}, bureaus.WHITEHOUSE),
            ({"organization_en": "Apple", "role_en": "Chief Executive Officer"}, bureaus.AI),
            ({"organization_en": "Armstrong Law LLP", "role_en": "Partner"}, None),
            ({"organization_en": "U.S. Special Operations Command", "role_en": "Commander"}, bureaus.MILITARY),
            ({"organization_en": "National Aeronautics and Space Administration", "role_en": "Administrator"}, bureaus.WHITEHOUSE),
            ({"organization_en": "Commodity Futures Trading Commission", "role_en": "Chairman"}, bureaus.WHITEHOUSE),
            ({"organization_en": "United States Mission to the United Nations",
              "role_en": "United States Representative to the United Nations"}, bureaus.WHITEHOUSE),
            ({"organization_en": "Office of National Drug Control Policy", "role_en": "Director"}, bureaus.WHITEHOUSE),
            ({"organization_en": "Federal Housing Finance Agency", "role_en": "Director"}, bureaus.WHITEHOUSE),
            ({"organization_en": "Lakeview Capital", "role_en": "Managing Partner"}, bureaus.AI),
            # 独立机构归三局（2026-10-21 补）
            ({"organization_en": "National Capital Planning Commission", "role_en": "Executive Director"}, bureaus.WHITEHOUSE),
            ({"organization": "美国全国信用合作社管理局", "role": "主席"}, bureaus.WHITEHOUSE),
            ({"organization_en": "National Archives and Records Administration", "role_en": "Archivist of the United States"},
             bureaus.WHITEHOUSE),
            ({"organization": "美国农业信贷管理局", "role": "理事"}, bureaus.WHITEHOUSE),
            ({"organization_en": "Department of Justice", "role_en": "Special Counsel"}, bureaus.JUSTICE),
        ]
        for role, expected in cases:
            with self.subTest(role=role):
                self.assertEqual(bureaus.classify_role(role)[0], expected)

    def test_chinese_companies(self):
        for role in ({"organization": "华为技术有限公司", "role": "当值轮值董事长"}, {"organization_en": "Huawei Technologies Co., Ltd."},
                     {"organization": "台积电公司"}, {"organization_en": "DJI"}, {"organization_en": "Moonshot AI"}):
            with self.subTest(role=role):
                self.assertTrue(bureaus.chinese_company(role))
        for role in ({"organization_en": "U.S. Embassy in Djibouti"}, {"organization_en": "Anthropic"},
                     {"organization_en": "Racer Trust"}, {"organization": "TikTok美国数据安全合资有限公司"}, {"organization": "美国国务院"}):
            with self.subTest(role=role):
                self.assertFalse(bureaus.chinese_company(role))

    def test_primary_role_skips_committees_and_concurrent(self):
        roles = [{"organization": "参议院预算委员会", "role": "主席", "sector": "committee", "concurrent": True},
                 {"organization": "美国副总统", "role": "", "sector": "politics", "concurrent": False},
                 {"organization": "美国参议院议长", "role": "", "sector": "politics", "concurrent": False}]
        self.assertEqual(bureaus.classify_person(roles)[0], bureaus.WHITEHOUSE)


if __name__ == "__main__":
    unittest.main()
