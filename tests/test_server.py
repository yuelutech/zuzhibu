"""本地接口：七局目录、名册、人物、动态、检索、导出（金标能导出，名册记录不能）；
只认本机地址、POST 只收同源 JSON；安装版读本机数据目录的当前快照，联网更新切换后自动重读。用临时数据目录，人物虚构。"""

import http.client
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from io import BytesIO
from pathlib import Path
from unittest import mock

from bureaus.dispatch import run
from shared.backends import ai_api
from zuzhibu import bureaus, datasync, desktop, personnel, progress, server
from zuzhibu.store import Store

from tests.fakes.fake_ai_api import FAKE_KEY, FakeApi, anthropic_message, search_blocks, text_block
from tests.test_personnel import TODAY, _career, _fact, _role, make_event, make_record


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        store = Store(Path(cls.tmp.name)).load()
        senator = make_record("lin-mu", "林木", [_role("美国联邦参议员（北岸州）", start="2003-01")],
                              [_career("2003", "美国国会参议院", "联邦参议员（北岸州）", ongoing=True)], bureaus.CONGRESS)
        senator["resume"]["person"]["birth_date"] = _fact("1960-05")
        senator["resume"]["person"]["work_start"] = _fact("1985-06")
        store.save_person(senator)
        store.save_person(make_record("he-shan", "何山", [_role("青岚科技公司", "首席执行官", "enterprise", "2018-07")], [], bureaus.AI, tier="seed"))
        ev = make_event(store, "lin-mu", "leave", ended=[{"organization": "美国联邦参议员（北岸州）", "role": ""}],
                        others=[], appointment=None, date="2026-09-11")
        ev["headline"] = "林木卸任美国联邦参议员"
        personnel.apply_event(store, ev, TODAY)
        store.save_meta({"daily": {"default_view": {"from": "2026-08-01", "to": "2026-10-31"}}, "bureaus": {}})
        cls.srv = server.make_server(0, cls.tmp.name)
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as resp:
            return resp.read(), resp.headers

    def json(self, path):
        return json.loads(self.get(path)[0])

    def test_health_and_bureaus(self):
        self.assertTrue(self.json("/api/health")["ok"])
        cat = self.json("/api/bureaus")
        self.assertEqual([b["number"] for b in cat["bureaus"]], ["一局", "二局", "三局", "四局", "五局", "六局", "七局"])
        counts = {b["id"]: b["count"] for b in cat["bureaus"]}
        self.assertEqual(counts["retired"], 1)
        self.assertEqual(counts["dispatch"], 1)

    def test_roster_person_events_search(self):
        retired = self.json("/api/people?bureau=retired")["items"]
        self.assertEqual([r["id"] for r in retired], ["lin-mu"])
        view = self.json("/api/people/lin-mu")
        self.assertEqual(view["record"]["bureau"], bureaus.RETIRED)
        self.assertTrue(view["composed"]["exportable"])
        self.assertIn("2003年－2026年9月", view["composed"]["text"])
        self.assertEqual(len(view["events"]), 1)
        events = self.json("/api/events?from=2026-08-01&to=2026-10-31")["items"]
        self.assertEqual(events[0]["applied"]["status_label"], "已写回")
        found = self.json("/api/search?q=%E6%9E%97%E6%9C%A8")
        self.assertEqual(found["people"][0]["id"], "lin-mu")

    def test_exports(self):
        data, headers = self.get("/api/people/lin-mu/export?format=docx")
        with zipfile.ZipFile(BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        self.assertIn("林木简历", xml)
        self.assertNotIn("组织部文件", xml, "简历导出用普通文件，不套红头")
        self.assertIn("filename*=UTF-8''", headers["Content-Disposition"])
        for fmt in ("article", "card", "md", "txt"):
            body, _ = self.get(f"/api/people/lin-mu/export?format={fmt}")
            self.assertIn("林木", body.decode("utf-8"))
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/api/people/he-shan/export?format=docx")
        self.assertEqual(cm.exception.code, 403)

    def test_static_and_errors(self):
        html, _ = self.get("/")
        self.assertIn(b"AI\xe6\x94\xbf\xe8\xa7\x81", html)
        for bad in ("/api/people/nobody", "/api/nothing", "/../README.md"):
            with self.assertRaises(urllib.error.HTTPError):
                self.get(bad)

    def raw(self, method, path, body=b"", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def test_only_local_host_and_same_origin_json(self):
        host = "127.0.0.1:%d" % self.srv.server_address[1]
        self.assertEqual(self.raw("GET", "/api/health", headers={"Host": "evil.example"})[0], 403, "域名重绑定挡掉")
        body = json.dumps({"api": {"model": "x"}}).encode()
        settings = Path(self.tmp.name) / "settings.json"
        with mock.patch.object(server, "settings_path", return_value=settings):
            self.assertEqual(self.raw("POST", "/api/settings", body, {"Content-Type": "text/plain"})[0], 403, "简单请求挡掉")
            self.assertEqual(self.raw("POST", "/api/settings", body, {"Content-Type": "application/json",
                                                                    "Origin": "https://evil.example"})[0], 403, "跨站挡掉")
            self.assertFalse(settings.exists())
            self.assertEqual(self.raw("POST", "/api/settings", body, {"Content-Type": "application/json",
                                                                    "Origin": "http://" + host})[0], 200)
            self.assertTrue(settings.exists())

    def test_update_uses_only_users_own_quota(self):
        """立即更新只用使用者自己的额度；窗口从数据截止日到今天（维护者每月更新一次，中间这段由用户自己补）。"""
        import datetime as dt

        def window(meta, today):  # 窗口由一局流水线的 --since-last 算（与 daily.ps1 同算法）
            return run.window_days(run.last_cutoff(meta, None), today.isoformat())

        self.assertEqual(window({"daily": {"last_window": ["2026-10-01", "2026-10-20"]}}, dt.date(2026, 10, 21)), 2)
        self.assertEqual(window({"daily": {"last_window": ["2026-09-01", "2026-10-01"]}}, dt.date(2026, 10, 25)), 25)
        self.assertEqual(window({}, dt.date(2026, 10, 25)), 31, "取不到截止日就 31 天")
        self.assertEqual(window({"daily": {"last_window": ["x", "2026-01-01"]}}, dt.date(2026, 10, 25)), 62, "最长 62 天")
        settings = Path(self.tmp.name) / "settings.json"
        settings.unlink(missing_ok=True)
        body = json.dumps({}).encode()
        with mock.patch.object(server, "settings_path", return_value=settings), mock.patch.object(server, "codex_available", return_value=False):
            status, raw = self.raw("POST", "/api/update", body, {"Content-Type": "application/json"})
            self.assertEqual(status, 200)
            out = json.loads(raw)
            self.assertEqual((out["ok"], out["message"]), (False, "请先在‘设置’中填写大模型接口"), "没填接口不开流水线")

    def test_datasync_in_repo_mode(self):
        view = self.json("/api/datasync")
        self.assertEqual(view["mode"], "dev")
        self.assertFalse(view["enabled"])
        self.assertEqual(view["url"], datasync.DEFAULT_SOURCES[0])
        status, body = self.raw("POST", "/api/datasync", json.dumps({"action": "check"}).encode(), {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertIn("git", json.loads(body)["error"])
        self.assertTrue(self.json("/api/ping?client=abc")["ok"])


def closed_port() -> int:
    """一个刚关掉、没人监听的本机端口。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class SecondEditionTest(unittest.TestCase):
    """第二版界面的接口：栏目、名单、搜索索引、人物页（名单外 404）、人事动态（只转 zuzhibu/renmian.py 的稿件）、关于、许可全文、字体目录。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        store = Store(root).load()
        store.save_person(make_record("lin-mu", "林木", [_role("美国联邦参议员（北岸州）")], [_career("2003", "美国国会参议院", "参议员", ongoing=True)], bureaus.CONGRESS))
        store.save_person(make_record("he-shan", "何山", [_role("青岚科技公司", "首席执行官", "enterprise")], [], bureaus.AI))
        (root / "rosters").mkdir()
        (root / "rosters" / "congress.json").write_text(json.dumps({"id": "congress", "title": "国会", "groups": [
            {"title": "参议员", "style": "grid", "members": [{"person": "lin-mu", "post": "北岸州", "party": "共和党", "state": "北岸州"}]}]},
            ensure_ascii=False), encoding="utf-8")
        (root / "rosters" / "whitehouse.json").write_text(json.dumps({"id": "whitehouse", "title": "白宫", "groups": []}), encoding="utf-8")
        cls.fonts = root / "fonts"
        cls.fonts.mkdir()
        (cls.fonts / "Test-Regular.ttf").write_bytes(b"\x00\x01\x00\x00fake")
        (cls.fonts / "notes.txt").write_text("不是字体", encoding="utf-8")
        cls.srv = server.make_server(0, str(root))
        cls.base = "http://127.0.0.1:%d" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as resp:
            return resp.read(), resp.headers

    def json(self, path):
        return json.loads(self.get(path)[0])

    def status(self, path):
        try:
            return self.get(path) and 200
        except urllib.error.HTTPError as exc:
            return exc.code

    def test_sections_come_from_the_roster_files(self):
        view = self.json("/api/app")
        self.assertEqual([s["id"] for s in view["sections"]], ["whitehouse", "business", "congress", "defense", "justice", "news"])
        self.assertEqual([s["title"] for s in view["sections"]], ["白宫", "商界", "国会", "国防", "司法", "人事动态"])
        self.assertEqual(view["home"], "whitehouse")
        self.assertEqual(view["version"], server.VERSION)

    def test_rosters_index_and_profile(self):
        congress = self.json("/api/rosters/congress")
        member = congress["groups"][0]["members"][0]
        self.assertEqual((member["name"], member["party"]), ("林木", "共和党"))
        self.assertEqual(self.json("/api/rosters/whitehouse")["groups"], [])
        self.assertEqual(self.status("/api/rosters/nowhere"), 404)
        self.assertEqual([p["id"] for p in self.json("/api/index")["items"]], ["lin-mu"], "搜索只搜名单里的人")
        self.assertEqual(self.json("/api/profile/lin-mu")["title"], "林木简历")
        self.assertEqual(self.status("/api/profile/he-shan"), 404, "名单外的人没有人物页")

    def test_news_only_relays_renmian(self):
        self.srv.app._views.clear()
        with mock.patch.dict("sys.modules", {"zuzhibu.renmian": None}):
            self.assertEqual(self.json("/api/news")["items"], [], "还没有 renmian 模块时没有稿件")
        single = {"id": "2026-09-01-a", "title": "林木任某职", "date": "2026-09-01", "date_cn": "2026年9月1日", "paragraphs": ["林木9月1日任某职。"],
                  "source_line": "某机构", "sources": [{"result": "r1", "fact": "林木任某职", "sources": [{"publisher": "某机构", "url": "https://example.com/a"}]}],
                  "people": [{"person_id": "lin-mu", "name": "林木", "in_roster": True}], "results": [], "events": ["e1"]}
        merged = {"id": "2026-09-30-senate", "title": "美国参议院确认一批人事任命", "date": "2026-09", "date_cn": "2026年9月",
                  "paragraphs": ["第一段。", "第二段。"], "source_line": "美国参议院、某报", "sources": [], "people": [], "results": [], "events": []}
        fake = mock.Mock()
        fake.articles.return_value = [merged, single]
        fake.article.side_effect = lambda store, aid: {a["id"]: a for a in (merged, single)}.get(aid)
        self.srv.app._views.clear()
        with mock.patch.dict("sys.modules", {"zuzhibu.renmian": fake}):
            items = self.json("/api/news")["items"]
            self.assertEqual([a["id"] for a in items], ["2026-09-30-senate", "2026-09-01-a"], "照模块给的顺序")
            self.assertEqual(set(items[0]), {"id", "title", "date", "date_cn"}, "列表只有标题和日期")
            view = self.json("/api/news/2026-09-01-a")
            self.assertEqual(view, {k: single[k] for k in ("id", "title", "date", "date_cn", "paragraphs", "source_line")},
                             "单篇只给标题、日期、正文、来源一栏；人物名单、按事实的来源不给界面")
            self.assertEqual(self.json("/api/news/2026-09-30-senate")["date_cn"], "2026年9月", "只到月的照写")
            self.assertEqual(fake.article.call_args[0][1], "2026-09-30-senate", "单篇用 article(store, id)")
            self.assertEqual(self.status("/api/news/nothing"), 404)
        self.srv.app._views.clear()

    def test_about_and_license(self):
        about = self.json("/api/about")
        self.assertEqual(about["version"], server.VERSION)
        self.assertEqual(about["credits"], [])
        self.assertIn("Apache License", self.json("/api/license")["code"])

    def test_fonts_come_from_the_fonts_dir(self):
        with mock.patch.dict(os.environ, {"ZUZHIBU_FONTS": str(self.fonts)}):
            body, headers = self.get("/fonts/Test-Regular.ttf")
            self.assertEqual(body, b"\x00\x01\x00\x00fake")
            self.assertEqual(headers["Content-Type"], "font/ttf")
            self.assertIn("max-age", headers["Cache-Control"])
            for bad in ("/fonts/notes.txt", "/fonts/Missing.ttf", "/fonts/..%2Frosters%2Fcongress.json"):
                self.assertEqual(self.status(bad), 404, bad)
        self.assertEqual(server.fonts_dir(), server.local.get_path("fonts") or server.REPO / "fonts")


class InstalledServerTest(unittest.TestCase):
    """安装版：本机数据目录、当前快照、联网更新后自动重读、数据地址设置、关窗退出用的 ping 和 bye。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.bundled = base / "bundled"
        self.publish(self.bundled, [make_record("lin-mu", "林木", [_role("美国联邦参议员（北岸州）", start="2003-01")], [], bureaus.CONGRESS)],
                     "2026-10-20")
        self.home = datasync.DataHome(base / "home")
        datasync.ensure_seed(self.home, self.bundled)
        patcher = mock.patch.object(server, "settings_path", return_value=base / "settings.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.lifetime = desktop.Lifetime(grace=0.2)
        self.srv = server.make_server(0, None, home=self.home, lifetime=self.lifetime,
                                      dist={"version": server.VERSION, "sources": ["https://example.com/zzb/data/"]})
        self.base = "http://127.0.0.1:%d" % self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    @staticmethod
    def publish(root, people, date):
        store = Store(root).load()
        for rec in people:
            store.save_person(rec)
        store.save_meta({"updated_at": date, "baseline": "2026年10月版", "bureaus": {}})
        manifest = datasync.build_manifest(root)
        manifest["generated_at"] = date + "T00:05:00Z"
        (root / "manifest.json").write_text(datasync.dumps_manifest(manifest), encoding="utf-8")
        return manifest

    def json(self, path, body=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())

    def test_reads_snapshot_and_follows_switch(self):
        health = self.json("/api/health")
        self.assertTrue(health["installed"] and health["datasync"])
        self.assertEqual(Path(health["data_dir"]), self.home.current())
        view = self.json("/api/datasync")
        self.assertEqual(view["mode"], "installed")
        self.assertEqual(view["current"]["origin"], "bundled")
        self.assertEqual(view["url"], "https://example.com/zzb/data/", "默认地址取安装包里的")
        newer = Path(self.tmp.name) / "newer"
        shutil.copytree(self.bundled, newer)
        manifest = self.publish(newer, [make_record("he-shan", "何山", [_role("青岚科技公司", "首席执行官", "enterprise", "2018-07")], [],
                                                    bureaus.AI, tier="seed")], "2026-10-21")
        datasync.apply(self.home, manifest, datasync.LocalSource(newer), origin="online")
        self.assertEqual(self.json("/api/health")["people"], 2, "联网更新换了快照，服务自动改读新的")
        self.assertEqual(self.json("/api/datasync")["data_date"], "2026-10-21")
        self.assertIn("2026-10-21", self.json("/api/ping")["data"])

    def test_data_settings(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.json("/api/datasync", {"url": "http://example.com/data/"})
        self.assertEqual(cm.exception.code, 400, "外网地址要 https")
        self.assertTrue(self.json("/api/datasync", {"url": "https://mirror.example.com/zzb/data", "auto": False})["ok"])
        view = self.json("/api/datasync")
        self.assertEqual(view["url"], "https://mirror.example.com/zzb/data/")
        self.assertTrue(view["custom"])
        self.assertFalse(view["auto"])
        self.json("/api/datasync", {"url": ""})
        self.assertFalse(self.json("/api/datasync")["custom"], "空地址恢复默认")

    def test_manual_check_unreachable_keeps_local(self):
        self.json("/api/datasync", {"url": "http://127.0.0.1:%d/data/" % closed_port()})
        self.assertTrue(self.json("/api/datasync", {"action": "check"})["started"])
        deadline = time.monotonic() + 40  # Windows 上连一个没人监听的端口要重试几次才报错，机器忙时更慢
        while True:
            view = self.json("/api/datasync")
            if not view["running"] and view["state"].get("last_status") or time.monotonic() > deadline:
                break
            time.sleep(0.05)
        self.assertEqual(view["state"]["last_status"], "unreachable")
        self.assertIn("继续用本机数据", view["state"]["last_message"])
        self.assertEqual(self.json("/api/health")["people"], 1, "本机数据照常可看")

    def test_ping_and_bye(self):
        self.json("/api/ping?client=w1")
        self.json("/api/ping?client=w2")
        self.json("/api/bye", {"client": "w1"})
        time.sleep(0.3)
        self.assertIsNone(self.lifetime.reason(), "还有一个窗口开着")
        self.json("/api/bye", {"client": "w2"})
        time.sleep(0.3)
        self.assertEqual(self.lifetime.reason(), "窗口已关闭")


class ApiSettingsTest(unittest.TestCase):
    """“我的”页的 AI 接口：密钥打码、没有联网检索的不能开更新、换地址清旧密钥、检测联网检索、只收本软件页面的请求。
    设置文件放临时目录，不碰本机真实设置。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict(os.environ, {"APPDATA": cls.tmp.name, "ZUZHIBU_HOME": str(Path(cls.tmp.name) / "home")})
        cls.env.start()
        cls.nocodex = mock.patch.object(server, "codex_available", lambda: False)
        cls.nocodex.start()
        data = Path(cls.tmp.name) / "data"
        Store(data).load().save_meta({"daily": {}})
        cls.srv = server.make_server(0, str(data))
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.nocodex.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        server.settings_path().unlink(missing_ok=True)

    def get(self, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as resp:
            return json.loads(resp.read())

    def post(self, path, body, headers=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_no_settings_means_no_update(self):
        view = self.get("/api/settings")
        self.assertEqual([(p["id"], p["label"]) for p in view["providers"]], [("openai", "OpenAI 兼容接口"), ("anthropic", "Anthropic 接口")],
                         "设置里只列能联网检索的两类接口")
        self.assertFalse(view["update"]["ok"])
        self.assertEqual(view["update"]["reason"], "请先在‘设置’中填写大模型接口")
        self.assertFalse(self.get("/api/update")["plan"]["ok"])
        self.assertEqual(self.post("/api/update", {})[1]["ok"], False, "不能更新时不开流水线")

    def test_key_is_masked_and_web_search_is_required(self):
        self.assertEqual(self.post("/api/settings", {"api": {"provider": "openai", "api_key": FAKE_KEY}})[0], 200)
        view = self.get("/api/settings")
        self.assertNotIn(FAKE_KEY, json.dumps(view))
        self.assertTrue(view["web"]["ok"])
        self.assertEqual(view["update"]["backend"], "api")
        self.post("/api/settings", {"api": {"provider": "other", "api_key": FAKE_KEY}})
        plan = self.get("/api/update")["plan"]
        self.assertFalse(plan["ok"])
        self.assertIn("没有联网检索工具", plan["reason"])

    def test_changing_the_endpoint_clears_the_old_key(self):
        self.post("/api/settings", {"api": {"provider": "openai", "api_key": FAKE_KEY}})
        masked = self.get("/api/settings")["api"]["api_key"]
        status, out = self.post("/api/settings", {"api": {"provider": "openai", "base_url": "https://relay.example.com/v1", "api_key": masked}})
        self.assertEqual((status, out["cleared_key"]), (200, True), "密钥只发往填它时的那个地址")
        self.assertEqual(server.load_settings()["api"]["api_key"], "")
        status, out = self.post("/api/settings", {"api": {"provider": "openai", "base_url": "https://relay.example.com/v1", "api_key": FAKE_KEY}})
        self.assertFalse(out["cleared_key"])
        self.assertEqual(server.load_settings()["api"]["api_key"], FAKE_KEY)
        self.assertEqual(self.post("/api/settings", {"api": {"base_url": "http://relay.example.com/v1"}})[0], 400, "不收明文 http 地址")

    def test_only_this_apps_pages_may_post(self):
        for headers in ({"Origin": "https://evil.example"}, {"Content-Type": "text/plain"}, {"Host": f"evil.example:{self.port}"}):
            status, _ = self.post("/api/settings", {"api": {"base_url": "https://evil.example/v1"}}, headers)
            self.assertEqual(status, 403, headers)
        self.assertFalse(server.settings_path().exists(), "被挡的请求什么也没改")

    def test_check_web_search(self):
        self.post("/api/settings", {"api": {"provider": "anthropic", "api_key": FAKE_KEY}})
        fake = FakeApi(lambda call: anthropic_message(search_blocks(1) + [text_block("白宫新闻标题")]))
        with mock.patch.object(ai_api, "urllib_transport", fake):
            status, out = self.post("/api/settings/check", {})
        self.assertEqual(status, 200)
        self.assertTrue(out["ok"], out)
        self.assertEqual(fake.calls[0]["url"], "https://api.anthropic.com/v1/messages")
        check = self.get("/api/settings")["check"]
        self.assertTrue(check["ok"])
        self.assertNotIn("fingerprint", check)
        self.post("/api/settings", {"api": {"model": "claude-sonnet-5-5"}})
        self.assertIsNone(self.get("/api/settings")["check"], "改了模型，检测结果作废")
        self.assertNotIn(FAKE_KEY, json.dumps(server.load_settings()["api"].get("check") or {}))

    def test_update_command_keeps_the_key_off_the_command_line(self):
        home = Path(self.tmp.name) / "home"
        cmd = server.update_command({"backend": "api"}, Path(self.tmp.name) / "data", home)
        self.assertEqual(cmd[1:3], ["-m", "bureaus.dispatch.run"])
        for flag in ("--since-last", "--backend", "--settings", "--journal", "--runs"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index("--journal") + 1], str(home / "local"))
        self.assertNotIn(FAKE_KEY, " ".join(cmd))
        self.assertIn("--origin", server.update_command({"backend": "codex"}, Path(self.tmp.name) / "data", home))


class UpdateProgressTest(unittest.TestCase):
    """立即更新的开始、进度、停止：进度从流水线写的进度文件读，提示语照审定的文案；停止后已核实的照样保存。
    流水线换成假的（只报进度），设置和进度文件放临时目录。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.env = mock.patch.dict(os.environ, {"APPDATA": cls.tmp.name, "ZUZHIBU_HOME": str(Path(cls.tmp.name) / "home")})
        cls.env.start()
        cls.nocodex = mock.patch.object(server, "codex_available", lambda: False)
        cls.nocodex.start()
        data = Path(cls.tmp.name) / "data"
        Store(data).load().save_meta({"daily": {}})
        cls.srv = server.make_server(0, str(data))
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.nocodex.stop()
        cls.env.stop()
        cls.tmp.cleanup()

    def setUp(self):
        server.save_settings({"api": {"provider": "openai", "api_key": FAKE_KEY}})
        self.cmds, self.started = [], threading.Event()

    get = ApiSettingsTest.get
    post = ApiSettingsTest.post

    def fake_pipeline(self, cmd, **kw):
        """冒充一局流水线：报到第 3 步，等停止文件出现后收尾，报已停止。"""
        self.cmds.append(cmd)
        report = progress.Progress(cmd[cmd.index("--progress") + 1], cmd[cmd.index("--stop-file") + 1])
        report.step(3, group="国会", index=3, total=5)
        self.started.set()
        deadline = time.time() + 10
        while not report.stop_requested() and time.time() < deadline:
            time.sleep(0.02)
        report.result(events=2)
        report.finish("stopped")
        return subprocess.CompletedProcess(cmd, run.EXIT_STOPPED, "", "")

    def test_start_progress_and_stop(self):
        with mock.patch.object(server.subprocess, "run", self.fake_pipeline):
            self.assertEqual(self.post("/api/update", {})[1], {"ok": True})
            self.assertTrue(self.started.wait(5))
            view = self.get("/api/update")
            self.assertEqual((view["state"], view["running"], view["step"]), ("running", True, 3))
            self.assertEqual(view["text"], "正在深度思考……（逐项研判人事变动　国会 3/5）")
            self.assertEqual(self.post("/api/update", {})[1], {"ok": False, "message": "更新正在进行"})
            self.assertEqual(self.post("/api/update/stop", {})[1], {"ok": True, "state": "stopping"})
            for _ in range(250):
                view = self.get("/api/update")
                if not view["running"]:
                    break
                time.sleep(0.02)
        self.assertEqual((view["state"], view["percent"]), ("stopped", 100))
        self.assertEqual(view["message"], "更新已停止。已核实的 2 条人事动态已保存。")
        (cmd,) = self.cmds
        for flag in ("--progress", "--stop-file", "--backend", "--journal"):
            self.assertIn(flag, cmd)
        self.assertNotIn(FAKE_KEY, " ".join(cmd), "密钥不在命令行里")
        self.assertEqual(self.post("/api/update/stop", {})[1], {"ok": False, "message": "没有正在进行的更新"})

    def test_progress_left_behind_by_an_interrupted_run(self):
        progress_file, _ = server.update_files()
        report = progress.Progress(progress_file)
        report.step(4, index=3, total=12)
        view = self.get("/api/update")
        self.assertEqual((view["state"], view["running"]), ("failed", False))
        self.assertEqual(view["message"], "更新未完成：程序中断。已核实的结果已保存。")
        progress_file.unlink()
        self.assertEqual(self.get("/api/update")["state"], "idle")


if __name__ == "__main__":
    unittest.main()
