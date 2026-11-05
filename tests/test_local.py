"""本机设置 zuzhibu/local.py，和四局重核从本机规则目录拼提示词（reverify.rules_dir、rules_text）。全部用临时目录和虚构姓名。"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zuzhibu import local

REPO = Path(__file__).resolve().parents[1]


def load_reverify():
    spec = importlib.util.spec_from_file_location("reverify", REPO / "bureaus" / "personnel-congress" / "reverify.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass 要在 sys.modules 里找到模块
    spec.loader.exec_module(module)
    return module


class LocalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = {k: v for k, v in os.environ.items() if not k.startswith("ZUZHIBU_")}
        env["APPDATA"] = self.tmp.name
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, conf):
        path = Path(self.tmp.name, "zuzhibu", "local.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(conf, ensure_ascii=False), encoding="utf-8")

    def test_file_env_and_missing(self):
        self.assertIsNone(local.get("python"))
        self.assertEqual(local.get("python", "python"), "python")
        self.write({"python": "某处/python.exe", "aipolis_rules": self.tmp.name})
        self.assertEqual(local.get("python"), "某处/python.exe")
        self.assertEqual(local.get_path("aipolis_rules"), Path(self.tmp.name))
        with mock.patch.dict(os.environ, {"ZUZHIBU_PYTHON": "别处/python.exe"}):
            self.assertEqual(local.get("python"), "别处/python.exe")

    def test_scrub(self):
        self.assertEqual(local.scrub("交某维护者确认"), "交某维护者确认")
        self.write({"maintainer_name": "某维护者"})
        self.assertEqual(local.scrub("交某维护者确认"), "交AI政见确认")

    def test_reverify_reads_rules_from_given_dir(self):
        reverify = load_reverify()
        rules = Path(self.tmp.name, "rules")
        rules.mkdir()
        with self.assertRaises(reverify.FatalError):
            reverify.rules_dir(rules)
        (rules / "research.md").write_text("# 研究\n\n## 证据与日期\n\n冲突交某维护者确认。\n\n## 干部字段纪律\n\n甲\n\n## 两次会话和放行条件\n\n乙\n", encoding="utf-8")
        (rules / "cadre.md").write_text("# 写法\n\n## 1 首段\n\n丙\n\n## 2 履历\n\n## 3 日期\n\n## 4 来源\n", encoding="utf-8")
        (rules / "party_join.md").write_text("# 入党\n\n丁\n", encoding="utf-8")
        self.assertEqual(reverify.rules_dir(rules), rules)
        self.write({"aipolis_rules": str(rules), "maintainer_name": "某维护者"})
        self.assertEqual(reverify.rules_dir(None), rules)
        text = reverify.rules_text(reverify.rules_dir(None))
        self.assertIn("冲突交AI政见确认", text)
        self.assertNotIn("某维护者", text)
        self.assertEqual(reverify.repo_path(REPO / "output" / "x"), "output/x")


if __name__ == "__main__":
    unittest.main()
