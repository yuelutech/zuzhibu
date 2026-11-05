"""一局：线索采集（国会数据对比、名册任职日期）、程序核查与核查放行规则、补采动态入库流程。不联网，不调 Codex。
人物、机构、网页全部虚构。"""

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bureaus.dispatch import collect, research, run
from shared.backends import ai_api
from zuzhibu import bureaus, localupdates, personnel
from zuzhibu.store import Store, read_json, write_json

from tests.fakes.fake_ai_api import FAKE_KEY, FakeApi, openai_response
from tests.test_personnel import _career, _role, make_record


def _event(**kw):
    ev = {"candidate_id": None, "kind": "appoint", "person_id": None, "name_en": "Yuan Bai", "name_zh": "白远",
          "announced_at": None, "effective_at": "2026-09-01", "ended_roles": [],
          "new_roles": [{"organization": "美国联邦众议员（南湖州第13选区）", "role": "", "organization_en": "U.S. House", "role_en": "Representative",
                         "start": "2026-09-01", "end": None, "concurrent": False, "sector": "politics", "source_ids": ["e1", "e2"]}],
          "other_current_roles": None, "new_appointment": None, "new_appointment_checked": False,
          "headline_zh": "白远宣誓就任联邦众议员", "summary_zh": "", "bureau_hint": "congress",
          "sources": [{"id": "e1", "url": "https://example.com/a", "title": "A", "publisher": "A", "published_at": None, "excerpt": "x"},
                      {"id": "e2", "url": "https://example.com/b", "title": "B", "publisher": "B", "published_at": None, "excerpt": "y"}]}
    ev.update(kw)
    return ev


def _review(index=0, **kw):
    r = {"index": index, "decision": "approve", "failed_claims": [], "corrections": [], "verified_source_ids": ["e1", "e2"],
         "other_current_roles_confirmed": False, "new_appointment_confirmed": False, "notes": ""}
    r.update(kw)
    return r


def _corr(field, value, url="https://www.lakeview.gov/news/sworn-in", excerpt="Yuan Bai was sworn in on August 31, 2026."):
    return {"field": field, "value": value, "source_url": url, "source_title": "Sworn in", "source_excerpt": excerpt}


