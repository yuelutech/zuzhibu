"""立即更新（假的 AI 接口，不联网、不花额度）：6 步上报进度；名单上的位置换了人，给新任取标准简历和官网照片，名单、人物记录、
照片、动态一起写；取不到的整组不写、下次接着取；停止后已核实的照样写回；接口额度不足时停下。人物、机构、网页全部虚构。"""

import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bureaus.dispatch import collect, research, run
from shared.backends import ai_api
from zuzhibu import bureaus, localupdates, personnel, progress, resume, rosterchange, rosters
from zuzhibu.store import Store, read_json, write_json

from tests.fakes.fake_ai_api import FAKE_KEY, FakeApi, openai_response
from tests.test_dispatch import NOMINATIONS_PAGE, NOMINATIONS_URL, _event, _nomination, _review
from tests.test_personnel import _role, make_record

TODAY = "2026-10-24"
PHOTO_PAGE = "https://www.lakeview.gov/leadership/feng"
PHOTO = "https://www.lakeview.gov/images/feng.jpg"
ARMY = {"organization": "美国陆军部", "role": "部长", "organization_en": "Department of the Army", "role_en": "Secretary of the Army",
        "start": "2026-10-20", "end": None, "concurrent": False, "sector": "politics", "source_ids": ["e1", "e2"]}


def _fact(value):
    return personnel.fact(value, ["s1"])


def _career(kind, start, end, org, role=None, **kw):
    return {"kind": kind, "start": _fact(start), "end": _fact(end) if end else None, "organization": org, "role": role,
            "program": None, "degree_awarded": None, "concurrent": False, "source_ids": ["s1"], "ongoing": end is None,
            "roles": [], "role_category": None, "role_basis": None, "study_level": None, "full_time": None, "annotations": [],
            "appointment_state": None, "stable_identity": False, **kw}


def resume_bundle(prompt: str) -> dict:
    """获取简历研究会话的回复：能过整理、核查、完整简历卡和写法检查的事实包，加官网照片。"""
    pid = re.search(r'subject_id="([^"]+)"', prompt).group(1)
    name = re.search(r"中文译名「([^」]+)」", prompt).group(1)
    item = personnel.new_resume(pid, name, "")
    item["person"].update(name=_fact(name), gender=_fact("男"), nationality=_fact("美国"), birth_date=_fact("1975-03"),
                          birth_place=_fact("俄亥俄州克利夫兰"), work_start=_fact("1997-07"), education=_fact("大学"),
                          degree=_fact("西点军校工程学学士"))
    item["sources"] = [{"id": "s1", "url": PHOTO_PAGE, "title": "Secretary of the Army", "published_at": None,
                        "checked_at": TODAY, "excerpt": "was sworn in as Secretary of the Army", "tier": 1}]
    item["current_roles"] = [{"organization": "美国陆军部", "role": "部长", "start": _fact("2026-10-20"), "concurrent": False,
                              "source_ids": ["s1"], "roles": [], "sector": "politics", "appointment_state": "正式"}]
    item["careers"] = [
        _career("study", "1993-07", "1997-06", "美国西点军校", program="工程学", degree_awarded=_fact("学士"), study_level="bachelor",
                full_time=True),
        _career("work", "1997-07", "2026-10", "美国陆军", "军官"),
        _career("work", "2026-10-20", None, "美国陆军部", "部长", appointment_state="正式"),
    ]
    item["unknowns"] = []
    return {"schema_version": "1.0", "date": TODAY, "items": [item], "portrait": {"image_url": PHOTO, "page_url": PHOTO_PAGE}}


CAPS_TIERS = {"tiers": [{"title": "市值万亿美元以上", "min": 1000000000000}, {"title": "市值万亿美元以下", "min": 0}],
              "unlisted": "未上市公司"}


