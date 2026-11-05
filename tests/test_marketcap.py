"""商界市值（bureaus/dispatch/marketcap.py）：会话的回复要全部对得上才写市值文件，对不上整份不写、留着上次的；
名单页按市值分层、层内从大到小排（zuzhibu/rosters.py）。公司、人物和数字都是虚构的，数据放临时目录，不开真会话。"""

import json
import tempfile
import unittest
from pathlib import Path

from bureaus.dispatch import marketcap
from zuzhibu import bureaus, rosters
from zuzhibu.store import Store

from tests.test_personnel import _career, _role, make_record

ENTRIES = [{"person": "a-one", "company": "甲公司", "ticker": "AAA", "usd": None},
           {"person": "b-two", "company": "乙公司", "ticker": "BBB", "usd": None},
           {"person": "c-three", "company": "丙公司", "ticker": "CCC", "usd": None}]


def answer(date="2026-10-01", **usd):
    values = {"AAA": 1.6e12, "BBB": 9.0e11, "CCC": 1.2e12, **usd}
    return {"date": date, "notes": "", "companies": [
        {"ticker": t, "usd": v, "url": f"https://example.com/{t.lower()}", "publisher": "示例行情网", "excerpt": f"{t} 市值 {v}"}
        for t, v in values.items() if v is not None]}


class CheckTest(unittest.TestCase):
    def test_good_answer_gives_every_company_on_one_day(self):
        rows, problems = marketcap.check(answer(), ENTRIES, as_of="2026-10-01", previous={})
        self.assertEqual(problems, [])
        self.assertEqual([(r["ticker"], r["usd"], r["date"]) for r in rows],
                         [("AAA", 1600000000000, "2026-10-01"), ("BBB", 900000000000, "2026-10-01"), ("CCC", 1200000000000, "2026-10-01")])

    def test_anything_off_is_a_problem(self):
        cases = {
            "缺 CCC": answer(CCC=None),
            "不像真的": answer(AAA=12.0),
            "之前 10 天以内": answer(date="2026-09-01"),
        }
        for want, ans in cases.items():
            _rows, problems = marketcap.check(ans, ENTRIES, as_of="2026-10-01", previous={})
            self.assertTrue(any(want in p for p in problems), (want, problems))
        extra = answer()
        extra["companies"].append({"ticker": "ZZZ", "usd": 1e11, "url": "https://example.com/z", "publisher": "", "excerpt": "z"})
        self.assertTrue(any("多出" in p for p in marketcap.check(extra, ENTRIES, as_of="2026-10-01", previous={})[1]))
        no_source = answer()
        no_source["companies"][0]["url"] = "http://example.com/aaa"
        self.assertTrue(any("来源" in p for p in marketcap.check(no_source, ENTRIES, as_of="2026-10-01", previous={})[1]))
        jump = marketcap.check(answer(), ENTRIES, as_of="2026-10-01", previous={"AAA": {"usd": 3e11}})[1]
        self.assertTrue(any("变动过大" in p for p in jump), jump)


class RefreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / "rosters" / "market-caps.json"
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"as_of": None, "companies": ENTRIES}, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, reply, error=None, pages=None):
        calls = []

        def call(prompt, schema, config, runs, label):
            calls.append((prompt, schema, label))
            return reply, {}, error

        def fetch(url):  # 默认：来源页上照着摘录写着这个数
            if pages is not None:
                return pages.get(url)
            row = next(c for c in (reply or {}).get("companies", []) if c["url"] == url)
            return f"<table><tr><td>Oct 1, 2026</td><td>{row['excerpt']}</td></tr></table>"
        result = marketcap.refresh(self.root, None, self.root, as_of="2026-10-01", today="2026-10-22", call=call, fetch=fetch)
        return result, calls

    def test_writes_only_when_everything_checks(self):
        result, calls = self.run_with(answer())
        self.assertTrue(result["written"])
        self.assertIn("2026-10-01", calls[0][0])
        self.assertIn("AAA：甲公司", calls[0][0])
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["as_of"], "2026-10-01")
        self.assertEqual([c["usd"] for c in saved["companies"]], [1600000000000, 900000000000, 1200000000000])

        before = self.path.read_bytes()
        result, _ = self.run_with(answer(CCC=None))
        self.assertFalse(result["written"])
        self.assertEqual(self.path.read_bytes(), before, "对不上就整份不写，留着上次的")
        result, _ = self.run_with(None, error="ApiTimeout：超时")
        self.assertEqual((result["written"], result["problems"]), (False, ["ApiTimeout：超时"]))
        self.assertEqual(self.path.read_bytes(), before)

    def test_one_person_two_companies_are_compared_company_by_company(self):
        """一人兼两家（乙二：BBB 小、BBX 大）：各家拿各家的旧数比，不报“变动过大”，两条都写回。"""
        entries = [{**ENTRIES[0], "usd": 1.5e12}, {**ENTRIES[1], "usd": 3.0e11}, {**ENTRIES[2], "usd": 1.1e12},
                   {"person": "b-two", "company": "乙二公司", "ticker": "BBX", "usd": 2.0e12}]
        self.path.write_text(json.dumps({"as_of": "2026-09-01", "companies": entries}, ensure_ascii=False), encoding="utf-8")
        result, calls = self.run_with(answer(BBB=3.1e11, BBX=2.05e12))
        self.assertEqual(result["problems"], [])
        self.assertTrue(result["written"])
        self.assertIn("BBX：乙二公司", calls[0][0])
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual([(c["person"], c["ticker"], c["usd"]) for c in saved["companies"] if c["person"] == "b-two"],
                         [("b-two", "BBB", 310000000000), ("b-two", "BBX", 2050000000000)])

    def test_source_page_must_show_the_number(self):
        reply = answer()
        wrong = {c["url"]: "<td>Oct 1, 2026</td><td>1.0</td>" for c in reply["companies"]}
        result, _ = self.run_with(reply, pages=wrong)
        self.assertFalse(result["written"])
        self.assertTrue(all("来源页上没有" in p for p in result["problems"]), result["problems"])
        result, _ = self.run_with(reply, pages={})
        self.assertTrue(result["written"], "来源页打不开的不算对不上")
        self.assertEqual(result["unreachable"], ["AAA", "BBB", "CCC"])