class GateTest(unittest.TestCase):
    def test_approve(self):
        out = research.gate([_event()], {"reviews": [_review()]}, verify_searches=3)
        self.assertEqual(out[0]["status"], "verified")

    def test_no_web_search_holds_everything(self):
        out = research.gate([_event()], {"reviews": [_review()]}, verify_searches=0)
        self.assertEqual(out[0]["status"], "held")

    def test_missing_review_holds(self):
        self.assertEqual(research.gate([_event()], {"reviews": []}, 2)[0]["status"], "held")

    def test_source_only_hold_is_released_with_remaining_source(self):
        review = _review(decision="hold", verified_source_ids=["e2"],
                         failed_claims=[{"field": "sources[0].excerpt", "problem": "PDF 打不开"}])
        out = research.gate([_event()], {"reviews": [review]}, 2)[0]
        self.assertEqual(out["status"], "verified")
        self.assertEqual([s["id"] for s in out["event"]["sources"]], ["e2"])
        self.assertEqual(out["event"]["new_roles"][0]["source_ids"], ["e2"])

    def test_field_failure_without_correction_holds(self):
        review = _review(decision="hold", failed_claims=[{"field": "effective_at", "problem": "应为 7 月 13 日"}])
        self.assertEqual(research.gate([_event()], {"reviews": [review]}, 2)[0]["status"], "held")

    def test_corrected_field_passes_and_is_applied(self):
        review = _review(decision="hold", failed_claims=[{"field": "effective_at", "problem": "应为 2026-08-31"}],
                         corrections=[_corr("effective_at", "2026-08-31"), _corr("new_roles[0].start", "2026-08-31")])
        out = research.gate([_event()], {"reviews": [review]}, 2)[0]
        self.assertEqual(out["status"], "verified")
        self.assertEqual(out["event"]["effective_at"], "2026-08-31")
        self.assertEqual(out["event"]["new_roles"][0]["start"], "2026-08-31")
        cited = [s for s in out["event"]["sources"] if s["id"] == "c1"]
        self.assertEqual(len(cited), 1, "同一链接、同一摘录只记一次")
        self.assertEqual(cited[0]["supports"], ["effective_at", "new_roles[0].start"])
        self.assertIn("c1", out["event"]["new_roles"][0]["source_ids"])

    def test_correction_without_source_is_not_taken(self):
        review = _review(corrections=[{**_corr("effective_at", "2026-08-31"), "source_url": "", "source_excerpt": ""}])
        out = research.gate([_event()], {"reviews": [review]}, 2)[0]
        self.assertEqual(out["event"]["effective_at"], "2026-09-01")
        self.assertIn("没附来源", out["corrections"][0])

    def test_correction_source_must_be_opened_in_the_session(self):
        review = _review(corrections=[_corr("effective_at", "2026-08-31")])
        out = research.gate([_event()], {"reviews": [review]}, 2, opened=["https://example.com/a", "https://example.com/b"])[0]
        self.assertEqual(out["event"]["effective_at"], "2026-09-01")
        self.assertIn("没打开过", out["corrections"][0])
        opened = ["https://example.com/a", "https://example.com/b", "https://lakeview.gov/news/sworn-in"]
        self.assertEqual(research.gate([_event()], {"reviews": [review]}, 2, opened=opened)[0]["event"]["effective_at"], "2026-08-31")

    def test_role_name_correction_needs_a_first_tier_source(self):
        media = _review(corrections=[_corr("new_roles[0].role", "副主席", url="https://news.example.com/x")])
        out = research.gate([_event()], {"reviews": [media]}, 2)[0]
        self.assertEqual(out["event"]["new_roles"][0]["role"], "")
        self.assertIn("一级来源", out["corrections"][0])
        official = _review(corrections=[_corr("new_roles[0].role", "副主席")])
        self.assertEqual(research.gate([_event()], {"reviews": [official]}, 2)[0]["event"]["new_roles"][0]["role"], "副主席")

    def test_correction_cannot_replace_a_whole_list(self):
        review = _review(decision="hold", failed_claims=[{"field": "new_roles", "problem": "职务写法不对"}],
                         corrections=[_corr("new_roles", "联邦众议员")])
        out = research.gate([_event()], {"reviews": [review]}, 2)[0]
        self.assertEqual(out["status"], "held")
        self.assertIsInstance(out["event"]["new_roles"], list)

    def test_only_text_fields_take_text_corrections(self):
        review = _review(corrections=[_corr("other_current_roles", "某协会会长"),   # 眼下是空值的列表字段
                                      _corr("kind", "promote"),                    # 不在类别枚举里
                                      _corr("sources[0].excerpt", "另一句话"),
                                      _corr("announced_at", "2026-08-30"),
                                      _corr("new_roles[0].end", "2030-08-27")])
        out = research.gate([_event()], {"reviews": [review]}, 2)[0]
        self.assertEqual(out["status"], "verified")
        self.assertIsNone(out["event"]["other_current_roles"])
        self.assertEqual(out["event"]["kind"], "appoint")
        self.assertEqual(out["event"]["sources"][0]["excerpt"], "x")
        self.assertEqual((out["event"]["announced_at"], out["event"]["new_roles"][0]["end"]), ("2026-08-30", "2030-08-27"))
        self.assertEqual(len(out["corrections"]), 2)

    def test_role_supported_only_by_dropped_source_holds(self):
        ev = _event()
        ev["new_roles"][0]["source_ids"] = ["e1"]
        out = research.gate([ev], {"reviews": [_review(verified_source_ids=["e2"])]}, 2)[0]
        self.assertEqual(out["status"], "held")

    def test_unconfirmed_retirement_checks_are_reset(self):
        ev = _event(kind="leave", new_roles=[], other_current_roles=[], new_appointment=None, new_appointment_checked=True,
                    ended_roles=[{"organization": "美国联邦参议员（北岸州）", "role": "", "organization_en": "", "role_en": "", "start": None,
                                  "end": None, "concurrent": False, "sector": "politics", "source_ids": ["e1"]}])
        out = research.gate([ev], {"reviews": [_review()]}, 2)[0]
        self.assertEqual(out["status"], "verified")
        self.assertIsNone(out["event"]["other_current_roles"], "核查没确认“没有其他现职”，写回时不能转七局")
        self.assertFalse(out["event"]["new_appointment_checked"])
        confirmed = research.gate([ev], {"reviews": [_review(other_current_roles_confirmed=True, new_appointment_confirmed=True)]}, 2)[0]
        self.assertEqual(confirmed["event"]["other_current_roles"], [])
        self.assertTrue(confirmed["event"]["new_appointment_checked"])

    def test_session_must_open_the_sources_it_verifies(self):
        out = research.gate([_event()], {"reviews": [_review()]}, 3, opened=["http://www.example.com/a/"])[0]
        self.assertEqual(out["status"], "verified")
        self.assertEqual([s["id"] for s in out["event"]["sources"]], ["e1"], "没打开过的 e2 不算核实")
        self.assertEqual(research.gate([_event()], {"reviews": [_review()]}, 3, opened=[])[0]["status"], "held")

    def test_web_stats_counts_pages_read_through_search(self):
        lines = [{"type": "item.completed", "item": {"type": "web_search", "action": {"type": "open_page", "url": "https://example.com/a"}}},
                 {"type": "item.completed", "item": {"type": "web_search", "action": {"type": "search", "queries": ["x"]}, "results": [
                     {"ref_id": "turn1search0", "url": "https://example.com/listed-only", "snippet": "…"},
                     {"ref_id": "turn1view0", "url": "https://example.com/b", "snippet": "Total lines: 31"}]}},
                 {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}}]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "attempt-1.stdout.jsonl"
            path.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
            stats = research.web_stats([mock.Mock(stdout_path=str(path))])
        self.assertEqual(stats["searches"], 2)
        self.assertEqual(stats["opened"], ["https://example.com/a", "https://example.com/b"], "只列在检索结果里的不算打开过")

    def test_research_schema_is_strict(self):
        def walk(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertFalse(node.get("additionalProperties", True))
                    self.assertEqual(sorted(node["required"]), sorted(node["properties"]))
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(research.RESEARCH_SCHEMA)
        walk(research.VERIFY_SCHEMA)


class CollectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)).load()
        rec = make_record("lin-mu", "林木", [_role("美国联邦参议员（南卡罗来纳州）")], [_career("2003", "美国国会参议院", "联邦参议员", ongoing=True)],
                          bureaus.CONGRESS)
        rec["ids"] = {"bioguide": "X000001"}
        self.store.save_person(rec)
        seed = make_record("he-shan", "何山", [_role("青岚科技公司", "首席执行官", "enterprise", "2026-09-01")], [], bureaus.AI, tier="seed")
        seed["roster_roles"] = [{"organization_en": "Qinglan", "role_en": "Chief Executive Officer"}]
        self.store.save_person(seed)

    def tearDown(self):
        self.tmp.cleanup()

    def test_congress_diff(self):
        current = [{"id": {"bioguide": "X000002"}, "name": {"official_full": "Yuan Bai"},
                    "terms": [{"type": "rep", "state": "GA", "district": 13, "start": "2026-09-01", "how": "special-election", "party": "Democrat"}]}]
        historical = [{"id": {"bioguide": "X000001"}, "name": {"official_full": "Mu Lin"},
                       "terms": [{"type": "sen", "state": "SC", "start": "2021-01-03", "end": "2026-07-11"}]}]
        leads = collect.congress_candidates(self.store, current, historical, "2026-08-01", "2026-10-17")
        by_id = {c["id"]: c for c in leads}
        self.assertEqual(by_id["congress-left-x000001"]["person_id"], "lin-mu")
        self.assertEqual(by_id["congress-left-x000001"]["date"], "2026-07-11", "库里在任、数据里已卸任的不论日期都报")
        self.assertEqual(by_id["congress-new-x000002"]["role"]["organization"], "美国联邦众议员（佐治亚州第13选区）")

    def test_roster_dates_in_window(self):
        leads = collect.roster_candidates(self.store, "2026-08-01", "2026-10-17")
        self.assertEqual([c["person_id"] for c in leads], ["he-shan"])
        self.assertEqual(collect.roster_candidates(self.store, "2026-10-01", "2026-10-17"), [])
        rec = self.store.person("he-shan")
        rec["events"] = ["2026-09-01-he-shan-appoint"]
        self.store.save_person(rec)
        self.assertEqual(collect.roster_candidates(self.store, "2026-08-01", "2026-10-17"), [], "已有动态的不再当线索")

    def test_planned_roles_become_leads_when_due(self):
        rec = self.store.person("he-shan")
        rec["planned_roles"] = [{"action": "任", "organization": "青岚科技公司", "role": "执行董事长", "organization_en": "Qinglan",
                                 "role_en": "Executive Chair", "date": "2026-12-01", "event": "2026-09-03-he-shan-announce"}]
        self.store.save_person(rec)
        self.assertEqual(collect.planned_candidates(self.store, "2026-11-30"), [])
        (lead,) = collect.planned_candidates(self.store, "2026-12-01")
        self.assertEqual((lead["person_id"], lead["kind_hint"], lead["date"]), ("he-shan", "appoint", "2026-12-01"))
        self.assertIn("已到期", lead["lead"])

    def test_future_effective_date_dates_the_event_by_its_announcement(self):
        ev = _event(kind="announce", announced_at="2026-09-03", effective_at="2026-12-01")
        ctx = {"leads": [], "today": "2026-10-21", "model": "m", "effort": "e", "run_id": "r", "origin": "daily"}
        self.assertEqual(run.to_record(self.store, ev, {"status": "verified", "corrections": [], "reasons": []}, ctx, {})["date"],
                         "2026-09-03")

    def test_seat_names(self):
        self.assertEqual(collect.seat_zh({"type": "sen", "state": "SC"}), "美国联邦参议员（南卡罗来纳州）")
        self.assertEqual(collect.seat_zh({"type": "rep", "state": "AK", "district": 0}), "美国联邦众议员（阿拉斯加州）")

    def test_prompt_lists_leads_and_scope(self):
        leads = collect.roster_candidates(self.store, "2026-08-01", "2026-10-17")
        text = research.research_prompt(today="2026-10-17", since="2026-08-01", until="2026-10-17", scope=(bureaus.AI,),
                                        leads=leads, people=self.store.people(), min_searches=10)
        self.assertIn("roster-he-shan", text)
        self.assertIn("二局（AI干部局）", text)
        self.assertIn("he-shan｜何山", text)
        self.assertNotIn("lin-mu", text, "AI 组不列四局人物")


