# -*- coding: utf-8 -*-
"""人事三局导入脚本的测试。

两部分：
- 用虚构字符串测解析器和内置 schema 校验器；
- 用 3 份真实样本（A000055 T2 初稿、N000147 T1 有 FEC 锚点、S000033 T1 无党派）跑完整转换。
  样本在 bureaus/personnel-congress/import/ceo-profiles/，目录不在（公开后移出）时自动跳过。
运行：python -m unittest tests.test_import_ceo_profiles
"""
from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "bureaus" / "personnel-congress" / "import_ceo_profiles.py"
SOURCE = REPO / "bureaus" / "personnel-congress" / "import" / "ceo-profiles"
SCHEMA = REPO / "shared" / "schema" / "bundle.schema.json"
SAMPLES = ["A000055", "N000147", "S000033"]
# 原料公开阶段已移出；维护者电脑上可能只剩个别过程文件，所以看三份样本在不在，不看目录在不在
HAVE_SAMPLES = all((SOURCE / "profiles" / f"{pid}.json").exists() for pid in SAMPLES)
DATE = "2026-10-16"


def load_module():
    spec = importlib.util.spec_from_file_location("import_ceo_profiles", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = load_module()


def minimal_item(**overrides) -> dict:
    """一份字段齐全、内容虚构的 cadreItem。"""
    src = ["s1"]
    item = {
        "id": "cadre-x000001", "line": "cadre", "subject_id": "x000001", "title": "某某简历",
        "sources": [{"id": "s1", "url": "https://example.org/bio", "title": "example.org",
                     "published_at": None, "checked_at": DATE, "excerpt": "", "tier": None}],
        "tags": ["draft", "senate"],
        "person": {
            "name": M.fact("某某", src), "gender": M.fact("男", src), "nationality": M.fact("美国", src),
            "birth_date": M.fact("1960-01-02", src), "birth_place": None,
            "work_start": M.fact("1985", src, True, "按第一份全职工作推算", M.inference("first_full_time_job", "1985")),
            "education": None, "degree": None, "honorary_degree": None,
            "party": M.fact("美国民主党", src), "party_joined_at": None, "work_start_action": "参加工作",
            "ethnicity": None, "academician": [], "military_rank": None, "dropout": None, "death_date": None,
            "status": "在任",
        },
        "current_roles": [{"organization": "美国联邦参议员（某州）", "role": "", "start": None,
                           "concurrent": False, "source_ids": src, "roles": [], "sector": "politics",
                           "appointment_state": "正式"}],
        "careers": [], "appointments": [], "event_date": None, "paragraphs": [],
        "company_tag": "美国参议院", "conflicts": [], "unknowns": [], "excluded": [],
        "country_profile": "us", "card_type": None, "date_granularity": None, "disambiguation": None,
        "review_items": [],
    }
    item.update(overrides)
    return item


def bundle_of(item: dict) -> dict:
    return {"schema_version": "1.0", "date": DATE, "items": [item]}


class MiniValidatorTests(unittest.TestCase):
    def setUp(self):
        self.v = M.MiniValidator(json.loads(SCHEMA.read_text(encoding="utf-8")))

    def test_accepts_minimal_bundle(self):
        self.assertEqual(self.v.validate(bundle_of(minimal_item())), [])

    def test_rejects_additional_property(self):
        item = minimal_item()
        item["person"]["nickname"] = "x"
        errors = self.v.validate(bundle_of(item))
        self.assertTrue(any("不允许的字段 nickname" in e for e in errors), errors)

    def test_rejects_missing_required(self):
        item = minimal_item()
        del item["person"]["work_start_action"]
        errors = self.v.validate(bundle_of(item))
        self.assertTrue(any("缺少必填字段 work_start_action" in e for e in errors), errors)

    def test_rejects_bad_pattern_and_enum(self):
        item = minimal_item()
        item["person"]["birth_date"] = M.fact("1960年1月", ["s1"])
        item["current_roles"][0]["sector"] = "parliament"
        errors = self.v.validate(bundle_of(item))
        self.assertTrue(any("birth_date" in e for e in errors), errors)
        self.assertTrue(any("sector" in e and "枚举" in e for e in errors), errors)

    def test_rejects_wrong_type_and_bad_date(self):
        bundle = bundle_of(minimal_item())
        bundle["date"] = "2026/10/16"
        bundle["items"][0]["tags"] = "draft"
        errors = self.v.validate(bundle)
        self.assertTrue(any("$.date" in e for e in errors), errors)
        self.assertTrue(any("tags" in e and "array" in e for e in errors), errors)


class ParserTests(unittest.TestCase):
    def test_parse_timeline_line(self):
        closed = M.Converter.parse_timeline_line("1992年-1996年 某市市法官")
        self.assertEqual((closed["start"], closed["end"], closed["ongoing"], closed["body"]),
                         (1992, 1996, False, "某市市法官"))
        open_ended = M.Converter.parse_timeline_line("1997年-     某州第4选区联邦众议员")
        self.assertEqual((open_ended["start"], open_ended["end"], open_ended["ongoing"]), (1997, None, True))
        single = M.Converter.parse_timeline_line("2011年 某公司顾问")
        self.assertEqual((single["start"], single["end"], single["ongoing"]), (2011, 2011, False))
        self.assertIsNone(M.Converter.parse_timeline_line("没有年份的一行"))

    def test_parse_study(self):
        s = M.parse_study("某某大学政治学专业学习，获文学学士学位")
        self.assertEqual((s["org"], s["prog"], s["deg"], s["level"], s["parsed"]),
                         ("某某大学", "政治学", "文学学士", "bachelor", True))
        s = M.parse_study("某某大学法学院学习，获法学博士学位")
        self.assertEqual((s["org"], s["prog"], s["deg"], s["level"]), ("某某大学法学院", None, "法学博士", "doctor"))
        s = M.parse_study("甲学院、乙大学学习")
        self.assertEqual((s["org"], s["deg"], s["level"]), ("甲学院、乙大学", None, None))
        s = M.parse_study("某某大学公共管理专业硕士研究生学习")
        self.assertEqual((s["prog"], s["level"]), ("公共管理", "master"))
        s = M.parse_study("某大学学习，获理学硕士学位、护理实践博士学位")
        self.assertEqual((s["deg"], s["level"]), ("理学硕士、护理实践博士", "doctor"))
        self.assertIsNone(M.parse_study("某州律师"))

    def test_compose_degree(self):
        self.assertEqual(M.compose_degree("某大学法学院", "法律", "法学博士（J.D.）"), "某大学法学院法学博士学位")
        self.assertEqual(M.compose_degree("某大学", "历史", "文学学士"), "某大学历史文学学士学位")
        self.assertEqual(M.compose_degree("某大学", "工商管理", "工商管理硕士（M.B.A.）"), "某大学工商管理硕士学位")
        self.assertEqual(M.compose_degree("甲学院、乙学院", "法律、伦理学", "法学博士（J.D.）、伦理学硕士（M.A.）"),
                         "甲学院法学博士学位")
        self.assertEqual(M.compose_degree("某大学法学院", "法律", "法学博士（J.D.，cum laude）"), "某大学法学院法学博士学位")

    def test_clean_name(self):
        self.assertEqual(M.clean_name("某某大学（Some University）"), "某某大学")
        self.assertEqual(M.clean_name("某某学院（Some College，某州某镇）"), "某某学院")
        self.assertEqual(M.clean_name("麻省理工学院（MIT）"), "麻省理工学院（MIT）")
        self.assertEqual(M.clean_name("某法院（第三巡回区，某市）"), "某法院（第三巡回区，某市）")
        self.assertEqual(M.clean_name("法学士（LL.B.）"), "法学士")

    def test_split_role_text_keeps_committee_names_whole(self):
        segs = M.split_role_text("现任某州联邦参议员、参议院农业、营养和林业委员会委员、参议院司法委员会资深成员。")
        self.assertEqual(segs, ["某州联邦参议员", "参议院农业、营养和林业委员会委员", "参议院司法委员会资深成员"])
        segs = M.split_role_text("美国联邦参议员（共和党，某州），美国参议院商业、科学和交通委员会主席")
        self.assertEqual(segs, ["美国联邦参议员（共和党，某州）", "美国参议院商业、科学和交通委员会主席"])

    def test_parse_role_segment(self):
        roles = M.parse_role_segment("拨款委员会委员（国土安全小组委员会主席）", "senate")
        self.assertEqual([(r["sector"], r["organization"], r["role"]) for r in roles], [
            ("committee", "参议院拨款委员会", "委员"),
            ("committee", "参议院拨款委员会国土安全小组委员会", "主席"),
        ])
        roles = M.parse_role_segment("农业、营养与林业委员会（主席）", "senate")
        self.assertEqual((roles[0]["organization"], roles[0]["role"]), ("参议院农业、营养与林业委员会", "主席"))
        roles = M.parse_role_segment("美国众议院多数党党鞭（领导职务）", "house")
        self.assertEqual([(r["sector"], r["organization"], r["role"]) for r in roles],
                         [("politics", "美国众议院多数党党鞭", "")])
        roles = M.parse_role_segment("共和党会议主席", "senate")
        self.assertEqual(roles[0]["organization"], "参议院共和党会议主席")
        roles = M.parse_role_segment("民主党参议员竞选委员会主席", "senate")
        self.assertEqual((roles[0]["organization"], roles[0]["role"]), ("民主党参议员竞选委员会", "主席"))
        roles = M.parse_role_segment("某委员会资深少数党成员", "house")
        self.assertEqual(roles[0]["role"], "资深成员")
        self.assertEqual(M.parse_role_segment("某州第4选区联邦众议员", "house"), [])
        self.assertEqual(M.parse_role_segment("现任职务待核", "house"), [])

    def test_classify_anchor(self):
        self.assertEqual(M.classify_anchor("首次以民主党身份参选：FEC参选声明（F2）受理1990-04-20"), 3)
        self.assertEqual(M.classify_anchor("党籍登记记录：1991-1996年登记为共和党，1996年转登记为民主党"), 1)
        self.assertEqual(M.classify_anchor("就任某州共和党执行主任（党内职务，1989年）"), 2)
        self.assertEqual(M.classify_anchor("2009年1月被州长任命为民主党联邦参议员"), 5)
        self.assertEqual(M.classify_anchor("首次以民主党身份当选某州众议员（2006年11月普选，2007年就任）"), 4)
        self.assertIsNone(M.classify_anchor("长期以无党籍身份从政"))
        self.assertEqual(M.ANCHOR_RULES[M.classify_anchor("2011年登记某某国会竞选委员会")],
                         "party_anchor_candidacy_filing")

    def test_anchor_date(self):
        self.assertEqual(M.Converter._anchor_date("FEC参选声明（F2）受理1990-04-20", 1990), (4, "1990-04-20"))
        self.assertEqual(M.Converter._anchor_date("2018年6月26日某州民主党初选", 2018), (6, "2018-06-26"))
        self.assertEqual(M.Converter._anchor_date("首次当选（2006年11月普选，2007年就任）", 2006), (11, "2006-11"))
        self.assertEqual(M.Converter._anchor_date("1989年任某州共和党执行主任", 1989), (None, None))

    def test_office_organization(self):
        conv = M.Converter(Path(tempfile.gettempdir()) / "no-such-source", DATE)
        office = conv._office_organization
        self.assertEqual(office({"category": "senator", "state": "OH", "district": ""}), "美国联邦参议员（俄亥俄州）")
        self.assertEqual(office({"category": "house", "state": "AL", "district": "4"}), "美国联邦众议员（阿拉巴马州第4选区）")
        self.assertEqual(office({"category": "house", "state": "AK", "district": ""}), "美国联邦众议员（阿拉斯加州）")
        self.assertEqual(office({"category": "delegate", "state": "DC", "district": ""}),
                         "美国国会众议院哥伦比亚特区代表（无表决权）")
        self.assertEqual(office({"category": "house", "state": "PR", "district": ""}),
                         "美国国会众议院波多黎各常驻代表（无表决权）")
        self.assertEqual(office({"category": "president", "state": "", "district": ""}), "美国总统")

    def test_annotation_from_text(self):
        conv = M.Converter(Path(tempfile.gettempdir()) / "no-such-source", DATE)
        ctx = {"unknowns": [], "excluded": []}
        ann = conv._annotation_from_text("2005.01-2009.01任某州众议院议长（第99任）", ["s1"], ctx, "某州众议院")
        self.assertEqual((ann["kind"], ann["start"]["value"], ann["end"]["value"], ann["ongoing"], ann["organization"]),
                         ("part_time", "2005-01", "2009-01", False, "某州众议院议长（第99任）"))
        ann = conv._annotation_from_text("2025- 参议院某某委员会主席", ["s1"], ctx, "x")
        self.assertEqual((ann["start"]["value"], ann["end"], ann["ongoing"]), ("2025", None, True))
        ann = conv._annotation_from_text("2009-2013在某大学法学院学习，获法学博士学位", ["s1"], ctx, "x")
        self.assertEqual((ann["kind"], ann["organization"], ann["degree_awarded"]["value"]),
                         ("study", "某大学法学院", "法学博士"))
        ann = conv._annotation_from_text("约1998-2001任某学区董事会成员", ["s1"], ctx, "x")
        self.assertTrue(ann["start"]["inferred"])
        self.assertIsNone(conv._annotation_from_text("持续经营家庭农场", ["s1"], ctx, "某州众议院"))
        self.assertTrue(any("持续经营家庭农场" in u for u in ctx["unknowns"]))

    def test_date_value(self):
        self.assertEqual(M.date_value(1965, 7, 22), "1965-07-22")
        self.assertEqual(M.date_value(1965, 7), "1965-07")
        self.assertEqual(M.date_value(1965), "1965")
        self.assertEqual(M.date_value(1965, None, 22), "1965")
        self.assertIsNone(M.date_value(None))


@unittest.skipUnless(HAVE_SAMPLES, "原料目录不在仓库里（公开阶段已移出）")
class SampleConversionTests(unittest.TestCase):
    """三份真实样本：A000055（T2 初稿）、N000147（T1，FEC 锚点）、S000033（T1，无党派）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        cls.report = M.run(SOURCE, SCHEMA, out, DATE, SAMPLES, quiet=True)
        cls.out = out
        cls.items = {}
        for pid in SAMPLES:
            bundle = json.loads((out / "bundles" / f"{pid}.json").read_text(encoding="utf-8"))
            cls.items[pid] = bundle["items"][0]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def source_url(self, item, sid):
        return next(s["url"] for s in item["sources"] if s["id"] == sid)

    def test_outputs_and_pass_rate(self):
        self.assertEqual(self.report["summary"]["total"], 3)
        self.assertEqual(self.report["summary"]["valid"], 3)
        self.assertEqual(self.report["summary"]["pass_rate"], "100.0%")
        for name in ("report.md", "report.json", "index.csv"):
            self.assertTrue((self.out / name).exists(), name)
        with open(self.out / "index.csv", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual({r["id"]: r["tier"] for r in rows}, {"A000055": "draft", "N000147": "gold", "S000033": "gold"})
        self.assertTrue(all(r["valid"] == "yes" for r in rows))
        validator = M.MiniValidator(json.loads(SCHEMA.read_text(encoding="utf-8")))
        for pid in SAMPLES:
            bundle = json.loads((self.out / "bundles" / f"{pid}.json").read_text(encoding="utf-8"))
            self.assertEqual(validator.validate(bundle), [], pid)
            self.assertEqual(bundle["date"], DATE)

    def test_t2_draft_aderholt(self):
        item = self.items["A000055"]
        person = item["person"]
        self.assertEqual(item["tags"], ["draft", "house", "state:AL"])
        self.assertEqual(item["company_tag"], "美国众议院")
        self.assertEqual(person["party"]["value"], "美国共和党")
        self.assertIsNone(person["party_joined_at"])
        self.assertTrue(any(u.startswith("入党时间：") for u in item["unknowns"]))
        self.assertTrue(any("1992年8月加入美国共和党" in e for e in item["excluded"]))
        self.assertEqual(person["birth_date"]["value"], "1965-07-22")
        self.assertIsNone(person["birth_place"])
        self.assertEqual(person["work_start"]["value"], "1990")
        self.assertTrue(person["work_start"]["inferred"])
        self.assertEqual(person["work_start"]["inference"]["rule"], "first_full_time_job")
        self.assertEqual(person["degree"]["value"], "桑福德大学坎伯兰法学院法学博士学位")
        self.assertEqual(person["education"]["value"], "研究生学历")
        office = item["current_roles"][0]
        self.assertEqual((office["sector"], office["organization"], office["role"], office["start"]["value"]),
                         ("politics", "美国联邦众议员（阿拉巴马州第4选区）", "", "2025-01"))
        kinds = [c["kind"] for c in item["careers"]]
        self.assertEqual(kinds, ["study", "study", "work", "work", "work", "work"])
        self.assertEqual(item["careers"][1]["degree_awarded"]["value"], "法学博士")
        self.assertEqual(item["careers"][1]["study_level"], "doctor")
        judge = item["careers"][3]
        self.assertEqual((judge["start"]["value"], judge["end"]["value"], judge["organization"], judge["role"]),
                         ("1992", "1996", "阿拉巴马州哈利维尔市市法官", None))
        self.assertEqual(len(judge["annotations"]), 1)
        self.assertEqual((judge["annotations"][0]["start"]["value"], judge["annotations"][0]["end"]["value"]),
                         ("1995", "1996"))
        last = item["careers"][-1]
        self.assertTrue(last["ongoing"])
        self.assertIsNone(last["end"])
        self.assertTrue(all(c["source_ids"] == [] for c in item["careers"]))

    def test_t1_gold_norton(self):
        item = self.items["N000147"]
        person = item["person"]
        self.assertEqual(item["tags"][0], "gold")
        self.assertEqual(person["party"]["value"], "美国民主党")
        pj = person["party_joined_at"]
        self.assertEqual(pj["value"], "1990-04")
        self.assertTrue(pj["inferred"])
        self.assertEqual(pj["inference"], {"rule": "party_anchor_candidacy_filing", "from_date": "1990-04-20",
                                           "duration_years": None})
        self.assertTrue(pj["basis"].startswith("第3级锚点："))
        self.assertIn("api.open.fec.gov", pj["basis"])
        self.assertTrue(any("api.open.fec.gov" in self.source_url(item, sid) for sid in pj["source_ids"]))
        self.assertEqual(person["birth_date"]["value"], "1937-06-13")
        self.assertEqual(person["birth_place"]["value"], "华盛顿哥伦比亚特区")
        self.assertEqual(person["work_start"]["value"], "1964")
        self.assertTrue(any("1964年11月" in e for e in item["excluded"]))
        self.assertEqual(item["current_roles"][0]["organization"], "美国国会众议院哥伦比亚特区代表（无表决权）")
        self.assertEqual(item["current_roles"][0]["start"]["value"], "1991-01-03")
        studies = [c for c in item["careers"] if c["kind"] == "study"]
        self.assertEqual([c["organization"] for c in studies], ["安蒂奥克学院", "耶鲁大学", "耶鲁大学法学院"])
        self.assertTrue(all(not c["start"]["inferred"] for c in studies))
        self.assertTrue(any("邓巴高中" in e and "高中不入履历" in e for e in item["excluded"]))
        aclu = next(c for c in item["careers"] if c["organization"] == "美国公民自由联盟（ACLU）")
        self.assertEqual((aclu["role"], aclu["roles"], aclu["start"]["value"], aclu["end"]["value"]),
                         ("助理法律总监", ["助理法律总监"], "1965", "1970"))
        self.assertTrue(aclu["source_ids"])
        commission = next(c for c in item["careers"] if c["organization"] == "纽约市人权委员会")
        self.assertEqual([a["kind"] for a in commission["annotations"]], ["part_time", "part_time"])
        self.assertTrue(all(a["concurrent"] for a in commission["annotations"]))
        self.assertTrue(item["careers"][-1]["ongoing"])
        self.assertEqual(len(item["conflicts"]), 2)
        self.assertEqual(item["conflicts"][0]["field"], "法律学位名称")
        self.assertTrue(any(u.startswith("底稿缺项：") for u in item["unknowns"]))
        for src in item["sources"]:
            self.assertEqual(src["checked_at"], "2026-06-12")

    def test_t1_gold_independent_sanders(self):
        item = self.items["S000033"]
        person = item["person"]
        self.assertEqual(item["tags"], ["gold", "senate", "state:VT"])
        self.assertEqual(person["party"]["value"], "无党派人士")
        self.assertIsNone(person["party_joined_at"])
        self.assertFalse(any("入党" in u for u in item["unknowns"]))
        committee = next(r for r in item["current_roles"] if r["sector"] == "committee")
        self.assertEqual((committee["organization"], committee["role"], committee["concurrent"], committee["roles"]),
                         ("参议院卫生、教育、劳工和养老金委员会", "资深成员", True, ["资深成员"]))
        self.assertEqual(item["current_roles"][0]["organization"], "美国联邦参议员（佛蒙特州）")
        self.assertEqual(person["degree"]["value"], "芝加哥大学政治学文学学士学位")
        self.assertEqual(person["education"]["value"], "大学学历")

    def test_report_counts_and_t1_list(self):
        pj = self.report["fields"]["person.party_joined_at"]
        self.assertEqual((pj["kept"], pj["empty_no_anchor"], pj["empty_independent"]), (1, 1, 1))
        kept = self.report["review"]["party_joined_at_kept"]
        self.assertEqual([x["id"] for x in kept], ["N000147"])
        self.assertEqual(self.report["review"]["invalid_items"], [])
        t1_ids = M.load_t1_ids(SOURCE)
        self.assertIn("N000147", t1_ids)
        self.assertIn("president", t1_ids)
        self.assertNotIn("A000055", t1_ids)

    def test_deterministic_output(self):
        first = (self.out / "bundles" / "N000147.json").read_bytes()
        with tempfile.TemporaryDirectory() as again:
            M.run(SOURCE, SCHEMA, Path(again), DATE, ["N000147"], quiet=True)
            self.assertEqual((Path(again) / "bundles" / "N000147.json").read_bytes(), first)


if __name__ == "__main__":
    unittest.main()
