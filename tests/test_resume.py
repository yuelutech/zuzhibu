"""简历生成（2026-10-17 简历字段纪律）：首段新句式、状态词、逝世与曾任、日期粒度、完整简历卡门槛。人物、机构全部虚构。"""

import copy
import unittest

from tools.lint import cadre_lint
from zuzhibu import bureaus, resume

from tests.test_personnel import _career, _fact, _role, make_record


def _person_record():
    rec = make_record("lin-zhao", "林昭丹",
                      [_role("美国联邦参议员（海岸州）", start="2011-01"), _role("参议院灯塔委员会", "委员", "committee", None, True)],
                      [_career("2011-01", "美国国会参议院", "联邦参议员（海岸州）", ongoing=True),
                       _career("1996-06", "海岸州律师事务所", "律师", "2004-12")],
                      bureaus.CONGRESS, tier="gold")
    p = rec["resume"]["person"]
    p["gender"] = _fact("女")
    p["birth_date"] = _fact("1968-11")
    p["birth_place"] = _fact("海岸州新港市")
    p["work_start"] = _fact("1996-06")
    p["ethnicity"] = _fact("亚裔")
    p["party"] = _fact("美国民主党")
    p["party_joined_at"] = _fact("2004-03")
    p["degree"] = _fact("海港大学法学院法学博士")
    rec["resume"]["country_profile"] = "us"
    return rec


class LeadTest(unittest.TestCase):
    def test_new_sentence_order(self):
        lead = resume.compose(_person_record())["doc"]["lead"]
        self.assertEqual(lead, "林昭丹，女，亚裔，美国籍，1968年11月出生，海岸新港人，1996年6月参加工作，2004年3月加入美国民主党，"
                               "海港大学法学院法学博士学位。")

    def test_birthplace_writes_a_name_shared_by_state_and_city_once(self):
        for place, text in (("海岸州新港市", "海岸新港人"), ("海岸州海岸市", "海岸人"), ("海岸州海岸市灯塔区", "海岸灯塔区人")):
            person = {"birth_place": _fact(place), "nationality": _fact("美国籍")}
            self.assertEqual(resume.birthplace_text(person, "us"), text, place)

    def test_ethnicity_only_for_us_and_only_five_words(self):
        rec = _person_record()
        rec["resume"]["country_profile"] = "other"
        self.assertNotIn("亚裔", resume.compose(rec)["doc"]["lead"])
        rec = _person_record()
        rec["resume"]["person"]["ethnicity"] = _fact("华裔")
        self.assertNotIn("华裔", resume.compose(rec)["doc"]["lead"])

    def test_identity_items_after_degree(self):
        rec = _person_record()
        rec["resume"]["person"]["academician"] = [_fact("美国国家工程院")]
        rec["resume"]["person"]["military_rank"] = _fact("上将")
        lead = resume.compose(rec)["doc"]["lead"]
        self.assertTrue(lead.endswith("法学博士学位，美国国家工程院院士，上将军衔。"), lead)

    def test_dropout_when_no_degree(self):
        rec = _person_record()
        rec["resume"]["person"]["degree"] = None
        rec["resume"]["person"]["dropout"] = _fact("海港大学")
        self.assertIn("海港大学肄业。", resume.compose(rec)["doc"]["lead"])

    def test_former_and_deceased(self):
        rec = _person_record()
        item = rec["resume"]
        item["current_roles"] = []
        item["careers"][0].update(end=_fact("2026-07-11"), ongoing=False)
        item["person"]["death_date"] = _fact("2026-07-11")
        lead = resume.compose(rec)["doc"]["lead"]
        self.assertTrue(lead.startswith("林昭丹（1968年11月－2026年7月），女，"), lead)
        self.assertNotIn("出生", lead)
        self.assertTrue(lead.endswith("曾任美国国会参议院联邦参议员（海岸州）。"), lead)

    def test_current_paragraph_with_states(self):
        rec = _person_record()
        rec["resume"]["current_roles"].append({**_role("美国商务部", "部长"), "appointment_state": "提名人选"})
        current = resume.compose(rec)["doc"]["current"]
        self.assertEqual(current, "现任美国联邦参议员（海岸州）、美国商务部部长提名人选、参议院灯塔委员会委员。")
        self.assertEqual(resume.stated("美国驻灯塔国", "大使", "拟任"), "拟任美国驻灯塔国大使")
        self.assertEqual(resume.stated("青岚公司", "首席执行官", "临时"), "青岚公司临时首席执行官")
        self.assertEqual(resume.stated("美国司法部", "代理常务副部长", "代理"), "美国司法部代理常务副部长")

    def test_officials_current_paragraph_lists_only_public_offices(self):
        """有公职的人现任段只写公职：企业职务、基金会一类社团职务、民间顾问职务不写（数据里照留）；挂在白宫下的特使算公职。"""
        rec = _person_record()
        rec["resume"]["current_roles"] += [_role("灯塔湾俱乐部有限责任公司", "总裁", "enterprise"),
                                           _role("海岸国家公园基金会", "董事", "committee", None, True),
                                           _role("白宫", "海岸事务特使", "advisory"),
                                           _role("海港律师事务所", "资深顾问", "advisory")]
        doc = resume.compose(rec)["doc"]
        self.assertEqual(doc["current"], "现任美国联邦参议员（海岸州）、白宫海岸事务特使、参议院灯塔委员会委员。")
        self.assertNotIn("现任", doc["lead"])
        self.assertEqual(len(rec["resume"]["current_roles"]), 6, "数据里的职务不动")

    def test_business_people_keep_company_roles_in_the_lead(self):
        rec = _person_record()
        rec["resume"]["current_roles"] = [_role("青岚科技公司", "首席执行官", "enterprise"),
                                          _role("青岚科技公司", "审计委员会委员", "committee", None, True)]
        doc = resume.compose(rec)["doc"]
        self.assertTrue(doc["lead"].endswith("现任青岚科技公司首席执行官、青岚科技公司审计委员会委员。"), doc["lead"])
        self.assertNotIn("current", doc, "公司的委员会不算公职")

    def test_nominee_without_a_seated_office_keeps_company_roles(self):
        rec = _person_record()
        rec["resume"]["current_roles"] = [_role("青岚科技公司", "首席执行官", "enterprise"),
                                          {**_role("美国驻灯塔国大使馆", "大使"), "appointment_state": "提名人选"}]
        doc = resume.compose(rec)["doc"]
        self.assertEqual(doc["current"], "现任美国驻灯塔国大使馆大使提名人选、青岚科技公司首席执行官。")
        self.assertNotIn("现任", doc["lead"])


