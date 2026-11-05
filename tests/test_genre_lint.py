"""tools/lint/speech_lint.py、newswire_lint.py 和共用的 genre_common：数字抽取、参数卡主要条目。全部用虚构样例。"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from tools.lint import genre_common, newswire_lint, speech_lint

FIXTURES = Path(__file__).resolve().parent / "fixtures"
GLOSSARY_DIR = str(FIXTURES / "glossary")


def levels(issues, rule=None):
    return [i["level"] for i in issues if rule is None or i["rule"] == rule]


def speech_doc():
    draft = json.loads((FIXTURES / "speech_response.json").read_text(encoding="utf-8"))
    paragraphs = [draft["date_line"], draft["speaker_line"], draft["salutation"], *draft["opening"]]
    for section in draft["sections"]:
        paragraphs += [section["heading"], *section["paragraphs"]]
    paragraphs += [*draft["closing"], draft["thanks"]]
    return {"title": draft["title"], "subtitle": draft["subtitle"], "paragraphs": paragraphs}


def newswire_doc():
    draft = json.loads((FIXTURES / "newswire_response.json").read_text(encoding="utf-8"))
    return {"title": draft["title"], "subtitle": draft["subtitle"], "paragraphs": [draft["dateline"] + draft["lead"], *draft["paragraphs"]]}


class NumberParsingTest(unittest.TestCase):
    def values(self, text):
        return sorted({round(n.value, 6) for n in genre_common.en_numbers(text)[0]})

    def test_english_numbers(self):
        self.assertIn(3e8, self.values("about three hundred million dollars"))
        self.assertIn(2.5e9, self.values("spend about $2.5 billion"))
        self.assertIn(1.2e4, self.values("we shipped twelve thousand"))
        self.assertIn(20, self.values("two decades of operations"))
        self.assertIn(2027, self.values("in fiscal year '27"))
        self.assertIn(1000, self.values("nearly 1,000 positions"))
        self.assertIn(1e9, self.values("of the billion people using it"))
        self.assertIn(3, self.values("triple our investment"))

    def test_required_numbers_skip_dates_and_one(self):
        _, required = genre_common.en_numbers("Updated October 14, 2026\nOne of the two plants")
        self.assertEqual([n.value for n in required], [2])

    def test_year_abbreviation_and_dollar_scale_not_double_counted(self):
        _, required = genre_common.en_numbers("He's proposing a $1.5 trillion budget for fiscal year '27.")
        self.assertEqual(sorted(n.value for n in required), [2027, 1.5e12])

    def test_chinese_numbers(self):
        values = [n.value for n in genre_common.zh_numbers("投资约25亿美元，雇用1200人，占6%，14日宣布，10月15日电，1.5万亿美元")]
        self.assertEqual(values, [2.5e9, 1200, 6, 1.5e12])
        self.assertEqual(genre_common.han_numbers("两个新基地、三点意见、十二人、两万"), [2, 3, 12, 20000])

    def test_fabricated_and_missing_numbers(self):
        issues = genre_common.number_issues(["投资30亿美元。", "共有7家。"], "They will invest $3 billion in two plants.", rule="X")
        self.assertEqual(levels(issues), ["P0", "P1"])
        self.assertIn("7", issues[0]["message"])
        self.assertIn("two", issues[1]["message"])

    def test_approx_qualifier(self):
        source = "It will spend up to $21 billion and about 6% more."
        self.assertEqual(levels(genre_common.approx_issues(["将投资210亿美元，增加6%。"], source, rule="N35")), ["P1", "P1"])
        self.assertEqual(genre_common.approx_issues(["将投资最多210亿美元，增加约6%。"], source, rule="N35"), [])


class SpeechLintTest(unittest.TestCase):
    def lint(self, document, source=None):
        source = (FIXTURES / "speech_sample.txt").read_text(encoding="utf-8") if source is None else source
        return speech_lint.lint_text("", source=source, document=document, glossary_dir=GLOSSARY_DIR)

    def test_fixture_has_no_p0(self):
        issues = self.lint(speech_doc())
        self.assertNotIn("P0", levels(issues), issues)

    def test_missing_salutation_and_headings_are_p0(self):
        doc = {"title": "在大会上的讲话", "subtitle": "", "paragraphs": ["（2026年1月1日）", "张三", "今天，我们召开大会。"]}
        issues = self.lint(doc, source="Today we meet.")
        self.assertEqual(levels(issues, "S01"), ["P0"])
        self.assertEqual(levels(issues, "S04"), ["P0"])

    def test_forbidden_refs_fake_orgs_placeholders_english(self):
        doc = speech_doc()
        doc["paragraphs"][5] += "要深入学习贯彻重要指示精神，公司党委要抓好落实，数据待核。we will keep going here."
        issues = self.lint(doc)
        for rule in ("S18", "S19", "S21", "S20"):
            self.assertIn("P0", levels(issues, rule), rule)

    def test_us_state_department_is_not_state_council(self):
        doc = speech_doc()
        doc["paragraphs"][5] += "美国国务院将参与。"
        self.assertNotIn("S18", [i["rule"] for i in self.lint(doc)])

    def test_transition_count_must_match_headings(self):
        doc = speech_doc()
        doc["paragraphs"][4] = "下面，我讲四点意见。"
        self.assertIn("P1", levels(self.lint(doc), "S06"))

    def test_cli_reads_pipeline_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "draft.json"
            path.write_text(json.dumps({"document": speech_doc()}, ensure_ascii=False), encoding="utf-8")
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
                code = speech_lint.main([str(path), "--source", str(FIXTURES / "speech_sample.txt"), "--glossary-dir", GLOSSARY_DIR, "--format", "json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())["counts"]["P0"], 0)


class NewswireLintTest(unittest.TestCase):
    def lint(self, document, agency="政见社"):
        source = (FIXTURES / "newswire_sample.txt").read_text(encoding="utf-8")
        return newswire_lint.lint_text("", source=source, document=document, extra={"agency": agency}, glossary_dir=GLOSSARY_DIR)

    def test_fixture_has_no_p0(self):
        issues = self.lint(newswire_doc())
        self.assertNotIn("P0", levels(issues), issues)

    def test_dateline_must_use_configured_agency(self):
        doc = newswire_doc()
        doc["paragraphs"][0] = doc["paragraphs"][0].replace("政见社", "新华社")
        issues = self.lint(doc)
        self.assertIn("P0", levels(issues, "N01"))
        self.assertIn("P0", levels(issues, "N02"))

    def test_english_units_and_self_reference(self):
        doc = newswire_doc()
        doc["paragraphs"][1] += "本报记者了解到，投资2.5 billion美元。"
        issues = self.lint(doc)
        self.assertIn("P0", levels(issues, "N21"))
        self.assertIn("P0", levels(issues, "N25"))

    def test_title_number_must_appear_in_body(self):
        doc = newswire_doc()
        doc["title"] = "示例动力公司拟投资40亿美元建厂"
        self.assertIn("P0", levels(self.lint(doc), "N05"))

    def test_english_tokens_compared_without_possessive_and_punctuation(self):
        source = "Paramount’s deal closed, and HBO Max and Paramount+ joined. It was obtained by The Associated Press."
        issues = genre_common.english_issues(["派拉蒙旗下HBO Max和Paramount+，据The Associated Press报道。"], source, rule="N29")
        self.assertEqual(sorted(i["level"] for i in issues), ["P2", "P2", "P2"], issues)

    def test_plain_text_input(self):
        doc = newswire_doc()
        text = "\n\n".join([doc["title"], *doc["paragraphs"]])
        issues = newswire_lint.lint_text(text, source="", extra={"agency": "政见社"}, glossary_dir=GLOSSARY_DIR)
        self.assertNotIn("N01", [i["rule"] for i in issues])


if __name__ == "__main__":
    unittest.main()
