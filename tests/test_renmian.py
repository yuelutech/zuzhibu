"""人事动态的公开内容（zuzhibu/renmian.py）：只公开落地的任免结果，同一次任职只出一项，合篇只合同一天同一决定主体的事，
正文按字段套模板。人物、公司、国名、选区全部虚构。"""

import re
import tempfile
import unittest
from pathlib import Path

from zuzhibu import bureaus, renmian
from zuzhibu.store import DEFAULT_DATA, Store, write_json

from tests.test_personnel import make_record

TODAY = "2026-10-22"
PEOPLE = {"yuan-bai": "白远", "shan-he": "何山", "lan-qing": "青岚", "mu-yun": "云木", "song-lin": "林松", "jin-xia": "夏瑾"}


def role(org, title, sector="politics", org_en="", title_en="", sid="e1"):
    return {"organization": org, "role": title, "organization_en": org_en, "role_en": title_en, "start": None, "end": None,
            "concurrent": False, "sector": sector, "source_ids": [sid]}


def src(sid="e1", url="", publisher="The White House", supports=None):
    out = {"id": sid, "url": url or f"https://www.whitehouse.gov/briefings/{sid}", "title": "公告（示例）", "publisher": publisher,
           "published_at": None, "excerpt": "示例摘录", "checked_at": TODAY, "excerpt_check": "ok"}
    if supports:
        out["supports"] = supports
    return out


class RenmianTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root).load()
        for pid, name in PEOPLE.items():
            self.store.save_person(make_record(pid, name, [], [], bureaus.WHITEHOUSE))
        write_json(self.root / "rosters" / "whitehouse.json", {"id": "whitehouse", "title": "白宫", "groups": [
            {"title": "内阁成员", "style": "grid", "members": [{"person": "jin-xia", "post": "内政部部长"},
                                                            {"person": "yuan-bai", "post": "劳工部部长"}]}]})

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, kind, pid, date, new=(), ended=(), announced="same", effective=None, sources=None, merged=None, status="verified"):
        ev = {"id": f"{date}-{pid}-{kind}", "date": date, "kind": kind, "person_id": pid,
              "person": {"name_en": pid.replace("-", " ").title(), "name_zh": "研究会话写的名字"},
              "announced_at": date if announced == "same" else announced, "effective_at": effective,
              "new_roles": list(new), "ended_roles": list(ended), "sources": sources or [src()],
              "headline": "Yuan Bai 获任（PN901-2，报道未列宣布日）", "summary": "本次检索未发现……",
              "verification": {"status": status, **({"merged": merged} if merged else {})}}
        self.store.save_event(ev)
        return ev

    def arts(self, **switches):
        return renmian.articles(self.store, TODAY, **switches)

    def results(self, **switches):
        return renmian.results(self.store, TODAY, **switches)

    # ─────────────────────────── 只公开落地的结果

    def test_chinese_companies_are_not_published(self):
        ev = self.event("acting", "song-lin", "2026-10-01", new=[role("华为技术有限公司", "当值轮值董事长", "enterprise")],
                        effective="2026-10-01")
        self.assertEqual(renmian.is_publishable(ev, self.store, TODAY), (False, renmian.CHINESE_COMPANY))
        self.assertEqual(self.arts(), [])

    def test_nomination_is_not_published(self):
        ev = self.event("nominate", "yuan-bai", "2026-09-14", new=[role("美国劳工部", "部长提名人选")])
        self.assertEqual(renmian.is_publishable(ev, self.store, TODAY), (False, renmian.NOMINATED))
        self.assertEqual(self.arts(), [])
        self.assertEqual(renmian.stats(self.store, TODAY)["unpublished"], {renmian.NOMINATED: 1})

    def test_future_announcement_is_not_published(self):
        future = self.event("announce", "yuan-bai", "2026-09-03", new=[role("青岚科技公司", "首席执行官", "enterprise")],
                            effective="2026-12-01")
        undated = self.event("announce", "shan-he", "2026-09-28", new=[role("青岚科技公司", "首席科学家", "enterprise")])
        later = self.event("appoint", "lan-qing", "2026-10-05", new=[role("北岸银行", "董事", "enterprise")], effective="2026-11-01")
        self.assertEqual(renmian.is_publishable(future, self.store, TODAY), (False, renmian.ANNOUNCED_FUTURE))
        self.assertEqual(renmian.is_publishable(undated, self.store, TODAY), (False, renmian.ANNOUNCED))
        self.assertEqual(renmian.is_publishable(later, self.store, TODAY), (False, renmian.FUTURE))
        self.assertEqual(self.arts(), [])

    def test_unverified_or_undated_events_stay_in_the_backend(self):
        held = self.event("appoint", "yuan-bai", "2026-09-01", new=[role("美国劳工部", "部长")], effective="2026-09-01", status="held")
        undated = self.event("appoint", "shan-he", "2026-10-20", new=[role("美国驻北岸国大使馆", "大使")], announced=None)
        self.assertEqual(renmian.is_publishable(held, self.store, TODAY), (False, renmian.NOT_VERIFIED))
        self.assertEqual(renmian.is_publishable(undated, self.store, TODAY), (False, renmian.NO_DATE), "只有采集日期，不能当发布时间")
        self.assertEqual(self.arts(), [])

    # ─────────────────────────── 同一次任职只出一项

    def test_confirmation_then_swearing_in_is_one_result(self):
        labor = role("美国劳工部", "部长", org_en="U.S. Department of Labor", title_en="Secretary of Labor")
        self.event("confirm", "yuan-bai", "2026-08-07", new=[labor], sources=[src(publisher="United States Senate",
                                                                                   url="https://www.senate.gov/x")])
        sworn = self.event("appoint", "yuan-bai", "2026-08-21", new=[dict(labor, organization_en="United States Department of Labor")],
                           effective="2026-08-21", announced="2026-09-15")
        self.assertEqual(renmian.is_publishable(sworn, self.store, TODAY), (True, "到任"), "能公开，只是并进确认那一项")
        res = self.results()
        self.assertEqual(len(res), 1)
        self.assertEqual((res[0]["how"], res[0]["date"], res[0]["decider"]), ("confirm", "2026-08-07", "美国参议院"))
        self.assertEqual([(d["event"], d["date"]) for d in res[0]["duplicates"]], [(sworn["id"], "2026-08-21")])
        arts = self.arts()
        self.assertEqual([a["title"] for a in arts], ["美国参议院确认白远为美国劳工部部长"])
        self.assertEqual(arts[0]["paragraphs"], ["美国参议院8月7日确认白远为美国劳工部部长。"])
        self.assertEqual(renmian.stats(self.store, TODAY)["duplicates"], 1)

    def test_switch_off_publishes_the_swearing_in_instead_of_the_confirmation(self):
        confirm = self.event("confirm", "yuan-bai", "2026-08-07", new=[role("美国劳工部", "部长")])
        self.event("appoint", "yuan-bai", "2026-08-21", new=[role("美国劳工部", "部长")], effective="2026-08-21")
        self.assertEqual(renmian.is_publishable(confirm, self.store, TODAY, confirmations=False), (False, renmian.CONFIRM_HIDDEN))
        arts = self.arts(confirmations=False)
        self.assertEqual([(a["title"], a["date"]) for a in arts], [("白远任美国劳工部部长", "2026-08-21")])

    def test_sworn_in_confirmation_counts_from_the_vote_day(self):
        """确认动态的日期写成了宣誓日：确认那一项仍按表决日（宣布日）发布，宣誓只是并进去的重复报道。"""
        self.event("confirm", "yuan-bai", "2026-08-21", new=[role("美国劳工部", "部长")], announced="2026-08-07", effective="2026-08-21",
                   sources=[src(url="https://www.dol.gov/news", supports=["effective_at"])])
        self.event("confirm", "shan-he", "2026-08-07", new=[role("美国消费品安全委员会", "委员")])
        arts = self.arts()
        self.assertEqual([(a["title"], a["date"]) for a in arts], [("美国参议院确认一批人事任命", "2026-08-07")])
        self.assertEqual(arts[0]["paragraphs"], ["美国参议院8月7日确认白远为美国劳工部部长，何山为美国消费品安全委员会委员。"])
        arts = self.arts(confirmations=False)
        self.assertEqual([(a["title"], a["date"]) for a in arts], [("白远任美国劳工部部长", "2026-08-21")], "关掉确认时宣誓到任单独公开")

    def test_two_reports_of_one_appointment_keep_the_exact_day(self):
        self.event("appoint", "shan-he", "2026-10-01", new=[role("美国驻北岸国大使馆", "大使")], effective="2026-10", announced="2026-10-14")
        self.event("appoint", "shan-he", "2026-10-15", new=[role("美国驻北岸国大使馆", "驻北岸国特命全权大使")], effective="2026-10-15")
        res = self.results()
        self.assertEqual([(r["date"], r["precision"]) for r in res], [("2026-10-15", "day")])
        self.assertEqual([a["title"] for a in self.arts()], ["何山任美国驻北岸国特命全权大使"])

    def test_acting_then_permanent_counts_twice(self):
        self.event("acting", "yuan-bai", "2026-06-01", new=[role("美国劳工部", "代理部长")], effective="2026-06-01", announced="2026-05-12")
        self.event("appoint", "yuan-bai", "2026-09-01", new=[role("美国劳工部", "部长")], ended=[role("美国劳工部", "代理部长")],
                   effective="2026-09-01")
        res = self.results()
        self.assertEqual([(r["change"], r["date"]) for r in res], [("任", "2026-09-01"), ("代理", "2026-06-01")],
                         "转正的那条不再另出“不再代理”")
        self.assertEqual([a["paragraphs"] for a in self.arts()],
                         [["白远9月1日任美国劳工部部长。"], ["白远6月1日起代理美国劳工部部长。"]])

    def test_similar_posts_and_seats_do_not_override_each_other(self):
        court = "美国联邦第十五巡回上诉法院"
        self.event("appoint", "yuan-bai", "2026-08-01", new=[role(court, "法官")], effective="2026-08-01")
        self.event("transfer", "yuan-bai", "2026-10-01", new=[role(court, "首席法官")], ended=[role(court, "法官")], effective="2026-10-01")
        deputy = "Deputy Secretary of the Treasury"
        self.event("confirm", "shan-he", "2026-08-07", new=[role("美国财政部", "副部长", title_en=deputy, org_en="Department of the Treasury")])
        self.event("appoint", "shan-he", "2026-09-22", new=[role("美国财政部", "常务副部长", title_en=deputy, org_en="Department of the Treasury")])
        self.event("appoint", "lan-qing", "2026-09-02", new=[role("美国联邦众议员（北岸州第3选区）", "")], effective="2026-09-02")
        self.event("appoint", "mu-yun", "2026-09-02", new=[role("美国联邦众议员（北岸州第4选区）", "")], effective="2026-09-02")
        self.event("appoint", "song-lin", "2026-10-09", new=[role("美国最高法院", "大法官")], effective="2026-10-09")
        self.event("appoint", "jin-xia", "2026-10-09", new=[role("美国最高法院", "大法官")], effective="2026-10-09")
        texts = sorted(r["text"] for r in self.results())
        self.assertEqual(texts, sorted([
            "白远任美国联邦第十五巡回上诉法院法官", "白远任美国联邦第十五巡回上诉法院首席法官", "白远不再担任美国联邦第十五巡回上诉法院法官",
            "美国参议院确认何山为美国财政部副部长", "何山任美国财政部常务副部长",
            "青岚任美国联邦众议员（北岸州第3选区）", "云木任美国联邦众议员（北岸州第4选区）",
            "林松任美国最高法院大法官", "夏瑾任美国最高法院大法官"]))
        titles = {a["title"]: a["paragraphs"] for a in self.arts()}
        self.assertIn("青岚任美国联邦众议员（北岸州第3选区）", titles, "不同选区不合篇")
        self.assertIn("云木任美国联邦众议员（北岸州第4选区）", titles)
        self.assertEqual(titles["美国最高法院人事调整"], ["10月9日，夏瑾、林松任美国最高法院大法官。"], "名单里的人在前，同一职务合写")
        self.assertEqual(titles["白远任美国联邦第十五巡回上诉法院首席法官"],
                         ["白远10月1日任美国联邦第十五巡回上诉法院首席法官，不再担任法官。"])

    # ─────────────────────────── 名单内外、合篇

    def test_off_roster_people_are_named_only(self):
        ev = self.event("appoint", "shan-he", "2026-09-08", new=[role("美国消费品安全委员会", "委员")], effective="2026-09-08")
        self.event("appoint", "yuan-bai", "2026-09-09", new=[role("美国劳工部", "部长")], effective="2026-09-09")
        arts = {a["title"]: a for a in self.arts()}
        self.assertEqual(arts["何山任美国消费品安全委员会委员"]["people"], [{"person_id": "shan-he", "name": "何山", "in_roster": False}])
        self.assertEqual(arts["白远任美国劳工部部长"]["people"], [{"person_id": "yuan-bai", "name": "白远", "in_roster": True}])
        self.assertEqual(renmian.is_publishable(ev, self.store, TODAY, include_off_roster=False), (False, renmian.OFF_ROSTER))
        self.assertEqual([a["title"] for a in self.arts(include_off_roster=False)], ["白远任美国劳工部部长"])

    def test_senate_confirmations_of_one_day_make_one_article_with_sources_per_fact(self):
        senate = "https://www.senate.gov/legislative/nom_confc.htm"
        self.event("confirm", "lan-qing", "2026-08-07", new=[role("美国消费品安全委员会", "委员")],
                   sources=[src(url=senate, publisher="United States Senate")])
        self.event("confirm", "shan-he", "2026-08-07", new=[role("美国驻北岸国大使馆", "特命全权大使")],
                   sources=[src(url="https://www.state.gov/biographies/shan-he", publisher="U.S. Department of State")])
        self.event("confirm", "yuan-bai", "2026-08-07", new=[role("美国劳工部", "部长")],
                   sources=[src(url="https://www.dol.gov/agencies/osec", publisher="United States Senate")])
        self.event("confirm", "mu-yun", "2026-08-08", new=[role("美国司法部", "司法部长")])
        arts = self.arts()
        self.assertEqual([a["title"] for a in arts], ["美国参议院确认云木为美国司法部长", "美国参议院确认一批人事任命"])
        batch = arts[1]
        self.assertEqual(batch["id"], "2026-08-07-senate")
        self.assertEqual(batch["paragraphs"],
                         ["美国参议院8月7日确认白远为美国劳工部部长，何山为美国驻北岸国特命全权大使，青岚为美国消费品安全委员会委员。"])
        self.assertNotIn("；确认", batch["paragraphs"][0])
        self.assertEqual([[s["url"] for s in f["sources"]] for f in batch["sources"]],
                         [["https://www.dol.gov/agencies/osec"], ["https://www.state.gov/biographies/shan-he"], [senate]],
                         "每项事实带自己的来源，不拿第一项的来源顶替")
        self.assertEqual(batch["source_line"], "美国参议院、美国国务院", "发布机构写中文（译名表）")
        self.assertEqual([p["in_roster"] for p in batch["people"]], [True, False, False])

    def test_merges_only_the_same_day_and_the_same_organization(self):
        self.event("appoint", "mu-yun", "2026-10-07", new=[role("青岚科技公司", "首席执行官", "enterprise")], effective="2026-10-07")
        self.event("leave", "song-lin", "2026-10-07", ended=[role("青岚科技公司", "临时首席执行官", "enterprise")], effective="2026-10-07")
        self.event("appoint", "lan-qing", "2026-10-07", new=[role("北岸银行", "首席执行官", "enterprise")], effective="2026-10-07")
        self.event("leave", "shan-he", "2026-10-08", ended=[role("青岚科技公司", "首席财务官", "enterprise")], effective="2026-10-08")
        arts = {a["title"]: a for a in self.arts()}
        self.assertEqual(sorted(arts), ["何山不再担任青岚科技公司首席财务官", "青岚任北岸银行首席执行官", "青岚科技公司人事调整"])
        self.assertEqual(arts["青岚科技公司人事调整"]["paragraphs"],
                         ["10月7日，云木任青岚科技公司首席执行官。", "林松不再担任青岚科技公司临时首席执行官。"])

    def test_merged_article_without_appointments_writes_one_paragraph(self):
        self.event("leave", "mu-yun", "2026-09-03", ended=[role("美国太空军", "太空作战部长")], effective="2026-09-03")
        self.event("leave", "song-lin", "2026-09-03", ended=[role("美国太空军", "副太空作战部长")], effective="2026-09-03")
        arts = self.arts()
        self.assertEqual([(a["title"], a["paragraphs"]) for a in arts],
                         [("美国太空军人事调整", ["9月3日，云木不再担任美国太空军太空作战部长，林松不再担任副太空作战部长。"])])

    # ─────────────────────────── 写法和日期

    def test_wording_follows_the_facts(self):
        self.event("leave", "yuan-bai", "2026-09-02", ended=[role("美国陆军部", "部长")], effective="2026-09-02")
        self.event("acting", "shan-he", "2026-09-03", new=[role("美国陆军部", "部长")], effective="2026-09-03")
        self.event("acting", "lan-qing", "2026-10-01", new=[role("北岸电信公司", "当值轮值董事长", "enterprise")], effective="2026-10-01")
        self.event("transfer", "mu-yun", "2026-09-01", new=[role("青岚科技公司", "执行董事长", "enterprise")],
                   ended=[role("青岚科技公司", "首席执行官", "enterprise")], effective="2026-09-01", announced="2026-04-20")
        self.event("death", "song-lin", "2026-07-11", ended=[role("美国联邦参议员（北岸州）", ""), role("美国参议院预算委员会", "主席", "committee")],
                   effective="2026-07-11", announced="2026-07-12")
        month_only = self.event("leave", "jin-xia", "2026-06-01", ended=[role("北岸银行", "董事长", "enterprise")], effective="2026-06",
                                announced=None)
        arts = {a["title"]: a for a in self.arts()}
        expected = {
            "白远不再担任美国陆军部部长": ["白远9月2日不再担任美国陆军部部长。"],
            "何山代理美国陆军部部长": ["何山9月3日起代理美国陆军部部长。"],
            "青岚任北岸电信公司当值轮值董事长": ["青岚10月1日任北岸电信公司当值轮值董事长。"],
            "云木任青岚科技公司执行董事长": ["云木9月1日任青岚科技公司执行董事长，不再担任首席执行官。"],
            "美国联邦参议员（北岸州）林松逝世": ["美国联邦参议员（北岸州）、美国参议院预算委员会主席林松7月11日逝世。"],
        }
        self.assertEqual({t: a["paragraphs"] for t, a in arts.items()}, expected)
        self.assertEqual(renmian.is_publishable(month_only, self.store, TODAY), (False, renmian.MONTH_ONLY),
                         "日期只知道到月的不公开，也不补日子（维护者 2026-10-22 定：精确到日）")
        self.assertEqual(arts["云木任青岚科技公司执行董事长"]["date"], "2026-09-01", "发布时间是事件日期，不是宣布日")
        for art in arts.values():
            text = art["title"] + "".join(art["paragraphs"])
            self.assertIsNone(re.search(r"免去|免职|辞职|任命|决定|提名|宣布|将|[A-Za-z]|PN|报道|检索|研究会话", text), text)

    def test_merged_confirmation_inside_a_kept_event_goes_to_its_vote_day(self):
        """写回时并进到任动态的参议院确认（verification.merged）按表决日进当天的确认合篇，来源用它自己的。"""
        self.event("appoint", "yuan-bai", "2026-08-21", new=[role("美国国家档案和记录管理局", "局长", sid="e1")], effective="2026-08-21",
                   announced="2026-09-15", sources=[src("e1", url="https://www.archives.gov/sworn"),
                                                   src("m1", url="https://www.senate.gov/nom", publisher="United States Senate")],
                   merged=[{"id": "2026-08-21-yuan-bai-confirm", "kind": "confirm", "announced_at": "2026-08-07",
                            "effective_at": "2026-08-21", "sources": ["m1"]}])
        self.event("confirm", "shan-he", "2026-08-07", new=[role("美国消费品安全委员会", "委员")])
        res = self.results()
        self.assertEqual([(r["how"], r["date"], [s["url"] for s in r["sources"]]) for r in res if r["person_id"] == "yuan-bai"],
                         [("confirm", "2026-08-07", ["https://www.senate.gov/nom"])])
        arts = self.arts()
        self.assertEqual([a["paragraphs"] for a in arts],
                         [["美国参议院8月7日确认白远为美国国家档案和记录管理局局长，何山为美国消费品安全委员会委员。"]])

    def test_stats(self):
        self.event("nominate", "lan-qing", "2026-09-14", new=[role("美国海军部", "部长")])
        self.event("confirm", "yuan-bai", "2026-08-07", new=[role("美国劳工部", "部长")])
        self.event("appoint", "yuan-bai", "2026-08-21", new=[role("美国劳工部", "部长")], effective="2026-08-21")
        self.event("appoint", "mu-yun", "2026-10-07", new=[role("青岚科技公司", "首席执行官", "enterprise")], effective="2026-10-07")
        self.event("leave", "song-lin", "2026-10-07", ended=[role("青岚科技公司", "临时首席执行官", "enterprise")], effective="2026-10-07")
        self.assertEqual(renmian.stats(self.store, TODAY), {
            "events": 5, "publishable_events": 4, "unpublished": {renmian.NOMINATED: 1}, "facts": 4, "results": 3,
            "duplicates": 1, "folded_events": 1, "articles": 2, "merged_articles": 1})

    def test_repository_data_keeps_the_rules(self):
        if not (DEFAULT_DATA / "events").is_dir():
            self.skipTest("没有仓库数据")
        store = Store(DEFAULT_DATA).load()
        arts = renmian.articles(store, TODAY)
        self.assertEqual(len({a["id"] for a in arts}), len(arts))
        for art in arts:
            text = art["title"] + "".join(art["paragraphs"])
            self.assertIsNone(re.search(r"提名|宣布|将出任|拟任|免去|免职|辞职|决定", text), text)
            self.assertTrue(art["paragraphs"] and art["sources"] and all(f["sources"] for f in art["sources"]), art["id"])
        seen = set()
        for res in renmian.results(store, TODAY):
            key = (res["person_id"], res["change"], res["post"], res["date"])
            self.assertNotIn(key, seen, key)
            seen.add(key)


if __name__ == "__main__":
    unittest.main()
