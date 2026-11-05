# -*- coding: utf-8 -*-
"""tools/lint/cadre_lint.py 的单元测试：每条规则都有通过与不通过的样例，外加命令行。

运行：python -m unittest tests.test_cadre_lint
样本全是虚构人名和机构，见 tests/fixtures/cadre_lint/。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tools.lint import cadre_lint as cl

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "cadre_lint"
TEST_GLOSSARY = FIXTURES / "glossary"
REAL_GLOSSARY = REPO / "shared" / "glossary"

GLOSSARY = None


def setUpModule():
    global GLOSSARY
    GLOSSARY = cl.load_glossary(TEST_GLOSSARY)


def run(text, **kwargs):
    kwargs.setdefault("glossary", GLOSSARY)
    return cl.lint_text(text, **kwargs)


def codes(text, **kwargs):
    return {v.code for v in run(text, **kwargs) if v.level == "error"}


def messages(text, **kwargs):
    return [v.message for v in run(text, **kwargs) if v.level == "error"]


PASS_FIRST_PARA = "林昭丹，女，1968年11月出生，美国人，1990年参加工作，2004年3月加入美国民主党，海港大学法学博士学位。"
PASS_ENTRY = "1997年－2003年\nNorthwind Labs 公司工程师、芯片事业部总监（其间：1998年－2002年在海港大学电气工程专业在职研究生学习，获理学硕士学位）\n"


class NarrativeTest(unittest.TestCase):  # CR01
    def test_fail_core_words(self):
        for text in ("1992年－1995年\n在蓝鳍半导体公司工作", "蓝鳍半导体公司工程师，负责芯片业务",
                     "先后任工程师、总监", "从事半导体研究", "职业生涯始于海港大学"):
            self.assertIn("CR01", codes(text), text)

    def test_fail_biographic_words(self):
        self.assertIn("CR01", codes("毕业后进入蓝鳍半导体公司"))
        self.assertIn("CR01", codes("曾任蓝鳍半导体公司总监"))

    def test_fail_join_not_party(self):
        self.assertIn("CR01", codes("2005年加入蓝鳍半导体公司"))

    def test_pass_fixed_phrases(self):
        self.assertNotIn("CR01", codes(PASS_FIRST_PARA))
        self.assertNotIn("CR01", codes("项目负责人"))
        self.assertNotIn("CR01", codes("蓝鳍半导体公司工作人员"))
        self.assertNotIn("CR01", codes("2004年3月加入美国民主党"))
        self.assertNotIn("CR01", codes(PASS_ENTRY))


class QijianTest(unittest.TestCase):  # CR02
    def test_fail_qijian(self):
        self.assertIn("CR02", codes("（期间：1998年－2002年在海港大学在职研究生学习）"))
        self.assertIn("CR02", codes("任职期间"))

    def test_fail_qijian_format(self):
        self.assertIn("CR02", codes("（其间，1998年－2002年在海港大学学习）"))
        self.assertIn("CR02", codes("其间：1998年－2002年在海港大学学习"))

    def test_pass(self):
        self.assertNotIn("CR02", codes(PASS_ENTRY))


class DateTest(unittest.TestCase):  # CR03
    BAD = (
        "1985年-1993年\n海港大学学习", "1985-1993\n海港大学学习", "1985年—1993年\n海港大学学习",
        "1985年至1993年\n海港大学学习", "1985年 － 1993年\n海港大学学习", "1985－1993年\n海港大学学习",
        "1985年－1993\n海港大学学习", "1985年03月出生", "2011-01-28", "1993.4", "1993年4月-至今\n蓝鳍半导体公司总裁",
        "1993年4月至现在\n蓝鳍半导体公司总裁", "１９９３年４月至今\n蓝鳍半导体公司总裁", "一九八五年参加工作",
        "2003年1月－2001年1月\n蓝鳍半导体公司总裁", "1985年13月出生", "1990年代", "2012年4月5日加入美国民主党",
        "1985 年参加工作", "1985年－1993年\n1993年－1995年\n海港大学学习", "1985年－1993年\n",
    )
    GOOD = (
        "1985年－1993年\n海港大学学习", "2001年1月－2003年1月\n蓝鳍半导体公司总裁", "1993年4月至今\n蓝鳍半导体公司总裁",
        "1963年2月出生", "1984年参加工作", "（2026年3月起兼任亚特兰蒂斯潮汐部顾问委员会委员）", PASS_ENTRY,
        "2004年3月加入美国民主党", "1985年－1993年 海港大学学习",
    )

    def test_fail(self):
        for text in self.BAD:
            self.assertIn("CR03", codes(text), text)

    def test_pass(self):
        for text in self.GOOD:
            self.assertNotIn("CR03", codes(text), text)

    def test_message_names_fullwidth_dash(self):
        self.assertTrue(any("－" in m for m in messages("1985年-1993年\n海港大学学习")))


class SpacingTest(unittest.TestCase):  # CR04
    def test_fail_missing_space(self):
        self.assertIn("CR04", codes("Northwind Labs公司工程师"))
        self.assertIn("CR04", codes("现任Northwind Labs 公司董事"))

    def test_fail_too_many_or_wrong_spaces(self):
        self.assertIn("CR04", codes("Northwind Labs  公司"))
        self.assertIn("CR04", codes("Northwind Labs　公司"))
        self.assertIn("CR04", codes("蓝鳍半导体公司 （NL）高级副总裁"))
        self.assertIn("CR04", codes("蓝鳍半导体公司\t总裁"))

    def test_pass(self):
        for text in ("Northwind Labs 公司工程师", "蓝鳍半导体公司（NL）高级副总裁", "1985年参加工作", "#AI政见 #干部简历", PASS_ENTRY):
            self.assertNotIn("CR04", codes(text), text)

    def test_modes(self):
        self.assertNotIn("CR04", codes("Northwind Labs公司", cjk_space="forbid"))
        self.assertIn("CR04", codes("Northwind Labs 公司", cjk_space="forbid"))
        self.assertNotIn("CR04", codes("Northwind Labs公司　", cjk_space="off"))


class DunhaoTest(unittest.TestCase):  # CR05
    def test_fail_comma_in_entry(self):
        self.assertIn("CR05", codes("2003年－2011年\n蓝鳍半导体公司总裁，首席执行官"))
        self.assertIn("CR05", codes("2003年－2011年 蓝鳍半导体公司总裁,首席执行官"))

    def test_fail_present_roles(self):
        self.assertIn("CR05", codes("现任蓝鳍半导体公司总裁，首席执行官。"))
        self.assertIn("CR05", codes("现任蓝鳍半导体公司总裁、董事，现任海港大学校董。"))

    def test_fail_repeated_unit_and_symbols(self):
        self.assertIn("CR05", codes("1995年－2001年\n蓝鳍半导体公司工程师、蓝鳍半导体公司总监"))
        self.assertIn("CR05", codes("1995年－2001年\n蓝鳍半导体公司工程师、、总监"))
        self.assertIn("CR05", codes("1995年－2001年\n蓝鳍半导体公司总裁/首席执行官"))
        self.assertIn("CR05", codes("1995年－2001年\n蓝鳍半导体公司总裁、总裁"))

    def test_pass(self):
        for text in ("2011年1月至今\n蓝鳍半导体公司总裁、首席执行官、董事",
                     "现任美国联邦参议员（海岸州）、参议院灯塔委员会委员、参议院潮汐、航运和渔业委员会资深成员。",
                     PASS_ENTRY, PASS_FIRST_PARA):
            self.assertNotIn("CR05", codes(text), text)


class PlaceholderTest(unittest.TestCase):  # CR06
    def test_fail_words(self):
        for word in ("未知", "待补", "资料不详", "待核", "未查到", "暂缺"):
            self.assertIn("CR06", codes(f"出生年月{word}"), word)

    def test_fail_symbols_and_fuzzy(self):
        for text in ("××大学学习", "XX公司工程师", "出生年月？", "约5年", "大概1990年", "1990年左右参加工作"):
            self.assertIn("CR06", codes(text), text)

    def test_pass(self):
        self.assertNotIn("CR06", codes(PASS_FIRST_PARA))
        self.assertNotIn("CR06", codes("约翰·史密斯"))  # “约”后面不是数字


class GlossaryTest(unittest.TestCase):  # CR07
    def test_load(self):
        self.assertEqual(GLOSSARY.counts, {"org": 5, "title": 6, "person": 3})
        self.assertIn("蓝鳍半导体公司", GLOSSARY.standards)
        self.assertIn("灯塔管理员", GLOSSARY.standards)  # “／”分成两种写法
        self.assertIn("灯塔长", GLOSSARY.standards)
        self.assertNotIn("××负责人", GLOSSARY.standards)  # 模板条目不进索引
        self.assertEqual(GLOSSARY.full_forms["美国蓝鳍半导体公司（Bluefin）"], "蓝鳍半导体公司")
        self.assertIn("行政总裁", GLOSSARY.variants)  # 用法说明里的“不写”
        self.assertNotIn("亚特兰蒂斯潮汐部长", GLOSSARY.variants)  # 只差国名前缀
        self.assertNotIn("奎尔", GLOSSARY.variants)  # 标准写法的一部分

    def test_fail_variants_and_full_forms(self):
        self.assertIn("CR07", codes("Bluefin公司总裁"))
        self.assertIn("CR07", codes("美国蓝鳍半导体公司（Bluefin）总裁"))
        self.assertIn("CR07", codes("美国海港大学经济学专业学习"))
        self.assertIn("CR07", codes("蓝鳍半导体公司行政总裁"))
        self.assertIn("CR07", codes("蓝鳍半导体公司CEO"))
        self.assertIn("CR07", codes("北风实验室研究员"))
        self.assertIn("CR07", codes("艾弗里·奎尔"))

    def test_fail_english_keys(self):
        self.assertIn("CR07", codes("Avery Quill"))
        self.assertIn("CR07", codes("蓝鳍半导体公司 Chief Harbor Officer"))
        msgs = messages("Chief Executive Officer")
        self.assertTrue(any("首席执行官" in m for m in msgs))

    def test_person_name_columns(self):
        self.assertIn("CR07", codes("乔丹·林"))  # 有汉名就用汉名
        self.assertTrue(any("林昭丹" in m for m in messages("乔丹·林")))
        self.assertIn("CR07", codes("方凯茜"))  # 汉名待核，用新华社译名
        self.assertNotIn("CR07", codes("凯西·方"))
        self.assertNotIn("CR07", codes("林昭丹"))

    def test_unverified_han_name_uses_xinhua_even_when_row_is_verified(self):
        header = "english_name,新华社译名,本人汉名或通行汉名,备注,status\n"
        for note in ("汉名待核", "媒体称其汉名为方凯茜（未核）；简历用汉名须先核实"):
            with self.subTest(note=note), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "译名表_test_person.csv"
                path.write_text(header + f"Casey Fong,凯西·方,方凯茜,{note},已核\n", encoding="utf-8")
                glossary = cl.load_glossary(tmp)
                self.assertNotIn("CR07", codes("凯西·方", glossary=glossary))
                self.assertIn("CR07", codes("方凯茜", glossary=glossary))
                self.assertTrue(any("凯西·方" in m for m in messages("方凯茜", glossary=glossary)))

    def test_middle_dot(self):
        self.assertIn("CR07", codes("埃弗里•奎尔"))
        self.assertNotIn("CR07", codes("埃弗里·奎尔"))

    def test_pass_standards_and_short_forms(self):
        for text in ("蓝鳍半导体公司", "Northwind Labs 公司", "Northwind Labs公司", "首席执行官", "埃弗里·奎尔",
                     "蓝鳍", "亚特兰蒂斯潮汐部长", "灯塔长", "参议院灯塔委员会委员"):
            self.assertNotIn("CR07", codes(text), text)

    def test_english_inside_longer_run_or_url(self):
        self.assertNotIn("CR07", codes("Chief Harbor Officer of Atlantis"))
        self.assertNotIn("CR07", codes("https://example.test/avery-quill/Avery%20Quill"))

    def test_pending_note(self):
        notes = [v for v in run("亚特兰蒂斯潮汐部部长") if v.level == "note"]
        self.assertEqual(len(notes), 1)
        self.assertIn("待核", notes[0].message)
        self.assertNotIn("CR07", codes("亚特兰蒂斯潮汐部部长"))

    def test_no_glossary(self):
        self.assertNotIn("CR07", codes("Bluefin公司", glossary=None))

    def test_missing_dir(self):
        with self.assertRaises(FileNotFoundError):
            cl.load_glossary(FIXTURES / "no-such-dir")


@unittest.skipUnless(REAL_GLOSSARY.is_dir(), "shared/glossary 不在")
class RealGlossaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = cl.load_glossary(REAL_GLOSSARY)

    def test_counts(self):
        self.assertGreater(self.g.counts.get("org", 0), 100)
        self.assertGreater(self.g.counts.get("title", 0), 100)
        self.assertGreater(self.g.counts.get("person", 0), 100)

    def test_known_entries(self):
        self.assertIn("谷歌公司", self.g.standards)
        self.assertIn("首席执行官", self.g.standards)
        self.assertIn("CR07", codes("Google 高级副总裁", glossary=self.g))
        self.assertIn("CR07", codes("美国谷歌公司（Google）", glossary=self.g))
        self.assertIn("CR07", codes("谷歌公司行政总裁", glossary=self.g))
        self.assertNotIn("CR07", codes("谷歌公司首席执行官", glossary=self.g))

    def test_party_join_with_standard_party_name(self):  # CR01：政党全称是译名表标准写法，遮住后不能误报
        self.assertIn("卢旺达爱国阵线", self.g.standards)
        lead = "穆加博·卡伊塔雷，男，卢旺达籍，1961年3月出生，1979年5月入伍，1990年10月加入卢旺达爱国阵线。"
        self.assertNotIn("CR01", codes(lead, glossary=self.g, country="rw"))
        self.assertNotIn("CR01", codes("2004年3月加入美国民主党", glossary=self.g))
        self.assertIn("CR01", codes("2005年加入谷歌公司", glossary=self.g))

    def test_verified_row_clears_pending(self):  # CR07：同一写法有一行已核（Permanent Secretary (Rwanda)），不再提示待核
        for country in ("us", "rw"):
            notes = [v for v in run("2003年8月－2008年11月\n卢旺达教育部常任秘书", glossary=self.g, country=country)
                     if v.level == "note" and "常任秘书" in v.message]
            self.assertEqual(notes, [], country)


class CountryGlossaryTest(unittest.TestCase):  # CR07 国别：--country rw 时优先用英文键带卢旺达括注的行
    TITLE = ("english_title,干部简历写法,历史写法,用法说明,依据URL,置信度,备注,status,decided_by\n"
             "Tide Keeper,司潮官,,,,高,虚构测试条目,已核,证据\n"
             "Tide Keeper (Rwanda),司潮官,,,,低,虚构测试条目,待核,历史\n"
             "Reef Warden,护礁官,,,,低,虚构测试条目,待核,历史\n"
             "\"Reef Warden (district, Rwanda)\",护礁长,,,,高,虚构测试条目,已核,证据\n")

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        (d / "译名表_t_title.csv").write_text(cls.TITLE, encoding="utf-8-sig")
        (d / "译名表_t_org.csv").write_text("english_name,全称,简历写法,历史写法,简称,简历写法示例,依据URL,依据媒体,依据日期,置信度,备注,status,decided_by\n", encoding="utf-8-sig")
        cls.g = cl.load_glossary(d)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def notes(self, text, country):
        return [v.message for v in cl.lint_text(text, glossary=self.g, country=country) if v.code == "CR07" and v.level == "note"]

    def test_pending_follows_country_row(self):
        self.assertEqual(self.notes("海港司潮官", "us"), [])          # 有一行已核
        self.assertEqual(len(self.notes("海港司潮官", "rw")), 1)      # 卢旺达那行待核
        self.assertNotIn("司潮官", self.g.pending)

    def test_english_hint_prefers_country_row(self):
        rw = [v.message for v in cl.lint_text("Reef Warden", glossary=self.g, country="rw") if v.code == "CR07"]
        us = [v.message for v in cl.lint_text("Reef Warden", glossary=self.g, country="us") if v.code == "CR07"]
        self.assertTrue(any("写作「护礁长」" in m for m in rw), rw)
        self.assertTrue(any("表中条目" in m for m in us), us)


class PartyTest(unittest.TestCase):  # CR08
    def test_fail_sentence_format(self):
        self.assertIn("CR08", codes("美国人，加入美国民主党，"))  # 没有年月
        self.assertIn("CR08", codes("2004年3月加入民主党"))  # 没写“美国”
        self.assertIn("CR08", codes("2004年3月加入美国民主党党员"))
        self.assertIn("CR08", codes("2004年3月加入了美国民主党"))
        for text in ("民主党籍", "共和党人", "1985年入党", "Republican", "无党籍", "独立人士", "无党派，"):
            self.assertIn("CR08", codes(text), text)

    def test_fail_position(self):
        self.assertIn("CR08", codes("林昭丹，女，1968年11月出生，2004年3月加入美国民主党，美国人，1990年参加工作，海港大学法学博士学位。"))
        self.assertIn("CR08", codes("林昭丹，女，1968年11月出生，美国人，1990年参加工作，海港大学法学博士学位，2004年3月加入美国民主党。"))
        self.assertIn("CR08", codes("林昭丹，女，1968年11月出生，美国人，1990年参加工作，1988年加入美国民主党，海港大学法学博士学位。"))
        self.assertIn("CR08", codes("林昭丹，女，1968年11月出生，美国人，2004年加入美国民主党，1990年参加工作，海港大学法学博士学位。"))
        self.assertIn("CR08", codes("林昭丹，女，1968年11月出生，美国人，无党派人士，2004年加入美国民主党，海港大学法学博士学位。"))
        self.assertIn("CR08", codes("林昭丹，女，无党派人士，1968年11月出生，美国人，海港大学法学博士学位。"))

    def test_pass(self):
        for text in (PASS_FIRST_PARA,
                     "林昭丹，女，1968年11月出生，美国人，2004年加入美国民主党，2006年参加工作，海港大学法学博士学位。",
                     "凯西·方，女，1971年6月出生，美国人，无党派人士，1994年参加工作，海港大学经济学博士学位。",
                     "1998年加入英国工党"):
            self.assertNotIn("CR08", codes(text), text)


class SourceTest(unittest.TestCase):  # CR09
    def test_fail(self):
        for text in ("来源：公司官网", "https://example.test/x", "按毕业年推算", "第3级锚点", "海港大学[1]", "据公司官网显示", "维基百科"):
            self.assertIn("CR09", codes(text), text)

    def test_pass(self):
        self.assertNotIn("CR09", codes(PASS_FIRST_PARA))
        self.assertNotIn("CR09", codes(PASS_ENTRY))


NEW_LEAD = "林昭丹，女，亚裔，美国籍，1968年11月出生，海岸新港人，1990年6月参加工作，2004年3月加入美国民主党，海港大学法学博士学位。"
NL = "\n"


def lines(*parts):
    return NL.join(parts) + NL


class RequiredFieldsTest(unittest.TestCase):  # CR10
    def test_pass(self):
        self.assertNotIn("CR10", codes(NEW_LEAD))
        self.assertNotIn("CR10", codes("帕克（1968年7月－2024年5月），男，美国籍，1990年6月参加工作，海港大学法学博士学位。"))

    def test_fail(self):
        for text in ("林昭丹，女，美国人，1968年11月出生，1990年6月参加工作。",    # 国籍写××籍
                     "林昭丹，女，美国籍，1968年出生，1990年6月参加工作。",          # 出生写到月
                     "林昭丹，女，美国籍，1968年11月出生，1990年参加工作。",        # 参加工作写到月
                     "林昭丹，女，美国籍，1968年11月出生，海港大学法学博士学位。",  # 缺参加工作
                     "林昭丹，美国籍，1968年11月出生，1990年6月参加工作。"):        # 缺性别
            self.assertIn("CR10", codes(text), text)


class GranularityTest(unittest.TestCase):  # CR11
    def test_pass(self):
        self.assertNotIn("CR11", codes(lines("1985年－1993年", "海港大学教授", "", "1993年至今", "灯塔公司总裁")))
        self.assertNotIn("CR11", codes(lines("1985年3月－1993年6月", "海港大学教授", "", "1993年7月至今", "灯塔公司总裁")))

    def test_fail(self):
        text = lines("1985年－1993年", "海港大学教授", "", "1993年7月－1999年8月", "灯塔公司副总裁", "", "1999年9月至今", "灯塔公司总裁")
        self.assertTrue(any("全篇写到月" in m for m in messages(text)))
        parts = []
        for i in range(6):
            parts += [f"{1980 + i}年－{1981 + i}年", "灯塔公司职员", ""]
        many = lines(*parts, "1987年3月至今", "灯塔公司总裁")
        self.assertTrue(any("全篇只写到年" in m for m in messages(many)))


class WorkAgeTest(unittest.TestCase):  # CR12
    def test(self):
        self.assertIn("CR12", codes("林昭丹，女，美国籍，1968年11月出生，1979年5月入伍。"))
        self.assertNotIn("CR12", codes(NEW_LEAD))


class OrderTest(unittest.TestCase):  # CR13
    def test_point_dates_sort_before_same_start_ranges_and_ongoing_roles(self):
        for start, end in (("1994年", "1995年"), ("1994年6月", "1995年7月")):
            with self.subTest(start=start):
                text = lines(start, "海港大学计算机专业学习，获理学博士学位", "",
                             f"{start}－{end}", "灯塔公司工程师", "", f"{start}至今", "海岸协会理事")
                self.assertNotIn("CR13", codes(text))
                for period in (f"{start}－{end}", f"{start}至今"):
                    reversed_text = lines(period, "灯塔公司工程师", "", start, "海港大学教授")
                    self.assertIn("CR13", codes(reversed_text))

    def test_pass(self):
        text = lines(NEW_LEAD, "", "1986年9月－1990年5月", "海港大学政治学专业学习，获文学学士学位", "",
                     "1990年6月－1993年8月", "海岸州州长办公室助理")
        self.assertNotIn("CR13", codes(text))

    def test_fail(self):
        unsorted = lines(NEW_LEAD, "", "1990年6月－1993年8月", "海岸州州长办公室助理", "",
                         "1986年9月－1990年5月", "海港大学政治学专业学习，获文学学士学位")
        self.assertIn("CR13", codes(unsorted))
        mismatch = lines(NEW_LEAD, "", "1991年1月－1993年8月", "海岸州州长办公室助理")
        self.assertTrue(any("参加工作时间" in m for m in messages(mismatch)))
        same_start = lines("1990年6月至今", "灯塔公司董事", "", "1990年6月－1993年8月", "海岸州州长办公室助理")
        self.assertTrue(any("先结束的在前" in m for m in messages(same_start)))


class FormerRoleTest(unittest.TestCase):  # CR14
    def test(self):
        lead = "林昭丹，女，美国籍，1968年11月出生，1990年6月参加工作，海港大学法学博士学位。曾任海岸州律师。"
        good = lines(lead, "", "1990年6月－2004年12月", "海岸州律师")
        self.assertNotIn("CR14", codes(good))
        self.assertNotIn("CR01", codes(good), "首段末的“曾任”不算叙述词")
        self.assertIn("CR14", codes(lines(lead, "", "1990年6月－2004年12月", "灯塔公司总裁")))


class CountryTest(unittest.TestCase):  # CR15
    def test_us(self):
        self.assertNotIn("CR15", codes(NEW_LEAD))
        for word in ("非裔", "白种人", "华裔"):
            self.assertIn("CR15", codes(NEW_LEAD.replace("亚裔", word)), word)

    def test_rw(self):
        lead = "保罗·穆加博，男，卢旺达籍，1957年10月出生，1980年7月参加工作。"
        self.assertNotIn("CR15", codes(lead + "现任卢旺达爱国阵线主席、卢旺达共和国总统。", country="rw"))
        self.assertIn("CR15", codes(lead + "现任卢旺达共和国总统、卢旺达爱国阵线主席。", country="rw"))
        self.assertIn("CR15", codes(lead.replace("男，", "男，图西族，"), country="rw"))
        self.assertNotIn("CR15", codes(NEW_LEAD, country="other"))
        with self.assertRaises(ValueError):
            cl.lint_text("x", country="xx")


class FixtureFilesTest(unittest.TestCase):
    def test_pass_dir_is_clean(self):
        for path in sorted((FIXTURES / "pass").glob("*.txt")):
            errors = [v for v in cl.lint_file(path, glossary=GLOSSARY) if v.level == "error"]
            self.assertEqual(errors, [], f"{path.name}: " + "; ".join(v.format() for v in errors))

    def test_fail_file_hits_every_rule(self):
        found = {v.code for v in cl.lint_file(FIXTURES / "fail" / "one_per_rule.txt", glossary=GLOSSARY)}
        self.assertEqual(found, set(cl.RULES))

    def test_select_and_ignore(self):
        path = FIXTURES / "fail" / "one_per_rule.txt"
        only = {v.code for v in cl.lint_file(path, glossary=GLOSSARY, select=["CR03"])}
        self.assertEqual(only, {"CR03"})
        rest = {v.code for v in cl.lint_file(path, glossary=GLOSSARY, ignore=["cr03", "CR09"])}
        self.assertFalse(rest & {"CR03", "CR09"})
        with self.assertRaises(ValueError):
            cl.lint_text("x", select=["CR99"])

    def test_allow_list(self):
        self.assertIn("CR01", codes("蓝鳍半导体公司技术支持部经理"))
        self.assertNotIn("CR01", codes("蓝鳍半导体公司技术支持部经理", allow=cl.read_allow_file(FIXTURES / "allow.txt")))

    def test_violation_format_and_sorting(self):
        vs = run("1985年-1993年\n期间任总监")
        self.assertEqual([v.line for v in vs], sorted(v.line for v in vs))
        self.assertIn("CR03", vs[0].format())
        self.assertEqual(vs[0].to_dict()["rule"], "日期格式")

    def test_iter_files(self):
        found = cl.iter_files([FIXTURES])
        names = {p.name for p in found}
        self.assertIn("business.txt", names)
        self.assertIn("README.md", names)
        self.assertNotIn("译名表_test_org.csv", names)
        with self.assertRaises(FileNotFoundError):
            cl.iter_files([FIXTURES / "nope.txt"])


class CommandLineTest(unittest.TestCase):
    def cli(self, *args, stdin=None):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        return subprocess.run(
            [sys.executable, "-m", "tools.lint.cadre_lint", *args],
            cwd=REPO, input=stdin, capture_output=True, text=True, encoding="utf-8", env=env,
        )

    def test_exit_codes(self):
        self.assertEqual(self.cli(str(FIXTURES / "pass"), "--glossary", str(TEST_GLOSSARY)).returncode, 0)
        self.assertEqual(self.cli(str(FIXTURES / "fail"), "--glossary", str(TEST_GLOSSARY)).returncode, 1)
        self.assertEqual(self.cli(str(FIXTURES / "nope.txt"), "--no-glossary").returncode, 2)
        self.assertEqual(self.cli(str(FIXTURES / "pass"), "--glossary", str(FIXTURES / "no-such-dir")).returncode, 2)
        self.assertEqual(self.cli().returncode, 2)
        self.assertEqual(self.cli("--list-rules").returncode, 0)

    def test_text_output(self):
        r = self.cli(str(FIXTURES / "fail" / "one_per_rule.txt"), "--glossary", str(TEST_GLOSSARY))
        self.assertIn("one_per_rule.txt:1:", r.stdout)
        self.assertIn("CR03", r.stdout)
        self.assertIn("处违规", r.stderr)

    def test_json_output(self):
        r = self.cli(str(FIXTURES / "fail"), "--glossary", str(TEST_GLOSSARY), "--format", "json")
        data = json.loads(r.stdout)
        self.assertEqual(data["files"], 1)
        self.assertGreater(data["violations"], 0)
        self.assertEqual({i["code"] for i in data["items"]}, set(cl.RULES))

    def test_select_quiet_and_stdin(self):
        r = self.cli(str(FIXTURES / "fail"), "--glossary", str(TEST_GLOSSARY), "--select", "CR02", "-q")
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        r = self.cli("-", "--no-glossary", stdin="1985年－1993年\n海港大学学习\n")
        self.assertEqual(r.returncode, 0)
        r = self.cli("-", "--no-glossary", stdin="1985年-1993年\n海港大学学习\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("<stdin>:1:1: CR03", r.stdout)

    def test_notes_and_allow(self):
        r = self.cli(str(FIXTURES / "pass" / "business.txt"), "--glossary", str(TEST_GLOSSARY), "--notes")
        self.assertEqual(r.returncode, 0)
        self.assertIn("(提示)", r.stdout)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.txt"
            p.write_text("蓝鳍半导体公司技术支持部经理\n", encoding="utf-8")
            self.assertEqual(self.cli(str(p), "--no-glossary").returncode, 1)
            self.assertEqual(self.cli(str(p), "--no-glossary", "--allow", str(FIXTURES / "allow.txt")).returncode, 0)

    def test_bom_and_bad_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "bom.txt"
            good.write_bytes("﻿1985年－1993年\n海港大学学习\n".encode("utf-8"))
            self.assertEqual(self.cli(str(good), "--no-glossary").returncode, 0)
            bad = Path(tmp) / "gbk.txt"
            bad.write_bytes("海港大学学习\n".encode("gbk"))
            self.assertEqual(self.cli(str(bad), "--no-glossary").returncode, 2)


if __name__ == "__main__":
    unittest.main()
