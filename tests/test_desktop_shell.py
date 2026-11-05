"""桌面外壳（desktop/）：版本号和主程序一致、窗口和 preload 的约定、规则测试（node --test，本机没有 Node 时跳过）。"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from zuzhibu.server import VERSION

DESKTOP = Path(__file__).resolve().parent.parent / "desktop"


class ShellTest(unittest.TestCase):
    def test_version_matches_service(self):
        self.assertEqual(json.loads((DESKTOP / "package.json").read_text(encoding="utf-8"))["version"], VERSION,
                         "desktop/package.json 的版本号要和 zuzhibu/server.py 的 VERSION 一致")

    def test_lock_file_committed_and_node_modules_ignored(self):
        self.assertTrue((DESKTOP / "package-lock.json").is_file())
        ignore = (DESKTOP.parent / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("node_modules/", ignore)

    def test_window_and_preload_contract(self):
        """界面会话依赖的约定：系统画的标题栏按钮、页面只拿到 window.shell 四项、隔离和沙箱开着、外链交给系统浏览器。"""
        main = (DESKTOP / "main.js").read_text(encoding="utf-8")
        for need in ("titleBarStyle: 'hidden'", "titleBarOverlay: OVERLAY", "contextIsolation: true", "nodeIntegration: false",
                     "sandbox: true", "setWindowOpenHandler", "'will-navigate'", "requestSingleInstanceLock", "'--parent-pid'"):
            self.assertIn(need, main)
        preload = (DESKTOP / "preload.js").read_text(encoding="utf-8")
        exposed = re.search(r"exposeInMainWorld\('shell', Object\.freeze\(\{(.*?)\}\)\);", preload, re.S)
        self.assertIsNotNone(exposed)
        keys = re.findall(r"^\s{2}(\w+)", exposed.group(1), re.M)
        self.assertEqual(keys, ["version", "platform", "openExternal", "setTitleBarOverlay"])
        self.assertEqual(preload.count("exposeInMainWorld"), 1, "只暴露 window.shell")

    def test_startup_crash_recovery(self):
        """启动时页面进程、显卡进程崩溃有人管（lib/recovery.js）；本地服务不等“正在打开”页；模拟崩溃只在试装模式下有效。"""
        main = (DESKTOP / "main.js").read_text(encoding="utf-8")
        for need in ("'render-process-gone'", "'child-process-gone'", "app.disableHardwareAcceleration()", "app.relaunch(",
                     "RELAUNCH_GRACE", "recovery.plan(", "recovery.GPU_CACHES", "dialog.showMessageBoxSync"):
            self.assertIn(need, main)
        self.assertNotIn("await win.loadFile(path.join(PAGES, 'loading.html'))", main, "页面进程崩了不能拦住本地服务启动")
        self.assertNotRegex(main, r"render-process-gone[^\n]*\n[^\n]*contents\.reload\(\)", "崩了不能无条件重新加载")
        self.assertIn("const TEST_CRASH = TEST_DIR ?", main)
        self.assertIn("lib/**", (DESKTOP / "electron-builder.yml").read_text(encoding="utf-8"), "lib/recovery.js 要打进安装包")

    def test_store_home(self):
        """商店版（MSIX）的本机目录放包自己的数据目录（lib/store.js），本地服务和它开的子进程也认这个目录。"""
        main = (DESKTOP / "main.js").read_text(encoding="utf-8")
        self.assertIn("process.windowsStore && store.storeHome(process.execPath, process.env.LOCALAPPDATA)", main)
        self.assertIn("ZUZHIBU_HOME: HOME", main)

    def test_policy_rules_with_node(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("本机没有 Node")
        proc = subprocess.run([node, "--test", "test/*.test.js"], cwd=DESKTOP, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout[-3000:] + proc.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
