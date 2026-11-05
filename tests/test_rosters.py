"""五栏名单（zuzhibu/rosters.py）：照名单文件的分组和顺序给界面数据；名单外的人没有人物页、不进搜索索引；
领导分组从同栏的议员条目取党派、州；短称只供显示；照片署名只列名单里要署名的照片。人物全部虚构，数据放临时目录。"""

import json
import tempfile
import unittest
from pathlib import Path

from zuzhibu import bureaus, rosters
from zuzhibu.store import Store

from tests.test_personnel import _career, _role, make_record


def write_roster(root: Path, sid: str, data: dict) -> None:
    (root / "rosters").mkdir(parents=True, exist_ok=True)
    (root / "rosters" / f"{sid}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class RostersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root).load()
        for pid, name, role, bureau in (("bai-yuan", "白远", "北岸国总统", bureaus.WHITEHOUSE), ("lin-mu", "林木", "美国联邦参议员（北岸州）", bureaus.CONGRESS),
                                        ("he-shan", "何山", "青岚科技公司首席执行官", bureaus.AI), ("wu-ying", "吴影", "美国联邦众议员（北岸州第1选区）", bureaus.CONGRESS)):
            self.store.save_person(make_record(pid, name, [_role(role)], [_career("2003", role, "", ongoing=True)], bureau))
        write_roster(self.root, "whitehouse", {"id": "whitehouse", "title": "白宫", "groups": [
            {"title": "总统", "style": "lead", "members": [{"person": "bai-yuan", "post": "北岸国总统"}]},
            {"title": "空组", "style": "grid", "members": [{"person": "nobody", "post": "库里没有的人"}]},
            {"title": "", "style": "wall", "members": [{"person": "he-shan", "post": "青岚科技公司首席执行官、董事长", "short_post": "青岚科技首席执行官"}]},
        ]})
        write_roster(self.root, "congress", {"id": "congress", "title": "国会", "groups": [
            {"title": "参议院领导", "style": "row", "members": [{"person": "lin-mu", "post": "参议院多数党领袖"}]},
            {"title": "参议员", "style": "grid", "members": [{"person": "lin-mu", "post": "北岸州", "party": "共和党", "state": "北岸州"}]},
            {"title": "众议员", "style": "grid", "members": [{"person": "wu-ying", "post": "北岸州第1选区", "party": "民主党", "state": "北岸州", "district": 1}]},
        ]})
        (self.root / "rosters" / "defense.json").write_text("{不是 JSON", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_load_gives_every_section_and_empty_ones_for_missing_files(self):
        loaded = rosters.load(self.root)
        self.assertEqual(list(loaded), ["whitehouse", "business", "congress", "defense", "justice"])
        self.assertEqual(loaded["whitehouse"]["title"], "白宫")
        self.assertEqual(loaded["defense"], {"id": "defense", "title": "国防", "groups": []}, "读不了的名单当空名单")
        self.assertEqual(loaded["justice"]["groups"], [])

    def test_view_keeps_groups_and_order(self):
        view = rosters.view(self.store, rosters.load(self.root)["whitehouse"])
        self.assertEqual([g["title"] for g in view["groups"]], ["总统", ""], "库里没有的人跳过，空分组不出")
        self.assertEqual(view["groups"][0]["members"][0]["name"], "白远")
        self.assertEqual(view["groups"][1]["style"], "grid", "不认识的版式按网格")
        self.assertEqual(view["groups"][1]["members"][0]["post"], "青岚科技首席执行官", "有短称用短称")

    def test_leaders_get_party_and_state_from_their_member_entry(self):
        view = rosters.view(self.store, rosters.load(self.root)["congress"])
        leader = view["groups"][0]["members"][0]
        self.assertEqual((leader["post"], leader["party"], leader["state"]), ("参议院多数党领袖", "共和党", "北岸州"))
        self.assertEqual([g["filter"] for g in view["groups"]], [False, True, True], "filter 只标议员名单：身份、党派、州从这里取")
        self.assertEqual(view["groups"][2]["members"][0]["district"], 1)
        self.assertEqual(view["groups"][1]["members"][0]["name_en"], "Lin Mu", "卡片带英文名，供国会页按英文名搜索")

    def test_real_congress_roster_has_what_the_filters_need(self):
        """国会页的筛选用名单里的 party、state、district：议员名单每人都有党派、州；众议员名单里没有选区的正好是无表决权代表。"""
        groups = rosters.load()["congress"]["groups"]
        lists = [g for g in groups if any(m.get("party") or m.get("state") for m in g.get("members") or [])]
        self.assertEqual([g["title"] for g in lists], ["参议员", "众议员"])
        for g in lists:
            for m in g["members"]:
                self.assertIn(m.get("party"), ("共和党", "民主党", "无党派"), m["person"])
                self.assertTrue(m.get("state"), m["person"])
        senate, house = lists
        self.assertFalse(any("district" in m for m in senate["members"]))
        for m in house["members"]:
            self.assertEqual("district" not in m, "无表决权" in m["post"], m["person"])

    def test_only_listed_people_are_indexed_and_have_pages(self):
        loaded = rosters.load(self.root)
        self.assertEqual(list(rosters.listed(loaded)), ["bai-yuan", "nobody", "he-shan", "lin-mu", "wu-ying"])
        index = rosters.index(self.store, loaded)
        self.assertEqual([p["id"] for p in index], ["bai-yuan", "he-shan", "lin-mu", "wu-ying"])
        self.assertEqual(index[2]["post"], "参议院多数党领袖", "同一人取第一次出现的职务")
        page = rosters.person(self.store, loaded, "lin-mu")
        self.assertEqual(page["title"], "林木简历")
        self.assertEqual(page["sections"], ["congress"])
        self.assertTrue(page["lead"].startswith("林木，男"))
        self.assertTrue(page["careers"])
        self.assertNotIn("sources", page)
        write_roster(self.root, "congress", {"id": "congress", "groups": []})
        self.assertIsNone(rosters.person(self.store, rosters.load(self.root), "lin-mu"), "不在名单里就没有人物页")

    def test_stamp_changes_when_a_roster_changes(self):
        before = rosters.stamp(self.root)
        write_roster(self.root, "justice", {"id": "justice", "title": "司法", "groups": []})
        self.assertNotEqual(before, rosters.stamp(self.root))

    def test_credits_list_only_listed_people_whose_photos_need_attribution(self):
        portraits = self.root / "portraits"
        portraits.mkdir()
        (portraits / "index.csv").write_text(
            "person_id,file,source_kind,source,source_url,license,processing,date\n"
            "bai-yuan,bai-yuan.jpg,官方照片,北岸国政府,https://example.com/a.jpg,公有领域：政府作品,裁切,2026-10-21\n"
            "he-shan,he-shan.jpg,图库,示例图库,https://example.com/b.jpg,CC BY 2.0：张三（示例图库）,裁切,2026-10-21\n"
            "outsider,outsider.jpg,图库,示例图库,https://example.com/c.jpg,CC BY 4.0：李四,裁切,2026-10-21\n", encoding="utf-8")
        for pid in ("bai-yuan", "he-shan"):
            rec = self.store.person(pid)
            rec["portrait"] = {"file": pid + ".jpg"}
            self.store.save_person(rec)
        credits = rosters.credits(self.store, rosters.load(self.root))
        self.assertEqual(credits, [{"id": "he-shan", "name": "何山", "credit": "张三（示例图库），CC BY 2.0，有裁切"}])


if __name__ == "__main__":
    unittest.main()
