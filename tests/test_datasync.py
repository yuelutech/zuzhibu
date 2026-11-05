"""数据分发：清单、增量更新、逐个校验、快照切换；联网成功、失败（404、连不上、校验不符）、半途中断后接着下。

“远端”是本机临时目录加一个只监听 127.0.0.1 的小服务，可以按文件注入故障。人物、动态全部虚构。
"""

import gzip
import hashlib
import json
import os
import shutil
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from zuzhibu import datasync
from zuzhibu.datasync import DataHome, SyncError


def write(root: Path, rel: str, obj) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, bytes):
        path.write_bytes(obj)
    else:
        path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def make_data(root: Path, date="2026-10-20") -> Path:
    write(root, "meta.json", {"schema": "zuzhibu-data/1", "baseline": "2026年10月版", "updated_at": date})
    write(root, "people/lin-mu.json", {"id": "lin-mu", "name_zh": "林木", "bureau": "congress"})
    write(root, "people/he-shan.json", {"id": "he-shan", "name_zh": "何山", "bureau": "ai"})
    write(root, "events/2026-10/2026-10-01-lin-mu-leave.json", {"id": "2026-10-01-lin-mu-leave", "date": "2026-10-01", "person_id": "lin-mu"})
    write(root, "portraits/lin-mu.jpg", b"\xff\xd8\xff\xe0fake-jpeg" * 50)
    write(root, "portraits/index.csv", "person_id,file\nlin-mu,lin-mu.jpg\n".encode("utf-8"))
    return root


def publish(root: Path, generated_at: str) -> dict:
    """像维护者那样重算清单，生成时间指定好，便于排先后。"""
    manifest = datasync.build_manifest(root)
    manifest["generated_at"] = generated_at
    (root / datasync.MANIFEST).write_text(datasync.dumps_manifest(manifest), encoding="utf-8", newline="\n")
    return manifest