def write_caps(data: Path) -> Path:
    """商界名单（按市值分层）和市值文件：青岚科技 1.6 万亿、蓝湾资本 9000 亿（2026-10-01 收盘），北岸智能未上市。"""
    write_json(data / "rosters" / "business.json", {"id": "business", "title": "商界", "market_cap": CAPS_TIERS, "groups": [
        {"title": "市值万亿美元以上", "note": "", "style": "grid", "members": [{"person": "jiang-yue", "post": "青岚科技公司首席执行官"}]},
        {"title": "市值万亿美元以下", "note": "", "style": "grid", "members": [{"person": "song-lin", "post": "蓝湾资本首席执行官"}]},
        {"title": "未上市公司", "note": "", "style": "grid", "members": [{"person": "he-shan", "post": "北岸智能公司首席执行官"}]}]})
    rows = [("jiang-yue", "青岚科技公司", "AAA", 1600000000000, "1,600.00"), ("song-lin", "蓝湾资本", "BBB", 900000000000, "900.00")]
    path = data / "rosters" / "market-caps.json"
    write_json(path, {"as_of": "2026-10-01", "companies": [
        {"person": p, "company": c, "ticker": t, "usd": usd, "date": "2026-10-01", "url": f"https://example.com/{t.lower()}",
         "publisher": "示例行情网", "excerpt": f"Oct 1, 2026 | {shown} Bn"} for p, c, t, usd, shown in rows]})
    return path


CAPS_NOW = {"AAA": (950000000000, "950.00"), "BBB": (1200000000000, "1,200.00")}
CAPS_PAGES = {f"https://example.com/{t.lower()}": f"<html><body><p>Oct 23, 2026 | {shown} Bn</p></body></html>"
              for t, (_usd, shown) in CAPS_NOW.items()}


def caps_answer(**skip) -> dict:
    """市值会话的回复：2026-10-23 收盘，青岚科技跌到 9500 亿、蓝湾资本涨到 1.2 万亿；skip 里的公司不报。"""
    return {"date": "2026-10-23", "notes": "", "companies": [
        {"ticker": t, "usd": usd, "url": f"https://example.com/{t.lower()}", "publisher": "示例行情网",
         "excerpt": f"Oct 23, 2026 | {shown} Bn"} for t, (usd, shown) in CAPS_NOW.items() if t not in skip]}


def tiers(data: Path) -> list[tuple[str, list[str]]]:
    """名单页上商界各层的人（zuzhibu/rosters.py 按市值分层、层内从大到小排）。"""
    return [(g["title"], [m["person"] for m in g["members"]]) for g in rosters.ranked(rosters.load(data)["business"])]


