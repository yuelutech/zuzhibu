"""glossary 的测试：夹具表全是虚构条目；最后一组用仓库里的 v1.4 定稿做冒烟。"""

import unittest
from pathlib import Path

from shared.backends import glossary as g

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "glossary"


class FixtureGlossaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.glossary = g.Glossary.load(FIXTURE_DIR)

    def test_load_counts_and_columns(self):
        self.assertEqual(self.glossary.count("org"), 5)
        self.assertEqual(self.glossary.count("title"), 4)
        self.assertEqual(self.glossary.count("person"), 3)
        self.assertEqual(len(self.glossary), 12)

    def test_entry_forms(self):
        org = self.glossary.lookup("org", "Example Dynamics")[0]
        self.assertEqual(org.first, "美国示例动力公司（Example Dynamics）")
        self.assertEqual(org.later, "示例动力")
        self.assertEqual(org.resume, "示例动力公司")
        self.assertFalse(org.needs_review)
        hri = self.glossary.lookup("org", "Harbor Research Institute (HRI)")[0]
        self.assertEqual(hri.key, "Harbor Research Institute")
        self.assertEqual(hri.aliases, ("HRI",))
        self.assertEqual(hri.qualifier, "")
        ews = self.glossary.lookup("org", "Example Web Services")[0]
        self.assertEqual(ews.aliases, ("EWS",))
        mayor = self.glossary.lookup("title", "mayor")[0]
        self.assertEqual(mayor.qualifier, "City of Harborton")
        self.assertEqual(mayor.aliases, ())
        person = self.glossary.lookup("person", "Dana Reyes")[0]
        self.assertEqual((person.first, person.later), ("丹娜·雷耶斯", "雷耶斯"))
        lee = self.glossary.lookup("person", "Lee Wen")[0]
        self.assertEqual((lee.first, lee.later), ("李文", "李文"))
        self.assertTrue(lee.needs_review)

    def test_match_longest_first_aliases_titles_and_order(self):
        text = (
            "Example Dynamics Labs and Example Dynamics signed with the Harbor Research Institute (HRI); "
            "HRI's president and the chief executive officer, Dana Reyes, met the Mayor. Northwind Group declined. "
            "EWS was not involved; Lee Wen attended."
        )
        matches = self.glossary.match(text)
        by_english = {m.entry.english: m for m in matches}

        self.assertEqual(by_english["Example Dynamics Labs"].count, 1)
        self.assertEqual(by_english["Example Dynamics"].count, 1, "不能在 Labs 里二次命中")
        hri = by_english["Harbor Research Institute (HRI)"]
        self.assertEqual(hri.count, 3)
        self.assertEqual(hri.matched_texts, ["Harbor Research Institute", "HRI"])
        self.assertEqual(by_english["Example Web Services"].matched_texts, ["EWS"])

        president = by_english["President (company)"]
        self.assertTrue(president.ambiguous)
        self.assertEqual([a.english for a in president.alternatives], ["President (university)"])
        self.assertNotIn("President (university)", by_english)
        self.assertEqual(by_english["Chief Executive Officer"].matched_texts, ["chief executive officer"])
        self.assertEqual(by_english["Mayor (City of Harborton)"].entry.first, "市长")

        self.assertTrue(by_english["Northwind Group"].entry.needs_review)
        self.assertIn("Lee Wen", by_english)
        positions = [m.first_pos for m in matches]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(matches[0].entry.english, "Example Dynamics Labs")

    def test_match_respects_word_boundaries_and_case(self):
        self.assertEqual(self.glossary.match("ExampleDynamics is one word; HRIs plural; hri lower"), [])
        self.assertEqual([m.entry.english for m in self.glossary.match("CHIEF EXECUTIVE OFFICER")], ["Chief Executive Officer"])
        self.assertEqual(self.glossary.match("dana reyes"), [])

    def test_to_dict_and_prompt_table(self):
        matches = self.glossary.match("Example Dynamics and the president and Northwind Group")
        table = g.render_prompt_table(matches)
        self.assertIn("| Example Dynamics | 机构 | 美国示例动力公司（Example Dynamics） | 示例动力 | 已核 |", table)
        self.assertIn("同名多义", table)
        self.assertIn("待核，照表写并列入复核清单", table)
        d = matches[0].to_dict()
        self.assertEqual(d["english"], "Example Dynamics")
        self.assertEqual(d["kind_zh"], "机构")
        self.assertEqual(g.render_prompt_table([]).startswith("（原文没有命中译名表条目"), True)

    def test_check_output_first_mention_rules(self):
        matches = self.glossary.match("Example Dynamics met Dana Reyes and Northwind Group; the chief executive officer spoke.")
        good = "美国示例动力公司（Example Dynamics）宣布……示例动力首席执行官丹娜·雷耶斯说……雷耶斯还说……美国北风集团（Northwind Group）拒绝置评。"
        issues = self.glossary.check_output(good, matches)
        self.assertEqual([i["rule"] for i in issues], ["glossary-pending"])
        self.assertEqual(issues[0]["level"], "P2")

        only_short = "示例动力宣布……雷耶斯说……北风集团拒绝置评。"
        issues = self.glossary.check_output(only_short, matches)
        rules = sorted((i["rule"], i["english"]) for i in issues if i["rule"] != "glossary-pending")
        self.assertEqual(
            rules,
            [("glossary-first-mention", "Dana Reyes"), ("glossary-first-mention", "Example Dynamics"), ("glossary-first-mention", "Northwind Group")],
        )

        short_before_full = "示例动力宣布……美国示例动力公司（Example Dynamics）是……丹娜·雷耶斯……美国北风集团（Northwind Group）"
        issues = self.glossary.check_output(short_before_full, matches)
        self.assertIn(("glossary-first-mention", "Example Dynamics"), [(i["rule"], i["english"]) for i in issues])

        missing = "另一家公司宣布了什么。丹娜·雷耶斯……美国北风集团（Northwind Group）"
        issues = self.glossary.check_output(missing, matches)
        self.assertIn(("glossary-missing", "Example Dynamics"), [(i["rule"], i["english"]) for i in issues])

        repeated = "美国示例动力公司（Example Dynamics）……美国示例动力公司（Example Dynamics）……美国示例动力公司（Example Dynamics）丹娜·雷耶斯 美国北风集团（Northwind Group）"
        issues = self.glossary.check_output(repeated, matches)
        self.assertIn(("glossary-repeat-full", "Example Dynamics"), [(i["rule"], i["english"]) for i in issues])
        self.assertTrue(all(i["level"] in ("P1", "P2") for i in issues))

    def test_missing_column_raises(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "x.csv"
            bad.write_text("english_name,全称\nA,甲\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                g.Glossary.read_csv("org", bad)


class RealGlossarySmokeTest(unittest.TestCase):
    """仓库里的 v1.4 定稿：只验证能读、条数对、几条已核写法取得出来。"""

    @classmethod
    def setUpClass(cls):
        cls.glossary = g.Glossary.load()

    def test_counts_match_readme(self):
        self.assertEqual(self.glossary.count("org"), 522)
        self.assertEqual(self.glossary.count("title"), 406)
        self.assertEqual(self.glossary.count("person"), 1100)

    def test_known_entries(self):
        openai = self.glossary.lookup("org", "OpenAI")[0]
        self.assertEqual(openai.first, "美国开放人工智能研究中心（OpenAI）")
        self.assertEqual(openai.later, "OpenAI")
        aws = self.glossary.lookup("org", "Amazon Web Services")[0]
        self.assertIn("AWS", aws.aliases)
        ceo = self.glossary.lookup("title", "Chief Executive Officer")[0]
        self.assertEqual(ceo.first, "首席执行官")
        matches = self.glossary.match("AWS and OpenAI announced; the Chief Executive Officer spoke.")
        englishes = {m.entry.english for m in matches}
        self.assertIn("Amazon Web Services", englishes)
        self.assertIn("OpenAI", englishes)
        self.assertIn("Chief Executive Officer", englishes)

    def test_every_entry_has_forms(self):
        for entry in self.glossary:
            self.assertTrue(entry.first, entry.english)
            self.assertTrue(entry.later, entry.english)

    def test_single_word_name_inside_longer_name_is_not_a_hit(self):
        englishes = {m.entry.english for m in self.glossary.match("the U.S. International Development Finance Corporation and Intel Corp. said")}
        self.assertNotIn("Finance", englishes)
        self.assertIn("Intel", englishes)
        englishes = {m.entry.english for m in self.glossary.match("Today Google announced a deal. Netflix declined.")}
        self.assertTrue({"Google", "Netflix"} <= englishes)

    def test_dotted_initials_and_related_titles(self):
        matches = {m.entry.english: m for m in self.glossary.match("Vice President J.D. Vance spoke after President Trump.")}
        self.assertIn("JD Vance", matches)
        related = {r.english: r.first for r in matches["Vice President"].related}
        self.assertEqual(related.get("Vice President of the United States"), "副总统")
        table = g.render_prompt_table(list(matches.values()))
        self.assertIn("相关条目，按语境选：", table)
        self.assertIn("Vice President of the United States→副总统", table)


if __name__ == "__main__":
    unittest.main()
