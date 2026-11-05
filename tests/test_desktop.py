"""桌面版：关窗退出（窗口进程结束、页面都说了再见、长时间没人用）、单实例、安装信息、本机目录、铺底。"""

import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from zuzhibu import __main__ as launcher
from zuzhibu import datasync, desktop, server


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class LifetimeTest(unittest.TestCase):
    def test_bye_grace_and_idle(self):
        clock = Clock()
        life = desktop.Lifetime(grace=10, idle_limit=100, clock=clock)
        life.touch("a")
        life.touch("b")
        life.bye("a")
        clock.t += 20
        self.assertIsNone(life.reason(), "还有窗口开着")
        life.bye("b")
        clock.t += 5
        self.assertIsNone(life.reason(), "刷新页面的工夫不退")
        life.touch("c")
        clock.t += 20
        self.assertIsNone(life.reason(), "新页面来了就不退")
        life.bye("c")
        clock.t += 11
        self.assertEqual(life.reason(), "窗口已关闭")
        life2 = desktop.Lifetime(grace=10, idle_limit=100, clock=clock)
        life2.touch("x")
        clock.t += 101
        self.assertEqual(life2.reason(), "长时间没有使用")

    def test_watch_stops_when_window_process_ends(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.6)"])
        stopped = threading.Event()
        t = desktop.watch(desktop.Lifetime(), proc, stopped.set, poll=0.05, handoff=0.3)
        t.join(10)
        self.assertTrue(stopped.is_set())

    def test_watch_falls_back_to_bye_after_handoff(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        life = desktop.Lifetime(grace=0.1)
        life.touch("w")
        stopped = threading.Event()
        desktop.watch(life, proc, stopped.set, poll=0.05, handoff=5)
        time.sleep(0.4)
        self.assertFalse(stopped.is_set(), "Edge 进程马上结束是交给了已开着的 Edge，不能退")
        life.bye("w")
        self.assertTrue(stopped.wait(5))


class ParentWatchTest(unittest.TestCase):
    def test_stops_when_parent_ends(self):
        """桌面外壳（父进程）一结束，本机服务就收尾退出，外壳崩溃也不留孤儿进程。"""
        parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.8)"])
        stopped = threading.Event()
        with mock.patch("sys.stdout", io.StringIO()):
            desktop.watch_parent(parent.pid, stopped.set, poll=0.1)
            self.assertFalse(stopped.wait(0.3), "父进程还在，不退")
            parent.wait(10)
            self.assertTrue(stopped.wait(10))

    def test_missing_parent_stops_at_once(self):
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait(10)
        stopped = threading.Event()
        with mock.patch("sys.stdout", io.StringIO()):
            desktop.watch_parent(gone.pid, stopped.set, poll=0.1)
            self.assertTrue(stopped.wait(10))


class InstanceTest(unittest.TestCase):
    def test_claim_probe_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / "data"
            (data / "people").mkdir(parents=True)
            (data / "meta.json").write_text("{}", encoding="utf-8")
            srv = server.make_server(0, str(data))
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                inst = desktop.Instance(Path(tmp))
                self.assertIsNone(inst.alive_url(), "没有记录")
                inst.claim(srv.server_address[1], "wrong-token")
                self.assertIsNone(inst.alive_url(), "口令不对不认")
                inst.claim(srv.server_address[1], srv.app.instance)
                self.assertEqual(inst.alive_url(), "http://127.0.0.1:%d/" % srv.server_address[1])
                inst.release("other")
                self.assertTrue(inst.path.exists(), "别人的记录不删")
                inst.release(srv.app.instance)
                self.assertFalse(inst.path.exists())
            finally:
                srv.shutdown()
                srv.server_close()


class HomeAndDistTest(unittest.TestCase):
    def test_app_home_and_dist(self):
        with mock.patch.dict(os.environ, {"ZUZHIBU_HOME": "zzb-home-test"}):
            self.assertEqual(datasync.app_home(), Path("zzb-home-test"))
            self.assertEqual(datasync.default_home(), Path("zzb-home-test") / "data")
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(desktop.dist_info(Path(tmp)), "从仓库运行没有 dist.json")
            (Path(tmp) / "dist.json").write_text(json.dumps({"version": "0.8.0"}), encoding="utf-8")
            self.assertEqual(desktop.dist_info(Path(tmp))["version"], "0.8.0")
        self.assertIsNone(desktop.dist_info(), "仓库根目录不放 dist.json")

    def test_logging_without_console(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, err = sys.stdout, sys.stderr
            try:
                sys.stdout = sys.stderr = None
                path = desktop.setup_logging(Path(tmp))
                print("记一行")
                sys.stdout.close()
            finally:
                sys.stdout, sys.stderr = out, err
            self.assertIn("记一行", path.read_text(encoding="utf-8"))
            self.assertIsNone(desktop.setup_logging(Path(tmp)), "有控制台时不改输出")

    def test_prepare_data_seeds_and_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundled = Path(tmp) / "bundled"
            (bundled / "people").mkdir(parents=True)
            (bundled / "people" / "a.json").write_text('{"id": "a"}', encoding="utf-8")
            (bundled / "meta.json").write_text('{"updated_at": "2026-10-20"}', encoding="utf-8")
            with mock.patch("sys.stdout", io.StringIO()):
                home = launcher.prepare_data(Path(tmp) / "home" / "data", bundled)
                self.assertIsNotNone(home)
                self.assertTrue((home.current() / "people" / "a.json").is_file())
                blocked = Path(tmp) / "blocked"
                blocked.write_text("不是目录", encoding="utf-8")
                self.assertIsNone(launcher.prepare_data(blocked / "data", bundled), "本机目录用不了，改读附带数据")

    def test_data_and_sync_conflict(self):
        with mock.patch("sys.stderr", io.StringIO()), self.assertRaises(SystemExit):
            launcher.main(["--data", "x", "--sync", "--no-window"])


if __name__ == "__main__":
    unittest.main()