NOMINATIONS_URL = "https://www.lakeview.gov/nominations-2026-09-14"
NOMINATIONS_PAGE = """<html><head><title>Nominations Sent to the Senate &#8211; Lakeview Office</title></head>
<body><h1>Nominations Sent to the Senate</h1><p>September&nbsp;14, 2026</p>
<p>Yuan Bai, of Nanhu, to be Deputy Secretary of Commerce, vice Shan O&#8217;Hara, resigned.</p>
<p>Mu Lin, of Beian, to be Ambassador Extraordinary and Plenipotentiary of the United States of America to the Republic of Northshore.</p>
</body></html>"""


def _nomination(**kw):
    ev = _event(kind="nominate", name_en="Yuan Bai", announced_at="2026-09-14", effective_at=None, bureau_hint="whitehouse",
                new_roles=[{"organization": "美国商务部", "role": "常务副部长", "organization_en": "Department of Commerce",
                            "role_en": "Deputy Secretary of Commerce", "start": None, "end": None, "concurrent": False,
                            "sector": "politics", "source_ids": ["e1"]}],
                headline_zh="白远获提名任美国商务部常务副部长",
                sources=[{"id": "e1", "url": NOMINATIONS_URL, "title": "Nominations Sent to the Senate", "publisher": "Lakeview",
                          "published_at": "2026-09-14", "excerpt": "Yuan Bai, of Nanhu, to be Deputy Secretary of Commerce"}])
    ev.update(kw)
    return ev