class CareerTest(unittest.TestCase):
    def test_sorting_same_start_earlier_end_first(self):
        rec = _person_record()
        rec["resume"]["careers"] = [_career("2011-01", "灯塔公司", "董事", ongoing=True),
                                    _career("2011-01", "海岸州州长办公室", "助理", "2012-03")]
        periods = [e["period"] for e in resume.compose(rec)["doc"]["careers"]]
        self.assertEqual(periods, ["2011年1月－2012年3月", "2011年1月至今"])

    def test_granularity_year_when_many_missing(self):
        rec = _person_record()
        rec["resume"]["careers"] = [_career(str(1990 + i), "灯塔公司", "职员", str(1991 + i)) for i in range(5)] + \
                                   [_career("1996-06", "灯塔公司", "总裁", ongoing=True)]
        composed = resume.compose(rec)
        self.assertEqual(composed["date_granularity"], "year")
        self.assertEqual(composed["doc"]["careers"][-1]["period"], "1996年至今")

    def test_study_with_degree_and_single_year(self):
        rec = _person_record()
        study = _career("1986-09", "海港大学", None, "1990-05")
        study.update(kind="study", program="政治学", degree_awarded=_fact("文学学士"))
        same_year = _career("2005", "灯塔公司", "顾问", "2005")
        rec["resume"]["careers"] = [study, same_year]
        entries = resume.compose(rec)["doc"]["careers"]
        self.assertEqual(entries[0]["role"], "海港大学政治学专业学习，获文学学士学位")
        self.assertEqual(entries[1]["period"], "2005年")

    def test_composed_text_passes_new_lint_rules(self):
        rec = _person_record()
        text = resume.compose(rec)["text"]
        found = {v.code for v in cadre_lint.lint_text(text, select=["CR10", "CR12", "CR13", "CR14", "CR15"]) if v.level == "error"}
        self.assertEqual(found, set(), text)


class ThresholdTest(unittest.TestCase):
    def test_full_card(self):
        kind, reasons = resume.card_type(_person_record())
        self.assertEqual((kind, reasons), (resume.FULL_CARD, []))
        self.assertTrue(resume.compose(_person_record())["exportable"])

    def test_missing_years_or_unsourced(self):
        rec = _person_record()
        rec["resume"]["person"]["work_start"] = {"value": "1996", "source_ids": [], "inferred": True,
                                                 "basis": "T2 初稿时间线，无来源", "inference": {"rule": "first_full_time_job",
                                                                                         "from_date": "1996", "duration_years": None}}
        kind, reasons = resume.card_type(rec)
        self.assertIsNone(kind)
        self.assertIn("参加工作年没有来源或可推依据", reasons)
        self.assertFalse(resume.compose(rec)["exportable"])

    def test_current_role_needs_tier_one_source(self):
        rec = _person_record()
        for src in rec["resume"]["sources"]:
            src["tier"] = 2
        self.assertIn("现任职务没有一级来源", resume.card_type(rec)[1])

    def test_annotate_stores_derived_fields(self):
        rec = copy.deepcopy(_person_record())
        resume.annotate(rec)
        self.assertEqual(rec["resume"]["card_type"], resume.FULL_CARD)
        self.assertEqual(rec["resume"]["date_granularity"], "month")


if __name__ == "__main__":
    unittest.main()