def resume_review(prompt: str) -> dict:
    """获取简历核查会话的回复：逐项核实了事实包里的每个路径。"""
    part = prompt.split("【必须逐项覆盖的事实路径】", 1)[1].split("检查要点", 1)[0]
    return {"decision": "approve", "held_reasons": [], "failed_claims": [], "source_issues": [], "verified_source_ids": ["s1"],
            "verified_claim_paths": re.findall(r'"path": "([^"]+)"', part), "notes": ""}


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data, self.runs, self.journal = root / "data", root / "runs", root / "journal"
        self.settings, self.progress, self.stop = root / "settings.json", root / "update" / "progress.json", root / "update" / "stop"
        store = Store(self.data).load()
        store.save_person(make_record("lin-mu", "林木", [_role("美国陆军部", "部长", "politics", "2025-03")], [], bureaus.MILITARY))
        mu = make_record("mu-lin", "穆林", [_role("美国商务部", "副部长", "politics", "2025-03")], [], bureaus.WHITEHOUSE)
        mu["name_en"] = "Mu Lin"
        store.save_person(mu)
        store.save_meta({"daily": {"last_run": "2026-10-21T08:51:42", "last_window": ["2026-10-18", "2026-10-21"]}})
        write_json(self.data / "rosters" / "defense.json",
                   {"id": "defense", "title": "国防", "groups": [{"title": "各军种", "note": "", "style": "",
                                                                 "members": [{"person": "lin-mu", "post": "陆军部部长"}]}]})
        write_json(self.settings, {"api": {"provider": "openai", "api_key": FAKE_KEY}})
        self.found = {"五局（美军干部局）": [_event(name_en="Qing Feng", name_zh="冯青", effective_at="2026-10-20", bureau_hint="military",
                                             new_roles=[dict(ARMY)], headline_zh="冯青就任美国陆军部部长")]}
        self.pages = {PHOTO_PAGE: f'<html><body><img src="{PHOTO}" alt="Qing Feng"></body></html>',
                      NOMINATIONS_URL: NOMINATIONS_PAGE, **CAPS_PAGES}
        self.on_verify = None
        self.on_caps = lambda text: openai_response(json.dumps(caps_answer(), ensure_ascii=False), searches=3)
        self.steps = []

    def tearDown(self):
        self.tmp.cleanup()

    def handler(self, call):
        text = (call["body"] or {}).get("input", "")
        if call["url"].endswith("/cancel"):
            return openai_response(status="cancelled", rid="resp_slow")
        if "收盘市值" in text:
            return self.on_caps(text)
        if "whitehouse.gov" in text:
            return openai_response("白宫新闻标题")
        if "资料研究员" in text:
            return openai_response(json.dumps(resume_bundle(text), ensure_ascii=False), searches=5, opened=(PHOTO_PAGE,))
        if "独立事实核查员" in text:
            return openai_response(json.dumps(resume_review(text), ensure_ascii=False), searches=3, opened=(PHOTO_PAGE,))
        if "独立核查员" in text:
            if self.on_verify:
                return self.on_verify(text)
            return openai_response(json.dumps({"reviews": [_review(0)]}, ensure_ascii=False), searches=4,
                                   opened=("https://example.com/a", "https://example.com/b"))
        events = next((evs for scope, evs in self.found.items() if scope in text), [])
        return openai_response(json.dumps({"events": events, "checked_candidates": [], "notes": ""}, ensure_ascii=False), searches=16)

    def update(self, today=TODAY):
        fake = FakeApi(self.handler)
        real_step = progress.Progress.step

        def spy(report, n, **detail):
            self.steps.append(progress.text({"state": "running", "step": n, "detail": detail}))
            return real_step(report, n, **detail)

        with mock.patch.object(ai_api, "urllib_transport", fake), mock.patch.object(progress.Progress, "step", spy), \
                mock.patch.object(collect, "collect", lambda store, since, until, cache=None: {"leads": [], "errors": []}), \
                mock.patch.object(research, "fetch_page", lambda url, timeout=20.0: self.pages.get(url)):
            code = run.main(["--backend", "api", "--since-last", "--settings", str(self.settings), "--journal", str(self.journal),
                             "--data", str(self.data), "--runs", str(self.runs), "--date", today, "--jobs", "1",
                             "--progress", str(self.progress), "--stop-file", str(self.stop)])
        return code, fake, progress.view(progress.read(self.progress))

    def roster(self):
        return read_json(self.data / "rosters" / "defense.json")["groups"][0]["members"]

    def test_six_steps_and_the_new_appointee_replaces_the_holder(self):
        code, fake, view = self.update()
        self.assertEqual(code, 0)
        self.assertEqual(self.steps, [
            "正在连接接口……",
            "正在查询公开信息……（白宫、国会、联邦公报、证券交易委员会等官方来源）",
            "正在深度思考……（逐项研判人事变动　白宫 1/5）", "正在深度思考……（逐项研判人事变动　商界 2/5）",
            "正在深度思考……（逐项研判人事变动　国会 3/5）", "正在深度思考……（逐项研判人事变动　国防 4/5）",
            "正在深度思考……（逐项研判人事变动　司法 5/5）",
            "正在逐条核实……（第 1 条，共 1 条）",
            "正在获取简历……（新任人员 1 人）",
            "正在更新简历……（1 人）"])
        self.assertEqual((view["state"], view["percent"], view["running"] if "running" in view else False), ("done", 100, False))
        self.assertEqual(view["message"], "更新完成。新增人事动态 1 条，更新简历 1 份。")
        store = Store(self.data).load()
        self.assertEqual(self.roster(), [{"person": "qing-feng", "post": "陆军部部长"}], "新任换上，原任移出名单")
        person = store.person("qing-feng")
        self.assertEqual(resume.card_type(person)[0], resume.FULL_CARD)
        self.assertEqual((person["tier"], person["portrait"]["url"], person["portrait"]["source"]), ("reverified", PHOTO, PHOTO_PAGE))
        (ev,) = [e for e in store.events() if e.get("origin") == localupdates.ORIGIN]
        self.assertEqual((ev["person_id"], ev["applied"]["status"]), ("qing-feng", personnel.APPLIED))
        self.assertIn(ev["id"], person["events"])
        self.assertIsNotNone(store.person("lin-mu"), "原任的人物记录留着，只是不在名单上")
        journal = localupdates.Journal(self.journal)
        (entry,) = journal.events()
        self.assertEqual([(c["old"], c["new"], c["post_after"]) for c in entry["roster"]], [("lin-mu", "qing-feng", "陆军部部长")])
        self.assertIn("qing-feng", journal.people(), "本机取到的标准简历另记一份")
        self.assertEqual(journal.pending(), [])
        self.assertEqual(len([c for c in fake.calls if "资料研究员" in json.dumps(c["body"] or {}, ensure_ascii=False)]), 1)
        for path in Path(self.tmp.name).rglob("*"):
            if path.is_file() and path != self.settings:
                self.assertNotIn(FAKE_KEY, path.read_text(encoding="utf-8", errors="replace"), path)

    def test_no_official_photo_keeps_the_whole_group_back_until_next_time(self):
        del self.pages[PHOTO_PAGE]
        code, _, view = self.update()
        self.assertEqual(code, 0)
        self.assertEqual(view["message"], "更新完成。暂无新的人事动态。")
        store = Store(self.data).load()
        self.assertEqual(self.roster(), [{"person": "lin-mu", "post": "陆军部部长"}], "名单不动")
        self.assertIsNone(store.person("qing-feng"), "不留没有照片的半成品")
        self.assertEqual([e for e in store.events() if e.get("origin") == localupdates.ORIGIN], [], "动态和名单一起，等下次")
        (pending,) = localupdates.Journal(self.journal).pending()
        self.assertEqual(pending["attempts"], 1)
        self.assertIn("照片所在网页取不到", "；".join(pending["reasons"]))

        self.pages[PHOTO_PAGE] = f'<html><img src="{PHOTO}"></html>'
        self.found = {}
        self.steps.clear()
        code, _, view = self.update("2026-10-25")
        self.assertEqual(code, 0)
        self.assertIn("正在获取简历……（新任人员 1 人）", self.steps, "上次留下的接着取")
        self.assertEqual(view["message"], "更新完成。新增人事动态 1 条，更新简历 1 份。")
        self.assertEqual(self.roster(), [{"person": "qing-feng", "post": "陆军部部长"}])
        self.assertEqual(localupdates.Journal(self.journal).pending(), [])

    def test_write_error_leaves_nothing_half_written(self):
        real = rosterchange.write_json

        def disk_full_at_roster(path, obj):
            if Path(path).name == "defense.json":
                raise OSError("磁盘满了")
            real(path, obj)

        before = {p: p.read_bytes() for p in self.data.rglob("*.json") if p.name != "meta.json"}
        with mock.patch.object(rosterchange, "write_json", disk_full_at_roster):
            code, _, view = self.update()
        self.assertEqual(code, 0)
        after = {p: p.read_bytes() for p in self.data.rglob("*.json") if p.name != "meta.json"}
        self.assertEqual(after, before, "人物记录、动态、名单一个都没写：已写的退回，新建的删掉")
        (pending,) = localupdates.Journal(self.journal).pending()
        self.assertIn("写入出错", pending["reasons"][0])
        self.assertEqual(localupdates.Journal(self.journal).events(), [])

        self.found = {}
        code, _, view = self.update("2026-10-25")
        self.assertEqual(view["message"], "更新完成。新增人事动态 1 条，更新简历 1 份。", "下次接着写")
        self.assertEqual(self.roster(), [{"person": "qing-feng", "post": "陆军部部长"}])

    def test_stop_keeps_what_was_verified(self):
        self.found = {"三局（白宫干部局）": [
            _event(kind="transfer", person_id="mu-lin", name_en="Mu Lin", name_zh="穆林", effective_at="2026-10-22",
                   ended_roles=[{**ARMY, "organization": "美国商务部", "role": "副部长", "start": None, "end": "2026-10-22"}],
                   new_roles=[{**ARMY, "organization": "美国内政部", "role": "副部长", "start": "2026-10-22"}],
                   headline_zh="穆林任美国内政部副部长", bureau_hint="whitehouse"),
            _event(name_en="Yuan Bai", effective_at="2026-10-23", headline_zh="白远任美国能源部副部长", bureau_hint="whitehouse")]}

        def verify_then_stop(text):  # 使用者在核实第一条时点了“停止更新”
            self.stop.write_text("stop", encoding="utf-8")
            return openai_response(json.dumps({"reviews": [_review(0)]}, ensure_ascii=False), searches=4,
                                   opened=("https://example.com/a", "https://example.com/b"))

        self.on_verify = verify_then_stop
        code, fake, view = self.update()
        self.assertEqual(code, run.EXIT_STOPPED)
        self.assertEqual(view["state"], "stopped")
        self.assertEqual(view["message"], "更新已停止。已核实的 1 条人事动态已保存。")
        store = Store(self.data).load()
        self.assertEqual([e["person"]["name_en"] for e in store.events()], ["Mu Lin"])
        self.assertEqual(len([c for c in fake.calls if "独立核查员" in json.dumps(c["body"] or {}, ensure_ascii=False)]), 1,
                         "停下以后不再开新会话")
        (run_dir,) = [p for p in self.runs.iterdir() if p.is_dir()]
        held = read_json(run_dir / "held.json")
        self.assertEqual([(h["event"]["name_en"], h["reasons"]) for h in held], [("Yuan Bai", ["更新停下，这条没有核实"])])
        self.assertNotIn("last_window", localupdates.Journal(self.journal).state(), "停下的这次不推进截止日")

    def test_stop_cancels_the_session_running_on_the_interface(self):
        answer = self.handler

        def slow(call):  # 白宫组的研究会话在接口上跑着，使用者点了“停止更新”
            text = (call["body"] or {}).get("input", "")
            if call["method"] == "POST" and call["url"].endswith("/responses") and "三局（白宫干部局）" in text:
                self.stop.write_text("stop", encoding="utf-8")
                return {"id": "resp_slow", "object": "response", "status": "in_progress", "output": []}
            return answer(call)

        self.handler = slow
        code, fake, view = self.update()
        self.assertEqual(code, run.EXIT_STOPPED)
        self.assertEqual(view["message"], "更新已停止。已核实的 0 条人事动态已保存。")
        self.assertTrue(any(c["url"].endswith("/responses/resp_slow/cancel") for c in fake.calls), "接口上的会话已取消")
        research_calls = [c for c in fake.calls if c["url"].endswith("/responses") and "【范围】" in (c["body"] or {}).get("input", "")]
        self.assertEqual(len(research_calls), 1, "其余四组不再开")

    def test_quota_runs_out(self):
        self.found = {"三局（白宫干部局）": [_nomination(), _event(name_en="Yuan Bai", effective_at="2026-10-23",
                                                              headline_zh="白远任美国能源部副部长", bureau_hint="whitehouse")]}
        self.on_verify = lambda text: (429, {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}})
        code, _, view = self.update()
        self.assertEqual(code, 2)
        self.assertEqual((view["state"], view["error"]), ("failed", "接口额度不足"))
        self.assertEqual(view["message"], "更新未完成：接口额度不足。已核实的结果已保存。")
        store = Store(self.data).load()
        self.assertEqual([e["person"]["name_en"] for e in store.events()], ["Yuan Bai"], "程序核查已放行的照样写回")
        self.assertEqual(store.events()[0]["kind"], "nominate")
        self.assertEqual(view["result"]["events"], 0, "提名只留后台，栏目里没有新稿件")

    def test_count_is_new_articles_in_the_column(self):
        """“新增人事动态 n 条”按栏目里新出现的稿件数：参议院同一天确认的两人合成一篇，算 1 条。"""
        def confirmed(name_en, name_zh, org, title):
            return _event(kind="confirm", name_en=name_en, name_zh=name_zh, announced_at="2026-10-21", effective_at=None,
                          bureau_hint="whitehouse", new_roles=[{**ARMY, "organization": org, "role": title, "start": None}],
                          headline_zh=f"参议院确认{name_zh}")

        self.found = {"三局（白宫干部局）": [confirmed("Yuan Bai", "白远", "美国能源部", "副部长"),
                                         confirmed("Shan He", "何山", "美国商务部", "副部长")]}
        code, _, view = self.update()
        self.assertEqual(code, 0)
        store = Store(self.data).load()
        self.assertEqual(sorted(e["person"]["name_en"] for e in store.events()), ["Shan He", "Yuan Bai"], "两条动态都写进")
        self.assertEqual([a["id"] for a in run.renmian.articles(store, TODAY)], ["2026-10-21-senate"])
        self.assertEqual(view["message"], "更新完成。新增人事动态 1 条，更新简历 0 份。")
        self.assertEqual(self.update("2026-10-25")[2]["message"], "更新完成。暂无新的人事动态。", "再更新一次，栏目里没有新稿件")


    # ─────────────────────────────── 商界市值

    def run_log(self) -> str:
        return "\n".join(p.read_text(encoding="utf-8") for p in sorted(self.runs.glob("*/run.log")))

    def caps_calls(self, fake) -> list[dict]:
        return [c for c in fake.calls if "收盘市值" in ((c["body"] or {}).get("input") or "")]

    def test_caps_refresh_after_write_back_and_the_roster_reorders(self):
        self.found = {}
        caps = write_caps(self.data)
        self.assertEqual(tiers(self.data), [("市值万亿美元以上", ["jiang-yue"]), ("市值万亿美元以下", ["song-lin"]),
                                            ("未上市公司", ["he-shan"])])
        code, fake, view = self.update()
        self.assertEqual((code, view["state"]), (0, "done"))
        self.assertEqual(view["message"], "更新完成。暂无新的人事动态。", "完成提示不加字")
        self.assertEqual(self.steps[-1], "正在更新简历……（0 人）", "市值在第 6 步里刷新，不另报一步")
        self.assertFalse(any("市值" in s for s in self.steps))
        data = read_json(caps)
        self.assertEqual(data["as_of"], "2026-10-23")
        self.assertEqual({c["ticker"]: (c["usd"], c["date"]) for c in data["companies"]},
                         {"AAA": (950000000000, "2026-10-23"), "BBB": (1200000000000, "2026-10-23")})
        self.assertEqual(tiers(self.data), [("市值万亿美元以上", ["song-lin"]), ("市值万亿美元以下", ["jiang-yue"]),
                                            ("未上市公司", ["he-shan"])], "名单页按新市值重排")
        (call,) = self.caps_calls(fake)
        self.assertEqual(call["headers"]["Authorization"], "Bearer " + FAKE_KEY, "用使用者自己的接口和额度")
        self.assertIn("商界市值：已刷新到 2026-10-23 收盘", self.run_log())

    def test_caps_that_do_not_check_out_leave_the_file_alone_and_the_update_still_completes(self):
        self.found = {}
        caps = write_caps(self.data)
        before = caps.read_bytes()
        self.on_caps = lambda text: openai_response(json.dumps(caps_answer(BBB=True), ensure_ascii=False), searches=3)
        code, _, view = self.update()
        self.assertEqual((code, view["state"], view["message"]), (0, "done", "更新完成。暂无新的人事动态。"), "更新照样算完成")
        self.assertEqual(caps.read_bytes(), before, "查不齐就整份不写，留着原来的市值")
        self.assertIn("缺 BBB", self.run_log())
        self.on_caps = lambda text: (429, {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}})
        code, _, view = self.update("2026-10-25")
        self.assertEqual((code, view["state"]), (0, "done"), "刷新市值时额度不足，这次更新也照样算完成")
        self.assertEqual(caps.read_bytes(), before)
        self.assertIn("刷新市值停下：接口额度不足", self.run_log())
        self.assertEqual(tiers(self.data)[0], ("市值万亿美元以上", ["jiang-yue"]), "名单页照旧")

    def test_stop_while_refreshing_caps_leaves_no_half_written_file(self):
        self.found = {}
        caps = write_caps(self.data)
        before = caps.read_bytes()

        def stop_mid_session(text):  # 市值会话在接口上跑着，使用者点了“停止更新”
            self.stop.write_text("stop", encoding="utf-8")
            return {"id": "resp_caps", "object": "response", "status": "in_progress", "output": []}

        self.on_caps = stop_mid_session
        code, fake, view = self.update()
        self.assertEqual((code, view["state"]), (run.EXIT_STOPPED, "stopped"))
        self.assertTrue(any(c["url"].endswith("/responses/resp_caps/cancel") for c in fake.calls), "接口上的会话已取消")
        self.assertEqual(caps.read_bytes(), before)
        self.assertEqual([p.name for p in caps.parent.iterdir() if p.name.startswith(".tmp")], [], "不留写了一半的文件")

        self.stop.unlink()

        def stop_after_answer(text):  # 会话答完了，核对来源页时停下：也不写
            self.stop.write_text("stop", encoding="utf-8")
            return openai_response(json.dumps(caps_answer(), ensure_ascii=False), searches=3)

        self.on_caps = stop_after_answer
        code, _, view = self.update("2026-10-25")
        self.assertEqual((code, view["state"]), (run.EXIT_STOPPED, "stopped"))
        self.assertEqual(caps.read_bytes(), before)
        self.assertEqual([p.name for p in caps.parent.iterdir() if p.name.startswith(".tmp")], [])

    def test_caps_are_not_started_after_a_stop(self):
        caps = write_caps(self.data)
        before = caps.read_bytes()

        def verify_then_stop(text):
            self.stop.write_text("stop", encoding="utf-8")
            return openai_response(json.dumps({"reviews": [_review(0)]}, ensure_ascii=False), searches=4,
                                   opened=("https://example.com/a", "https://example.com/b"))

        self.on_verify = verify_then_stop
        code, fake, view = self.update()
        self.assertEqual((code, view["state"]), (run.EXIT_STOPPED, "stopped"))
        self.assertEqual(self.caps_calls(fake), [], "停下以后不开市值会话")
        self.assertEqual(caps.read_bytes(), before)