class Remote:
    """本机“数据地址”：按目录提供文件；faults 按相对路径注入故障（cut 断在半中间、bad 换内容、404）。"""

    def __init__(self, root: Path, gzip_text: bool = False):
        self.root = root
        self.faults: dict[str, str] = {}
        self.requests: list[str] = []
        self.gzip_text = gzip_text
        remote = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802
                rel = self.path.split("?")[0].lstrip("/").removeprefix("data/")
                remote.requests.append(rel)
                fault = remote.faults.get(rel)
                path = remote.root / rel
                if fault == "404" or not path.is_file():
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = path.read_bytes()
                etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'
                if rel == datasync.MANIFEST and self.headers.get("If-None-Match") == etag:
                    self.send_response(304)
                    self.end_headers()
                    return
                if fault == "bad":
                    body = body[:-2] + b"!\n"
                encoded = gzip.compress(body) if remote.gzip_text and rel.endswith(".json") else body
                self.send_response(200)
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("ETag", etag)
                if encoded is not body:
                    self.send_header("Content-Encoding", "gzip")
                self.end_headers()
                if fault == "cut":
                    self.wfile.write(encoded[: len(encoded) // 2])
                    self.wfile.flush()
                    return
                self.wfile.write(encoded)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/data/" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Crash(BaseException):
    """模拟进程在某一步被强行结束（不是普通异常，apply 不会替它收拾）。"""


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.bundled = make_data(base / "bundled")
        publish(self.bundled, "2026-10-20T00:05:00Z")
        self.remote_dir = base / "remote"
        shutil.copytree(self.bundled, self.remote_dir)
        self.home = DataHome(base / "home")

    def serve(self, **kw) -> Remote:
        remote = Remote(self.remote_dir, **kw)
        self.addCleanup(remote.close)
        return remote

    def seed(self):
        result = datasync.ensure_seed(self.home, self.bundled)
        self.assertEqual(result["status"], "updated")
        return result

    def next_day(self):
        """远端出了新一天的数据：改一人、删一人、加一条动态、改 meta。"""
        write(self.remote_dir, "meta.json", {"schema": "zuzhibu-data/1", "baseline": "2026年10月版", "updated_at": "2026-10-21"})
        write(self.remote_dir, "people/lin-mu.json", {"id": "lin-mu", "name_zh": "林木", "bureau": "retired"})
        (self.remote_dir / "people" / "he-shan.json").unlink()
        write(self.remote_dir, "events/2026-10/2026-10-21-lin-mu-retire.json", {"id": "2026-10-21-lin-mu-retire", "date": "2026-10-21", "person_id": "lin-mu"})
        return publish(self.remote_dir, "2026-10-21T00:05:00Z")

    def current_files(self) -> dict:
        cur = self.home.current()
        return {rel: (cur / rel).read_bytes() for rel in datasync.iter_files(cur)}


class ManifestTest(unittest.TestCase):
    def test_safe_path(self):
        good = ["meta.json", "people/lin-mu.json", "events/2026-10/2026-10-01-x.json", "portraits/lin-mu.jpg", "portraits/index.csv",
                "rosters/whitehouse.json"]
        bad = ["", "../meta.json", "/meta.json", "people/../meta.json", "people\\x.json", "people/x.exe", "other/x.json",
               "people/con.json", "people/x.json.", " people/x.json", "people/a/b/c.json", "people/.hidden.json", "manifest.json"]
        for rel in good:
            self.assertTrue(datasync.safe_path(rel), rel)
        for rel in bad:
            self.assertFalse(datasync.safe_path(rel), rel)

    def test_build_write_and_keep_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_data(Path(tmp))
            (root / ".tmp-x.json").write_text("{}", encoding="utf-8")
            manifest, changed = datasync.write_manifest(root)
            self.assertTrue(changed)
            self.assertEqual(sorted(manifest["files"]), sorted(datasync.iter_files(root)))
            self.assertNotIn(".tmp-x.json", manifest["files"])
            self.assertEqual(manifest["data_date"], "2026-10-20")
            self.assertEqual(manifest["counts"], {"people": 2, "events": 1, "portraits": 1})
            entry = manifest["files"]["people/lin-mu.json"]
            self.assertEqual(entry["sha256"], hashlib.sha256((root / "people/lin-mu.json").read_bytes()).hexdigest())
            text = (root / "manifest.json").read_text(encoding="utf-8")
            self.assertEqual(text.count("\n"), len(manifest["files"]) + 9, "一行一个文件")
            again, changed = datasync.write_manifest(root)
            self.assertFalse(changed, "文件没变，清单不改")
            self.assertEqual(again["generated_at"], manifest["generated_at"])
            write(root, "people/lin-mu.json", {"id": "lin-mu", "name_zh": "林木", "bureau": "retired"})
            third, changed = datasync.write_manifest(root)
            self.assertTrue(changed)
            self.assertTrue(datasync.is_newer(third, manifest))

    def test_parse_rejects_bad_manifests(self):
        good = {"schema": datasync.SCHEMA, "generated_at": "2026-10-20T00:00:00Z",
                "files": {"meta.json": {"sha256": "0" * 64, "size": 3}}}
        datasync.parse_manifest(json.dumps(good).encode())
        cases = {
            "schema": {**good, "schema": "other/1"},
            "stamp": {**good, "generated_at": "yesterday"},
            "path": {**good, "files": {**good["files"], "../evil.json": {"sha256": "0" * 64, "size": 1}}},
            "case": {**good, "files": {**good["files"], "people/A.json": {"sha256": "0" * 64, "size": 1},
                                       "people/a.json": {"sha256": "0" * 64, "size": 1}}},
            "sha": {**good, "files": {"meta.json": {"sha256": "xyz", "size": 3}}},
            "size": {**good, "files": {"meta.json": {"sha256": "0" * 64, "size": datasync.MAX_FILE + 1}}},
            "meta": {**good, "files": {"people/a.json": {"sha256": "0" * 64, "size": 3}}},
        }
        for name, body in cases.items():
            with self.assertRaises(SyncError, msg=name) as cm:
                datasync.parse_manifest(json.dumps(body).encode())
            self.assertEqual(cm.exception.kind, "format")
        with self.assertRaises(SyncError):
            datasync.parse_manifest(b"not json")

    def test_normalize_url(self):
        self.assertEqual(datasync.normalize_url("https://example.com/data"), "https://example.com/data/")
        self.assertEqual(datasync.normalize_url("http://127.0.0.1:9/x/"), "http://127.0.0.1:9/x/")
        for bad in ("http://example.com/data/", "ftp://example.com/", "https://example.com/?a=1", "data/"):
            with self.assertRaises(ValueError, msg=bad):
                datasync.normalize_url(bad)

    def test_repo_manifest_is_fresh(self):
        """软件联网按 data/manifest.json 取数：改了 data/ 却没重算清单，用户就一直取不到新数据（一局已改为每月更新）。"""
        path = datasync.DEFAULT_DATA / datasync.MANIFEST
        old = datasync.parse_manifest(path.read_bytes())["files"]
        new = datasync.build_manifest(datasync.DEFAULT_DATA)["files"]
        stale = sorted(rel for rel in old.keys() | new.keys() if old.get(rel) != new.get(rel))
        self.assertFalse(stale, f"data/manifest.json 和 data/ 对不上（{len(stale)} 个文件，如 {stale[:3]}）："
                                "提交数据前运行 python -m zuzhibu.datasync manifest")


class SeedTest(Base):
    def test_seed_once_and_upgrade_from_newer_bundle(self):
        self.seed()
        self.assertEqual(self.current_files(), {rel: (self.bundled / rel).read_bytes() for rel in datasync.iter_files(self.bundled)})
        self.assertEqual(self.home.pointer_data()["origin"], "bundled")
        self.assertIsNone(datasync.ensure_seed(self.home, self.bundled), "附带数据不比本机新，不重铺")
        # 本机“立即更新”加了一条动态
        mine = "events/2026-10/2026-10-02-he-shan-appoint.json"
        write(self.home.current(), mine, {"id": "2026-10-02-he-shan-appoint", "date": "2026-10-02", "person_id": "he-shan"})
        # 装了新版，附带数据更新
        write(self.bundled, "people/he-shan.json", {"id": "he-shan", "name_zh": "何山", "bureau": "ai", "tier": "gold"})
        publish(self.bundled, "2026-10-25T00:05:00Z")
        result = datasync.ensure_seed(self.home, self.bundled)
        self.assertEqual(result["files"], 1, "只换改过的那一个人物")
        files = self.current_files()
        self.assertIn(mine, files, "本机写进来的动态保留")
        self.assertIn(b'"gold"', files["people/he-shan.json"])
        self.assertEqual(len(list(self.home.snapshots.iterdir())), 1, "旧快照删掉")

    def test_bundle_without_manifest_is_oldest(self):
        (self.bundled / "manifest.json").unlink()
        self.seed()
        self.assertEqual(self.home.current_manifest()["generated_at"], datasync.OLDEST)

    def test_pointer_repair(self):
        self.seed()
        cur = self.home.current()
        self.home.pointer.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.home.current(), cur, "指针坏了找最新一份完整快照")
        self.home.recover()
        self.assertEqual(self.home.pointer_data()["snapshot"], cur.name)

    def test_state_and_pointer_writes_survive_concurrent_reads(self):
        """服务每秒都在读 current.json 和 sync.json；Windows 上读的同时替换会被拒绝，要重试，不能丢状态。"""
        self.seed()
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                self.home.state()
                self.home.current()

        threads = [threading.Thread(target=reader) for _ in range(3)]
        for t in threads:
            t.start()
        try:
            for i in range(150):
                self.home.save_state(n=i)
                self.home._point(self.home.current(), {"n": i})
        finally:
            stop.set()
            for t in threads:
                t.join()
        self.assertEqual(self.home.state()["n"], 149)
        self.assertEqual(self.home.pointer_data()["n"], 149)

    def test_copy_when_links_unsupported(self):
        with mock.patch("os.link", side_effect=OSError("no links")):
            self.seed()
            write(self.remote_dir, "people/lin-mu.json", {"id": "lin-mu", "name_zh": "林木", "bureau": "retired"})
            remote = self.serve()
            publish(self.remote_dir, "2026-10-21T00:05:00Z")
            self.assertEqual(datasync.check(self.home, [remote.url])["status"], "updated")
        self.assertIn(b"retired", self.current_files()["people/lin-mu.json"])


class OnlineTest(Base):
    def test_success_incremental_then_304(self):
        self.seed()
        remote = self.serve(gzip_text=True)
        old = self.home.current()
        mine = "events/2026-10/2026-10-02-he-shan-appoint.json"
        write(old, mine, {"id": "2026-10-02-he-shan-appoint", "date": "2026-10-02", "person_id": "he-shan"})
        self.next_day()
        result = datasync.check(self.home, [remote.url])
        self.assertEqual(result["status"], "updated", result)
        self.assertEqual(result["files"], 3)
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(sorted(remote.requests), sorted(["manifest.json", "meta.json", "people/lin-mu.json",
                                                          "events/2026-10/2026-10-21-lin-mu-retire.json"]),
                         "只下载改过的文件")
        files = self.current_files()
        for rel in datasync.iter_files(self.remote_dir):
            self.assertEqual(files[rel], (self.remote_dir / rel).read_bytes(), rel)
        self.assertNotIn("people/he-shan.json", files, "发布方删掉的删掉")
        self.assertIn(mine, files, "本机加的保留")
        self.assertFalse(old.exists(), "旧快照删掉")
        self.assertFalse(self.home.staging.exists())
        state = self.home.state()
        self.assertEqual(state["last_status"], "updated")
        self.assertEqual(self.home.pointer_data()["data_date"], "2026-10-21")
        remote.requests.clear()
        again = datasync.check(self.home, [remote.url])
        self.assertEqual(again["status"], "current")
        self.assertEqual(remote.requests, ["manifest.json"], "没变化时只问一次清单（304）")

    def test_older_remote_is_not_applied(self):
        self.seed()
        remote = self.serve()
        publish(self.remote_dir, "2026-10-19T00:05:00Z")
        before = self.current_files()
        self.assertEqual(datasync.check(self.home, [remote.url])["status"], "current")
        self.assertEqual(self.current_files(), before)

    def test_private_repo_404_keeps_local(self):
        self.seed()
        remote = self.serve()
        remote.faults["manifest.json"] = "404"
        before, pointer = self.current_files(), self.home.pointer_data()
        result = datasync.check(self.home, [remote.url])
        self.assertEqual(result["status"], "unreachable")
        self.assertIn("404", result["message"])
        self.assertIn("继续用本机数据", result["message"])
        self.assertEqual(self.current_files(), before)
        self.assertEqual(self.home.pointer_data(), pointer)
        self.assertEqual(self.home.state()["last_status"], "unreachable")

    def test_connection_refused_and_fallback_source(self):
        self.seed()
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            dead = "http://127.0.0.1:%d/data/" % s.getsockname()[1]
        self.assertEqual(datasync.check(self.home, [dead], timeout=5)["status"], "unreachable")
        remote = self.serve()
        self.next_day()
        result = datasync.check(self.home, [dead, remote.url], timeout=5)
        self.assertEqual(result["status"], "updated", "第一个地址不通就试下一个")
        self.assertEqual(result["source"], remote.url)

    def test_hash_mismatch_keeps_local_and_retries(self):
        self.seed()
        remote = self.serve()
        self.next_day()
        remote.faults["people/lin-mu.json"] = "bad"
        before = self.current_files()
        result = datasync.check(self.home, [remote.url])
        self.assertEqual(result["status"], "failed")
        self.assertIn("本机数据未改动", result["message"])
        self.assertEqual(self.current_files(), before)
        self.assertNotIn(remote.url, self.home.state().get("etags") or {}, "失败不记 ETag，下次重新取清单")
        del remote.faults["people/lin-mu.json"]
        self.assertEqual(datasync.check(self.home, [remote.url])["status"], "updated")

    def test_unreadable_data_is_not_switched(self):
        self.seed()
        remote = self.serve()
        write(self.remote_dir, "people/lin-mu.json", {"name_zh": "缺编号"})
        publish(self.remote_dir, "2026-10-21T00:05:00Z")
        before = self.current_files()
        result = datasync.check(self.home, [remote.url])
        self.assertEqual(result["status"], "failed")
        self.assertIn("读不了", result["message"])
        self.assertEqual(self.current_files(), before)
        self.assertEqual(list(self.home.snapshots.glob("*.partial")), [])

    def test_interrupted_download_resumes(self):
        self.seed()
        remote = self.serve()
        self.next_day()
        remote.faults["people/lin-mu.json"] = "cut"  # 按路径排在最后，前两个先下好
        before = self.current_files()
        result = datasync.check(self.home, [remote.url], workers=1)
        self.assertEqual(result["status"], "unreachable")
        self.assertIn("连接中断", result["message"])
        self.assertEqual(self.current_files(), before, "半途断了，本机数据不变")
        staged = sorted(p.name for p in self.home.staging.iterdir())
        self.assertEqual(len(staged), 2, "断之前下好的两个文件留着")
        del remote.faults["people/lin-mu.json"]
        remote.requests.clear()
        self.assertEqual(datasync.check(self.home, [remote.url])["status"], "updated")
        self.assertEqual(sorted(remote.requests), ["manifest.json", "people/lin-mu.json"],
                         "接着下：已下好的不再下")

    def test_crash_at_each_step_never_breaks_current(self):
        steps = ["download", "downloaded", "copy", "built", "renamed", "switched"]
        for step in steps:
            with self.subTest(step=step):
                self.setUp()
                self.seed()
                remote = self.serve()
                manifest = self.next_day()
                before = self.current_files()

                def hook(at, step=step):
                    if at == step:
                        raise Crash(step)

                with self.assertRaises(Crash):
                    datasync.check(self.home, [remote.url], hook=hook, workers=1)
                files = self.current_files()
                after = {rel: (self.remote_dir / rel).read_bytes() for rel in manifest["files"]}
                self.assertIn(files, (before, after), "要么是原来那份，要么是新的一份，没有半新半旧")
                if step != "switched":
                    self.assertEqual(files, before, f"{step} 前后中断，仍是原来的完整数据")
                self.home.recover(older_than=0)
                self.assertEqual(list(self.home.snapshots.glob("*.partial")), [])
                self.assertEqual(len([p for p in self.home.snapshots.iterdir() if p.is_dir()]), 1)
                result = datasync.check(self.home, [remote.url])
                self.assertIn(result["status"], ("updated", "current"))
                self.assertEqual(self.home.current_manifest()["generated_at"], manifest["generated_at"])


class SyncerTest(Base):
    def test_single_run_and_due(self):
        self.seed()
        remote = self.serve()
        gate = threading.Event()
        real_check = datasync.check

        def slow_check(*a, **kw):
            gate.wait(5)
            return real_check(*a, **kw)

        syncer = datasync.Syncer(self.home, lambda: [remote.url])
        with mock.patch.object(datasync, "check", slow_check):
            self.assertTrue(syncer.start())
            self.assertFalse(syncer.start(), "同一时间只跑一个")
            self.assertTrue(syncer.status()["running"])
            gate.set()
            for _ in range(100):
                if not syncer.running:
                    break
                time.sleep(0.05)
        self.assertFalse(syncer.running)
        self.assertEqual(syncer.last["status"], "current")
        self.assertFalse(syncer.due())
        self.assertTrue(syncer.due(time.time() + datasync.INTERVAL + 1))

    def test_loop_respects_auto_off(self):
        syncer = datasync.Syncer(self.home, lambda: [], auto=lambda: False)
        with mock.patch.object(syncer, "start") as start:
            t = syncer.run_in_background(first_delay=0, poll=0.01)
            time.sleep(0.1)
            syncer.stop()
            t.join(2)
        start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