class DuplicateTest(unittest.TestCase):
    def test_same_announcement_is_a_duplicate_even_if_dates_moved(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)).load()
            store.save_event({"id": "2026-08-21-yuan-bai-confirm", "date": "2026-08-21", "kind": "confirm", "person_id": None,
                              "announced_at": "2026-08-07", "person": {"name_en": "Yuan Bai"}})
            record = {"person_id": None, "person": {"name_en": "Yuan Bai"}, "kind": "confirm", "date": "2026-08-07",
                      "announced_at": "2026-08-07"}
            self.assertEqual(run.duplicate_of(store, record), "2026-08-21-yuan-bai-confirm")
            self.assertIsNone(run.duplicate_of(store, {**record, "announced_at": "2026-07-01", "date": "2026-07-01"}))

    def test_same_day_same_organization_is_a_duplicate_across_kinds(self):
        role = {"organization": "美国国家情报总监办公室", "role": "国家情报总监"}
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp)).load()
            store.save_event({"id": "2026-08-03-yuan-bai-transfer", "date": "2026-08-03", "kind": "transfer", "person_id": "yuan-bai",
                              "person": {"name_en": "Yuan Bai"}, "new_roles": [role], "ended_roles": []})
            record = {"person_id": "yuan-bai", "person": {"name_en": "Yuan Bai"}, "kind": "appoint", "date": "2026-08-03",
                      "announced_at": None, "new_roles": [{**role, "organization": "国家情报总监办公室"}], "ended_roles": []}
            self.assertEqual(run.duplicate_of(store, record), "2026-08-03-yuan-bai-transfer")
            other = {**record, "new_roles": [{"organization": "美国商务部", "role": "部长"}]}
            self.assertIsNone(run.duplicate_of(store, other), "同一天、别的机构不算")
            self.assertIsNone(run.duplicate_of(store, {**record, "date": "2026-08-04"}), "类别不同、日子不同不算")