class MonthlyCapsTest(unittest.TestCase):
    """维护者每月更新（本机 Codex，--daily）写回之后也刷新市值，用的是 Codex 的会话设置。会话换成假的，不调 Codex。"""

    def test_monthly_update_refreshes_caps_with_codex(self):
        with tempfile.TemporaryDirectory() as tmp:
            data, runs = Path(tmp) / "data", Path(tmp) / "runs"
            Store(data).load().save_meta({"daily": {"last_window": ["2026-09-28", "2026-10-01"]}})
            caps = write_caps(data)
            calls = []

            def fake_call(prompt, schema, config, runs, label):
                calls.append((label, type(config).__name__))
                stats = {"searches": 20, "opened": [], "input_tokens": 0, "output_tokens": 0, "errors": []}
                if label == "marketcap":
                    return caps_answer(), stats, None
                return {"events": [], "checked_candidates": [], "notes": ""}, stats, None

            with mock.patch.object(research, "call", fake_call), \
                    mock.patch.object(research, "fetch_page", lambda url, timeout=20.0: CAPS_PAGES.get(url)), \
                    mock.patch.object(collect, "collect", lambda *a, **k: {"leads": [], "errors": []}):
                code = run.main(["--daily", "--days", "3", "--data", str(data), "--runs", str(runs), "--codex", sys.executable,
                                 "--date", TODAY, "--jobs", "1"])
            self.assertEqual(code, 0)
            self.assertIn(("marketcap", "CodexConfig"), calls, "每月更新用本机 Codex 刷新市值")
            self.assertEqual(read_json(caps)["as_of"], "2026-10-23")
            self.assertEqual(tiers(data)[0], ("市值万亿美元以上", ["song-lin"]))