class RankedRosterTest(unittest.TestCase):
    """名单页：有市值的从大到小排进够得着的层，没查到数的留在原来那一组、排在后面，市值文件里没有的进“未上市公司”。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root).load()
        for pid, name in (("a-one", "甲一"), ("b-two", "乙二"), ("c-three", "丙三"), ("d-four", "丁四"), ("e-five", "戊五")):
            role = f"{name}公司首席执行官"
            self.store.save_person(make_record(pid, name, [_role(role)], [_career("2003", role, "", ongoing=True)], bureaus.AI))
        (self.root / "rosters").mkdir()
        roster = {"id": "business", "title": "商界",
                  "market_cap": {"tiers": [{"title": "市值万亿美元以上", "min": 1000000000000}, {"title": "市值万亿美元以下", "min": 0}],
                                 "unlisted": "未上市公司"},
                  "groups": [{"title": "市值万亿美元以上", "style": "grid", "members": [{"person": "a-one", "post": "甲"}, {"person": "b-two", "post": "乙"}]},
                             {"title": "市值万亿美元以下", "style": "grid", "members": [{"person": "c-three", "post": "丙"}, {"person": "d-four", "post": "丁"}]},
                             {"title": "未上市公司", "style": "grid", "members": [{"person": "e-five", "post": "戊"}]}]}
        (self.root / "rosters" / "business.json").write_text(json.dumps(roster, ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def titles(self):
        view = rosters.view(self.store, rosters.load(self.root)["business"])
        return [(g["title"], [m["id"] for m in g["members"]]) for g in view["groups"]]

    def test_tiers_follow_the_caps_file(self):
        self.assertEqual(self.titles(), [("市值万亿美元以上", ["a-one", "b-two"]), ("市值万亿美元以下", ["c-three", "d-four"]),
                                         ("未上市公司", ["e-five"])], "没有市值文件就照名单文件")
        before = rosters.stamp(self.root)
        caps = {"as_of": "2026-10-01", "companies": [
            {"person": "a-one", "ticker": "AAA", "usd": 1.6e12}, {"person": "b-two", "ticker": "BBB", "usd": 9.0e11},
            {"person": "c-three", "ticker": "CCC", "usd": 2.4e12}, {"person": "d-four", "ticker": "DDD", "usd": None}]}
        (self.root / "rosters" / "market-caps.json").write_text(json.dumps(caps), encoding="utf-8")
        self.assertNotEqual(rosters.stamp(self.root), before, "市值文件变了，名单页重读")
        self.assertEqual(self.titles(), [("市值万亿美元以上", ["c-three", "a-one"]), ("市值万亿美元以下", ["b-two", "d-four"]),
                                         ("未上市公司", ["e-five"])])

    def test_one_person_two_companies_counts_the_bigger_one(self):
        """一人兼两家上市公司，按大的那家排：乙二的 BBX（2.0 万亿）比 BBB（0.9 万亿）大，排进以上、在丙三和甲一之间；
        大的那条写在前面也一样（丙三）；没数的那条不顶替有数的（丁四在名单文件里是“以下”，按 DDD 1.2 万亿进“以上”）。"""
        caps = {"as_of": "2026-10-01", "companies": [
            {"person": "a-one", "ticker": "AAA", "usd": 1.6e12},
            {"person": "b-two", "ticker": "BBB", "usd": 9.0e11}, {"person": "b-two", "ticker": "BBX", "usd": 2.0e12},
            {"person": "c-three", "ticker": "CCC", "usd": 2.4e12}, {"person": "c-three", "ticker": "CCX", "usd": 1.0e11},
            {"person": "d-four", "ticker": "DDD", "usd": 1.2e12}, {"person": "d-four", "ticker": "DDX", "usd": None}]}
        (self.root / "rosters" / "market-caps.json").write_text(json.dumps(caps), encoding="utf-8")
        self.assertEqual(self.titles(), [("市值万亿美元以上", ["c-three", "b-two", "a-one", "d-four"]), ("未上市公司", ["e-five"])],
                         "“以下”这一层没人，空组不出")


if __name__ == "__main__":
    unittest.main()