class PrecheckTest(unittest.TestCase):
    pages = {NOMINATIONS_URL: NOMINATIONS_PAGE}

    def check(self, ev, pages=None):
        return research.precheck(ev, (self.pages if pages is None else pages).get)

    def test_tier1_verbatim_excerpt_with_all_fields_passes(self):
        out = self.check(_nomination())
        self.assertEqual(out["status"], "verified", out["reasons"])
        self.assertEqual(out["method"], "excerpt")
        self.assertEqual(out["event"]["new_roles"][0]["source_ids"], ["e1"])

    def test_unverified_sources_are_dropped_from_a_passing_event(self):
        ev = _nomination()
        ev["sources"].append({"id": "e2", "url": "https://news.example.com/x", "title": "x", "publisher": "x",
                              "published_at": None, "excerpt": "Yuan Bai was nominated"})
        ev["new_roles"][0]["source_ids"] = ["e1", "e2"]
        out = self.check(ev)
        self.assertEqual(out["status"], "verified")
        self.assertEqual([s["id"] for s in out["event"]["sources"]], ["e1"])
        self.assertEqual(out["event"]["new_roles"][0]["source_ids"], ["e1"])
        self.assertIn("e2：不是一级来源", out["notes"])

    def test_source_must_be_tier1_reachable_and_verbatim(self):
        media = _nomination()
        media["sources"][0]["url"] = "https://news.example.com/nominations"
        self.assertEqual(self.check(media, {media["sources"][0]["url"]: NOMINATIONS_PAGE})["status"], "held")
        self.assertEqual(self.check(_nomination(), {})["status"], "held", "网页取不到（403、PDF 等）")
        para = _nomination()
        para["sources"][0]["excerpt"] = "Yuan Bai was nominated to be Deputy Secretary of Commerce"
        out = self.check(para)
        self.assertEqual(out["status"], "held")
        self.assertIn("没有逐字找到", out["reasons"][0])

    def test_excerpt_matching_folds_entities_quotes_and_ellipses(self):
        page = research.page_parts(NOMINATIONS_PAGE)[1]
        self.assertTrue(research.excerpt_in("vice Shan O’Hara, resigned", page))
        self.assertTrue(research.excerpt_in("Yuan Bai, of Nanhu … Deputy Secretary of Commerce", page))
        self.assertFalse(research.excerpt_in("Deputy Secretary of Commerce … Yuan Bai, of Nanhu", page), "各段要依次出现")
        self.assertFalse(research.excerpt_in("Yuan Bai", page), "太短的摘录不算")

    def test_person_role_kind_and_dates_must_be_in_the_source(self):
        cases = {
            "人物": _nomination(name_en="Shan He"),
            "new_roles[0]": _nomination(new_roles=[{**_nomination()["new_roles"][0], "role_en": "Chief of Staff"}]),
            "类别": _nomination(kind="appoint"),  # 提名名单不能当到任的来源
            "announced_at": _nomination(announced_at="2026-09-15"),
        }
        for field, ev in cases.items():
            out = self.check(ev)
            self.assertEqual(out["status"], "held", field)
            self.assertTrue(any(r.startswith(field) for r in out["reasons"]), (field, out["reasons"]))

    def test_event_without_roles_is_held(self):
        self.assertIn("没有原职务也没有新职务", self.check(_nomination(kind="announce", new_roles=[]))["reasons"])

    def test_leave_retirement_checks_are_reset(self):
        url = "https://www.lakeview.gov/news/bai-departs"
        page = ("<title>Deputy Secretary Yuan Bai Departs</title><p>Deputy Secretary of Commerce Yuan Bai resigned "
                "effective October 2, 2026.</p>")
        role = {**_nomination()["new_roles"][0], "end": "2026-10-02"}
        ev = _nomination(kind="leave", announced_at=None, effective_at="2026-10-02", new_roles=[], ended_roles=[role],
                         other_current_roles=[], new_appointment=None, new_appointment_checked=True,
                         sources=[{"id": "e1", "url": url, "title": "", "publisher": "", "published_at": None,
                                   "excerpt": "Deputy Secretary of Commerce Yuan Bai resigned"}])
        out = self.check(ev, {url: page})
        self.assertEqual(out["status"], "verified", out["reasons"])
        self.assertIsNone(out["event"]["other_current_roles"], "程序核不了“没有其他现职”，写回时不能转七局")
        self.assertFalse(out["event"]["new_appointment_checked"])

    def test_dates_in_common_formats(self):
        text = research.fold("Sworn in Sept. 2, 2026; announced 14 September 2026; filed 2026-10-05; term began 6/30/2026.")
        for value in ("2026-09-02", "2026-09-14", "2026-10-05", "2026-06-30", "2026-09", "2026"):
            self.assertTrue(research.date_in(value, text), value)
        for value in ("2026-09-01", "2026-09-20", "2026-11", "2025"):
            self.assertFalse(research.date_in(value, text), value)

    def test_surname_drops_suffixes(self):
        self.assertEqual(research.surname("Charles Goodman III"), "goodman")
        self.assertEqual(research.surname("Henry Samueli, Ph.D."), "samueli")
        self.assertEqual(research.surname("Shan O’Hara Jr."), "o'hara")


