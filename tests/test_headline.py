"""一局动态的标题：程序按结构化字段生成，人民网人事新闻式，不带英文、编号、日期和取证说明。人物、机构全部虚构。"""

import re
import tempfile
import unittest
from pathlib import Path

from bureaus.dispatch import research
from zuzhibu import bureaus, headline, server
from zuzhibu.store import Store

from tests.test_personnel import _role, make_record


def role(org, title, sector="politics", org_en="Northshore Agency", title_en="Director"):
    return {"organization": org, "role": title, "organization_en": org_en, "role_en": title_en, "start": None, "end": None,
            "concurrent": False, "sector": sector, "source_ids": ["e1"]}


def event(kind, pid="yuan-bai", new=(), ended=(), name_en="Yuan Bai"):
    return {"id": f"2026-09-01-{pid}-{kind}", "date": "2026-09-01", "kind": kind, "person_id": pid,
            "person": {"name_en": name_en, "name_zh": "研究会话写的名字"}, "announced_at": "2026-09-01", "effective_at": None,
            "new_roles": list(new), "ended_roles": list(ended),
            "headline": "Yuan Bai 获任（PN901-2，2026-09-01，报道未列宣布日）", "summary": "本次检索未发现……"}


class HeadlineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name)).load()
        president = make_record("president", "唐纳德·特朗普", [_role("美国总统")], [], bureaus.WHITEHOUSE)
        self.store.save_person(president)
        self.store.save_person(make_record("yuan-bai", "白远", [], [], bureaus.WHITEHOUSE))

    def tearDown(self):
        self.tmp.cleanup()

    def h(self, ev, supplement=None):
        return headline.event_headline(ev, self.store, supplement or {})

    def test_each_kind(self):
        labor = role("美国劳工部", "部长")
        cases = [
            (event("appoint", new=[labor]), "白远就任美国劳工部部长"),
            (event("appoint", new=[role("青岚科技公司", "首席执行官", "enterprise")]), "白远出任青岚科技公司首席执行官"),
            (event("confirm", new=[labor]), "美国参议院确认白远出任美国劳工部部长"),
            (event("nominate", new=[role("美国海军部", "部长提名人选")]), "特朗普提名白远为美国海军部部长人选"),
            (event("announce", new=[labor]), "白宫宣布白远将出任美国劳工部部长"),
            (event("announce", new=[role("青岚科技公司", "首席执行官", "enterprise")]), "白远将出任青岚科技公司首席执行官"),
            (event("leave", ended=[role("白宫", "新闻秘书")]), "白远卸任白宫新闻秘书"),
            (event("transfer", ended=[role("美国海军部", "副部长")], new=[role("美国海军部", "部长")]), "白远由美国海军部副部长转任部长"),
            (event("acting", new=[role("美国国家情报总监办公室", "代理国家情报总监")]), "白远代理美国国家情报总监"),
            (event("death", ended=[role("美国联邦参议员（北岸州）", ""), role("美国参议院预算委员会", "主席", "committee")]),
             "美国联邦参议员（北岸州）白远逝世"),
        ]
        for ev, expected in cases:
            with self.subTest(kind=ev["kind"]):
                self.assertEqual(self.h(ev), expected)

    def test_posts_are_written_the_house_way(self):
        envoy = role("美国驻北岸国大使馆", "驻北岸国特命全权大使")
        self.assertEqual(self.h(event("confirm", new=[envoy])), "美国参议院确认白远出任美国驻北岸国特命全权大使")
        self.assertEqual(self.h(event("appoint", new=[role("美国司法部", "司法部长")])), "白远就任美国司法部长")
        board = [role("青岚科技公司", "董事", "enterprise"), role("青岚科技公司", "审计委员会委员", "committee"),
                 role("青岚科技公司", "安全委员会委员", "committee")]
        self.assertEqual(self.h(event("appoint", new=board)), "白远出任青岚科技公司董事", "有主职时委员会委员不进标题")
        many = [role(f"美国驻{c}大使馆", "特命全权大使") for c in ("北岸国", "南湖国", "西岭国")]
        self.assertEqual(self.h(event("nominate", new=many)), "特朗普提名白远为美国驻北岸国特命全权大使等3个职务人选")

    def test_no_english_numbers_dates_or_research_notes(self):
        text = self.h(event("confirm", new=[role("美国劳工部", "部长", title_en="Secretary of Labor")]))
        self.assertIsNone(re.search(r"[A-Za-z]|PN|\d{4}|报道|检索|未查清", text), text)

    def test_name_falls_back_from_record_to_supplement_to_english(self):
        self.assertEqual(headline.person_name(event("appoint"), self.store, {})[0], "白远")
        nameless = make_record("shan-he", "", [], [], bureaus.WHITEHOUSE)
        self.store.save_person(nameless)
        ev = event("appoint", pid="shan-he", new=[role("美国劳工部", "部长")], name_en="Shan He")
        self.assertEqual(headline.person_name(ev, self.store, {"shan-he": "何山"}), ("何山", "supplement"))
        self.assertEqual(headline.person_name(ev, self.store, {}), ("Shan He", "english"))
        self.assertTrue(self.h(ev).startswith("Shan He 就任"))

    def test_server_gives_generated_headline_and_no_summary(self):
        ev = event("appoint", new=[role("美国劳工部", "部长")])
        out = server.event_summary(ev, self.store)
        self.assertEqual(out["headline"], headline.event_headline(ev, self.store))
        self.assertNotIn("summary", out)

    def test_prompts_keep_headline_and_summary_internal(self):
        self.assertIn("只作内部记录", research.research_prompt(today="2026-10-21", since="2026-10-01", until="2026-10-21",
                                                       scope=(bureaus.AI,), leads=[], people=[], min_searches=10))
        self.assertIn("只作内部记录", research.verify_prompt(today="2026-10-21", events=[]))


if __name__ == "__main__":
    unittest.main()