class ResumeFetchTest(unittest.TestCase):
    """获取简历：两次会话，照写法纪律放行；官网照片由程序取网页核对。"""

    def fetch(self, pages, review=resume_review):
        calls = []

        def fake_call(prompt, schema, config, runs, label):
            calls.append(label)
            stats = {"searches": 3, "opened": [PHOTO_PAGE], "input_tokens": 0, "output_tokens": 0, "errors": []}
            data = resume_bundle(prompt) if label.endswith("-research") else review(prompt)
            if label.endswith("-research"):
                self.assertIn("portrait", schema["properties"], "研究输出另报官网照片")
            return data, stats, None

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(research, "call", fake_call):
            out = run.resumes.fetch({"id": "qing-feng", "name_zh": "冯青", "name_en": "Qing Feng", "bureau": None}, post="陆军部部长",
                                    role=dict(ARMY), today=TODAY, config=None, runs=Path(tmp), label="resume-qing-feng",
                                    fetch_page=pages.get)
        return out, calls

    def test_full_card_with_official_photo(self):
        out, calls = self.fetch({PHOTO_PAGE: f'<img src="{PHOTO}">'})
        self.assertEqual((out["status"], out["reasons"]), ("ok", []))
        self.assertEqual(calls, ["resume-qing-feng-research", "resume-qing-feng-verify"])
        self.assertEqual(out["portrait"]["url"], PHOTO)
        rec = {"id": "qing-feng", "name_zh": "冯青", "name_en": "Qing Feng", "bureau": None, "resume": {}}
        self.assertEqual(run.resumes.install(rec, out["item"], TODAY, out["portrait"]), [])
        self.assertEqual(run.resumes.ready(rec), [], "完整简历卡、写法检查、照片都过")

    def test_photo_must_be_on_the_official_page(self):
        out, _ = self.fetch({PHOTO_PAGE: "<html>没有照片</html>"})
        self.assertEqual(out["status"], "held")
        self.assertIn("照片所在网页上没有这张照片", out["reasons"][0])

    def test_unverified_facts_hold_the_resume(self):
        out, _ = self.fetch({PHOTO_PAGE: f'<img src="{PHOTO}">'},
                            review=lambda prompt: {**resume_review(prompt), "decision": "hold", "held_reasons": ["出生日期查不到"]})
        self.assertEqual(out["status"], "held")
        self.assertIn("核查结论为搁置", out["reasons"])

    def test_photo_check(self):
        check = run.resumes.check_portrait
        page = {PHOTO_PAGE: f'<img src="{PHOTO}">'}
        self.assertEqual(check({"image_url": None, "page_url": None}, page.get)[1], "没有找到官网照片")
        self.assertIn("不是官网", check({"image_url": PHOTO, "page_url": "https://news.example.com/feng"}, page.get)[1])
        self.assertIn("https", check({"image_url": "http://www.lakeview.gov/images/feng.jpg", "page_url": PHOTO_PAGE}, page.get)[1])
        self.assertEqual(check({"image_url": PHOTO, "page_url": PHOTO_PAGE}, {PHOTO_PAGE: '<img src="/images/feng.jpg">'}.get)[1], "",
                         "网页里写的是相对地址也认")


if __name__ == "__main__":
    unittest.main()