class InboxRunTest(unittest.TestCase):
    """补采动态入库：先剔除重复和无职务变动的，程序核查过的直接写回，没过的一次一条开核查会话。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data, self.runs, self.inbox = root / "data", root / "runs", root / "events.json"
        store = Store(self.data).load()
        store.save_event({"id": "2026-09-14-mu-lin-nominate", "date": "2026-09-14", "kind": "nominate", "person_id": None,
                          "person": {"name_zh": "林木", "name_en": "Mu Lin"}})
        store.save_meta({"daily": {"last_run": "2026-10-20T08:03:23", "last_status": "完成"}})
        blocked = {"id": "e1", "url": "https://www.lakeview.gov/blocked", "title": "", "publisher": "", "published_at": None,
                   "excerpt": "was sworn in as Under Secretary of Commerce"}
        role = {**_nomination()["new_roles"][0], "role": "副部长", "role_en": "Under Secretary of Commerce", "start": "2026-10-01"}
        self.events = [
            _nomination(),                                                                 # 程序核查放行
            _nomination(name_en="Mu Lin", headline_zh="林木获提名任驻北岸国大使"),            # 库里已有
            _nomination(kind="announce", name_en="Shan He", new_roles=[], headline_zh="白宫撤回何山的提名"),  # 没有职务变动
            _nomination(name_en="Shan He", announced_at="2026-08-07", headline_zh="何山获提名任美国商务部常务副部长"),  # 之后撤回了
            _nomination(kind="announce", name_en="Lin Mu", new_roles=[], ended_roles=[_nomination()["new_roles"][0]],
                        headline_zh="林木宣布将卸任美国商务部常务副部长"),                   # 宣布类只有原职务
            _nomination(kind="appoint", name_en="Qing Feng", effective_at="2026-10-01", new_roles=[role], sources=[blocked],
                        headline_zh="冯青就任美国商务部副部长"),                            # 网页取不到，核查会话打开过
            _nomination(kind="appoint", name_en="Hai Lan", effective_at="2026-10-01", new_roles=[role],
                        sources=[{**blocked, "url": "https://www.lakeview.gov/blocked-2"}],
                        headline_zh="兰海就任美国商务部副部长"),                            # 核查会话说核实了，却没打开过
        ]
        write_json(self.inbox, {"events": self.events, "checked_candidates": [], "notes": ""})
        self.prompts = []

    def tearDown(self):
        self.tmp.cleanup()

    def fake_call(self, prompt, schema, config, runs, label):
        self.prompts.append(prompt)
        opened = ["https://lakeview.gov/blocked"] if "Qing Feng" in prompt else []
        review = {"reviews": [{"index": 0, "decision": "approve", "failed_claims": [], "corrections": [],
                               "verified_source_ids": ["e1"], "other_current_roles_confirmed": False,
                               "new_appointment_confirmed": False, "notes": ""}]}
        return review, {"searches": 2, "opened": opened, "input_tokens": 0, "output_tokens": 0, "errors": []}, None

    def test_inbox_flow(self):
        with mock.patch.object(research, "call", self.fake_call), \
                mock.patch.object(research, "fetch_page", lambda url, timeout=20.0: {NOMINATIONS_URL: NOMINATIONS_PAGE}.get(url)):
            code = run.main(["--inbox", str(self.inbox), "--data", str(self.data), "--runs", str(self.runs),
                             "--codex", sys.executable, "--date", "2026-10-20", "--jobs", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.prompts), 2, "只有程序核查没过的两条开了核查会话")
        self.assertTrue(all('"index": 0' in p and '"index": 1' not in p for p in self.prompts), "一次一条")
        store = Store(self.data).load()
        written = {e["person"]["name_en"]: e for e in store.events() if e.get("origin") == "codex-inbox"}
        self.assertEqual(sorted(written), ["Qing Feng", "Yuan Bai"])
        self.assertEqual(written["Yuan Bai"]["verification"]["method"], "excerpt")
        self.assertIsNone(written["Yuan Bai"]["verification"]["model"])
        self.assertEqual(written["Qing Feng"]["verification"]["method"], "codex")
        self.assertTrue(written["Qing Feng"]["verification"]["precheck"], "记下程序核查没过的原因")
        (run_dir,) = [p for p in self.runs.iterdir() if p.is_dir()]
        held = {(h["event"]["name_en"], h["event"]["kind"]): h for h in read_json(run_dir / "held.json")}
        self.assertEqual(sorted(held), [("Hai Lan", "appoint"), ("Lin Mu", "announce"), ("Shan He", "announce"),
                                        ("Shan He", "nominate")])
        self.assertIn("没有可写回的职务", held[("Shan He", "announce")]["reasons"][0])
        self.assertIn("只有原职务", held[("Lin Mu", "announce")]["reasons"][0])
        self.assertIn("2026-09-14 撤回", held[("Shan He", "nominate")]["reasons"][0])
        self.assertIn("没有核实的来源", held[("Hai Lan", "appoint")]["reasons"])
        report = (run_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("已有 2026-09-14-mu-lin-nominate", report)
        meta = read_json(self.data / "meta.json")
        self.assertEqual(meta["daily"]["last_run"], "2026-10-20T08:03:23", "补采入库不顶替每日更新状态")
        self.assertEqual(meta["inbox"]["last_applied"], 2)


class MonthlyRunTest(unittest.TestCase):
    """每月更新：研究会话的动态也先剔除、再程序核查，过不了的一次一条开核查会话；--recheck 重核已入库的动态。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data, self.runs = root / "data", root / "runs"
        store = Store(self.data).load()
        store.save_meta({"daily": {"last_run": "2026-10-20T08:00:00", "last_window": ["2026-10-17", "2026-10-20"]}})
        role = {**_nomination()["new_roles"][0], "role": "副部长", "role_en": "Under Secretary of Commerce", "start": "2026-10-01"}
        self.events = [_nomination(),
                       _nomination(kind="appoint", name_en="Qing Feng", effective_at="2026-10-01", new_roles=[role],
                                   sources=[{"id": "e1", "url": "https://www.lakeview.gov/blocked", "title": "", "publisher": "",
                                             "published_at": None, "excerpt": "was sworn in as Under Secretary of Commerce"}],
                                   headline_zh="冯青就任美国商务部副部长")]
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def fake_call(self, prompt, schema, config, runs, label):
        self.calls.append((label, prompt))
        stats = {"searches": 2, "opened": ["https://lakeview.gov/blocked"], "input_tokens": 0, "output_tokens": 0, "errors": []}
        if label.endswith("-research"):
            return {"events": copy.deepcopy(self.events), "checked_candidates": [], "notes": ""}, {**stats, "searches": 30}, None
        review = {"reviews": [{"index": 0, "decision": "approve", "failed_claims": [], "corrections": [],
                               "verified_source_ids": ["e1"], "other_current_roles_confirmed": False,
                               "new_appointment_confirmed": False, "notes": ""}]}
        return review, stats, None

    def run_main(self, *args):
        pages = {NOMINATIONS_URL: NOMINATIONS_PAGE}
        with mock.patch.object(research, "call", self.fake_call), \
                mock.patch.object(research, "fetch_page", lambda url, timeout=20.0: pages.get(url)), \
                mock.patch.object(collect, "collect", lambda *a, **k: {"leads": [], "errors": []}):
            return run.main([*args, "--data", str(self.data), "--runs", str(self.runs), "--codex", sys.executable,
                             "--date", "2026-10-21", "--jobs", "1"])

    def test_monthly_update_verifies_one_event_per_session(self):
        self.assertEqual(self.run_main("--since", "2026-09-01", "--until", "2026-10-21", "--groups", "gov"), 0)
        verify = [p for label, p in self.calls if label.endswith("-verify")]
        self.assertEqual(len(verify), 1, "程序核查过了的不开会话，没过的一条一个会话")
        self.assertIn("Qing Feng", verify[0])
        self.assertNotIn('"index": 1', verify[0])
        written = {e["person"]["name_en"]: e["verification"] for e in Store(self.data).load().events()}
        self.assertEqual(written["Yuan Bai"]["method"], "excerpt")
        self.assertEqual(written["Qing Feng"]["method"], "codex")
        self.assertIn("一次一条", written["Qing Feng"]["by"])

    def test_recheck_updates_verification_without_rewriting(self):
        self.assertEqual(self.run_main("--since", "2026-09-01", "--until", "2026-10-21", "--groups", "gov"), 0)
        store = Store(self.data).load()
        target = next(e for e in store.events() if e["person"]["name_en"] == "Qing Feng")
        person_before = store.person(target["person_id"])
        self.calls.clear()
        self.assertEqual(self.run_main("--recheck", target["id"]), 0)
        self.assertEqual(len(self.calls), 1, "只为这一条开一个核查会话")
        store = Store(self.data).load()
        v = store.event(target["id"])["verification"]
        self.assertEqual((v["method"], v["rechecked"]["previous"]["method"]), ("codex", "codex"))
        self.assertEqual(store.person(target["person_id"]), person_before, "重核不重新写回")
        self.fake_call = lambda *a, **k: ({"reviews": [{"index": 0, "decision": "approve", "failed_claims": [], "corrections": [],
                                                         "verified_source_ids": ["e1"], "other_current_roles_confirmed": False,
                                                         "new_appointment_confirmed": False, "notes": ""}]},
                                          {"searches": 1, "opened": [], "input_tokens": 0, "output_tokens": 0, "errors": []}, None)
        self.assertEqual(self.run_main("--recheck", target["id"]), 0)
        (run_dir,) = sorted(p for p in self.runs.iterdir() if p.is_dir())[-1:]
        self.assertIn("没通过", (run_dir / "report.md").read_text(encoding="utf-8"), "会话没打开过来源的不通过")
        self.assertEqual(Store(self.data).load().event(target["id"])["verification"]["rechecked"]["at"], "2026-10-21",
                         "没通过的不改原核查记录")


class UserApiRunTest(unittest.TestCase):
    """使用者自己的 AI 接口跑一局更新：检测联网检索 → 研究 → 核查 → 放行写回；写回另记一份；密钥不进任何文件。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data, self.runs, self.journal, self.settings = root / "data", root / "runs", root / "journal", root / "settings.json"
        store = Store(self.data).load()
        rec = make_record("lin-mu", "林木", [_role("美国商务部", "副部长", "politics", "2025-03")],
                          [_career("2025-03", "美国商务部", "副部长", ongoing=True)], bureaus.WHITEHOUSE)
        rec["name_en"] = "Mu Lin"
        store.save_person(rec)
        store.save_meta({"daily": {"last_run": "2026-10-21T08:51:42", "last_window": ["2026-10-18", "2026-10-21"]}})
        write_json(self.settings, {"api": {"provider": "openai", "api_key": FAKE_KEY}})
        moved = {"organization": "美国内政部", "role": "副部长", "organization_en": "Department of the Interior",
                 "role_en": "Deputy Secretary of the Interior", "start": "2026-10-22", "end": None, "concurrent": False,
                 "sector": "politics", "source_ids": ["e1"]}
        old = {**moved, "organization": "美国商务部", "organization_en": "Department of Commerce", "role_en": "Deputy Secretary of Commerce",
               "start": None, "end": "2026-10-22"}
        self.research = {"events": [
            _event(kind="transfer", person_id="lin-mu", name_en="Mu Lin", name_zh="林木", effective_at="2026-10-22",
                   ended_roles=[old], new_roles=[moved], headline_zh="林木任美国内政部副部长", bureau_hint="whitehouse"),
            _event(name_en="Yuan Bai", effective_at="2026-10-23", headline_zh="白远任美国能源部副部长", bureau_hint="whitehouse"),
        ], "checked_candidates": [], "notes": ""}
        self.hold = _review(0, decision="hold", failed_claims=[{"field": "effective_at", "problem": "来源写的是 10 月 30 日"}])

    def tearDown(self):
        self.tmp.cleanup()

    def handler(self, call):
        text = (call["body"] or {}).get("input", "")
        if "whitehouse.gov" in text:
            return openai_response("白宫新闻标题")
        if "独立核查员" in text:  # 一次核一条：白远的不过，林木的过；会话里打开过两条来源
            review = {"reviews": [self.hold if "Yuan Bai" in text else _review(0)]}
            return openai_response(json.dumps(review, ensure_ascii=False), searches=4,
                                   opened=("https://example.com/a", "https://example.com/b"))
        found = self.research if "三局（白宫干部局）" in text else {"events": [], "checked_candidates": [], "notes": ""}
        return openai_response(json.dumps(found, ensure_ascii=False), searches=16)

    def run_api(self, handler=None):
        fake = FakeApi(handler or self.handler)
        with mock.patch.object(ai_api, "urllib_transport", fake), \
                mock.patch.object(collect, "collect", lambda store, since, until, cache=None: {"since": since, "until": until, "leads": [], "errors": []}), \
                mock.patch.object(research, "fetch_page", lambda url, timeout=20.0: None):
            code = run.main(["--backend", "api", "--since-last", "--settings", str(self.settings), "--journal", str(self.journal),
                             "--data", str(self.data), "--runs", str(self.runs), "--date", "2026-10-24", "--jobs", "1"])
        return code, fake

    def test_update_with_users_own_api(self):
        code, fake = self.run_api()
        self.assertEqual(code, 0)
        store = Store(self.data).load()
        (ev,) = [e for e in store.events() if e.get("origin") == "user-api"]
        self.assertEqual(ev["person_id"], "lin-mu")
        self.assertEqual(ev["verification"]["method"], "api")
        self.assertIn("使用者自己的 AI 接口（OpenAI 兼容接口）", ev["verification"]["by"])
        self.assertEqual(ev["applied"]["status"], personnel.APPLIED)
        self.assertEqual([r["organization"] for r in store.person("lin-mu")["resume"]["current_roles"]], ["美国内政部"])
        (run_dir,) = [p for p in self.runs.iterdir() if p.is_dir()]
        held = read_json(run_dir / "held.json")
        self.assertEqual([h["event"]["name_en"] for h in held], ["Yuan Bai"], "核查不过的搁置，不写回")
        self.assertIsNone(store.person("yuan-bai"))
        journal = localupdates.Journal(self.journal)
        self.assertEqual([e["id"] for e in journal.events()], [ev["id"]], "写回的另记一份，供维护者数据换新后重放")
        self.assertEqual(journal.state()["last_window"], ["2026-10-20", "2026-10-24"], "窗口从上次截止日接着查")
        meta = read_json(self.data / "meta.json")
        self.assertEqual(meta["daily"]["last_window"], ["2026-10-18", "2026-10-21"], "不顶替维护者的每日更新状态")
        self.assertEqual((meta["user_update"]["last_applied"], meta["user_update"]["last_held"]), (1, 1))
        probe, *sessions = fake.calls
        self.assertFalse(probe["body"].get("background"), "先检测联网检索")
        self.assertEqual(len(sessions), 7, "五栏各一次研究，加两条动态各开一次核查（程序核查取不到网页）")
        self.assertTrue(all(c["body"]["text"]["format"]["strict"] for c in sessions))
        self.assertTrue(all(c["headers"]["Authorization"] == "Bearer " + FAKE_KEY for c in fake.calls))
        self.assertIn("2026-10-20 至 2026-10-24", sessions[0]["body"]["input"])
        for path in Path(self.tmp.name).rglob("*"):
            if path.is_file() and path != self.settings:
                self.assertNotIn(FAKE_KEY, path.read_text(encoding="utf-8", errors="replace"), path)

    def test_interface_without_web_search_cannot_update(self):
        write_json(self.settings, {"api": {"provider": "other", "api_key": FAKE_KEY}})
        code, fake = self.run_api()
        self.assertEqual(code, 2)
        self.assertEqual(fake.calls, [], "不联网、不花额度")
        (run_dir,) = [p for p in self.runs.iterdir() if p.is_dir()]
        self.assertIn("没有联网检索工具", (run_dir / "run.log").read_text(encoding="utf-8"))

    def test_bad_key_stops_before_research(self):
        code, fake = self.run_api(lambda call: (401, {"error": {"message": "Incorrect API key provided"}}))
        self.assertEqual(code, 2)
        self.assertEqual(len(fake.calls), 1, "检测没过就不开研究会话")
        self.assertEqual(Store(self.data).load().events(), [])

    def test_window_from_last_cutoff(self):
        self.assertEqual(run.last_cutoff({"daily": {"last_window": ["2026-09-01", "2026-10-01"]},
                                          "user_update": {"last_window": ["2026-10-01", "2026-10-15"]}}, None), "2026-10-15")
        self.assertEqual(run.window_days("2026-10-15", "2026-10-24"), 10)
        self.assertEqual(run.window_days(None, "2026-10-24"), 31)
        self.assertEqual(run.window_days("2026-01-01", "2026-10-24"), 62)


class NotCollectedTest(unittest.TestCase):
    def test_chinese_companies_are_not_collected_in_any_group(self):
        store = mock.Mock()
        huawei = {"person_id": "lin-mu", "new_roles": [{"organization": "华为技术有限公司", "role": "当值轮值董事长"}], "ended_roles": []}
        for group in ("ai", "whitehouse"):
            self.assertEqual(run.not_collected(huawei, store, group), "中国企业不收（维护者 2026-10-23 定）")
        other = {"person_id": "lin-mu", "new_roles": [{"organization": "北岸科技公司", "role": "首席执行官"}], "ended_roles": []}
        self.assertIsNone(run.not_collected(other, store, "ai"), "在库人物的职务变动照收")
        store.people.assert_not_called()


if __name__ == "__main__":
    unittest.main()
