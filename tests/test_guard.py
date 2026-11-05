"""守护检查 tools/guard.py（docs/公开前检查.md）：仓库当前文件没有字体、凭据和第三方 App 名称；规则本身用临时文件和拼出来的假串测，测试文件里不出现整串假密钥。"""

import csv
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools import guard

FAKE_KEY = "sk-" + "Ab3" * 12


class RepoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = guard.tracked_files()

    def test_no_font_files(self):
        self.assertEqual(guard.fonts(self.files), [])

    def test_no_third_party_names(self):
        found = sorted({f for f, _ in guard.third_party_names(self.files)})
        self.assertEqual(found, [], "第三方 App 名称见 docs/公开前检查.md 第二节")

    def test_no_credentials_in_project_files(self):
        project = [f for f in self.files if not guard.is_data(f)]
        self.assertEqual(guard.credential_files(project), [])


    def test_parody_statement_only_in_readme_and_about(self):
        # 维护者 2026-10-21 定：戏仿和免责说明只放在 README 最下面的“关于”和软件的“关于”里，别处不写（开发文档、测试除外）。
        found = {f for f in self.files if not guard.is_data(f) and not f.startswith(("docs/", "tests/", "web/tests/"))
                 and "戏仿" in (guard.read_text(guard.REPO, f) or "")}
        self.assertEqual(found, {"README.md", "web/js/about.js"}, "软件里只有“关于”页（web/js/about.js）写")
        last = (guard.REPO / "README.md").read_text(encoding="utf-8").rstrip().rsplit("\n", 1)[-1]
        self.assertIn("本项目为戏仿项目", last)

class RuleTest(unittest.TestCase):
    def test_credentials(self):
        self.assertEqual(guard.credentials(f'OPENAI = "{FAKE_KEY}"'), [("AI 接口密钥", FAKE_KEY[:10] + "…")])
        self.assertTrue(guard.credentials("-----BEGIN RSA " + "PRIVATE KEY-----"))
        self.assertTrue(guard.credentials("api_key = '" + "Qw7" * 4 + "'"))
        # 网址里的英文片段、示例和占位不算
        self.assertEqual(guard.credentials("https://www.example.gov/news/2026/03/establishing-the-task-force-to-eliminate-fraud/"), [])
        self.assertEqual(guard.credentials('api_key = "your-api-key-here"'), [])

    def test_names_skip_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs").mkdir()
            (root / "docs" / "a.md").write_text("用某某软件打开", encoding="utf-8")
            (root / "data" / "people").mkdir(parents=True)
            (root / "data" / "people" / "x.json").write_text('{"org": "某某软件"}', encoding="utf-8")
            files = ["docs/a.md", "data/people/x.json"]
            self.assertEqual(guard.third_party_names(files, root, names=("某某软件",)), [("docs/a.md", "某某软件")])

    def test_portrait_licenses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "data" / "portraits"
            folder.mkdir(parents=True)
            with (folder / "index.csv").open("w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["person_id", "license"])
                w.writerows([["a", "公有领域：美国政府作品"], ["b", "CC BY 2.0：作者某"], ["c", "CC BY-NC 2.0：作者某"],
                             ["d", "版权保留：公司官网，未见开放许可"], ["e", "多为美国政府作品（公有领域），公开前逐张核对"]])
            self.assertEqual([p for p, _ in guard.portrait_licenses(root)], ["c", "d", "e"])

    def test_long_excerpts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "people").mkdir(parents=True)
            rec = {"resume": {"sources": [{"url": "https://www.example.gov/bio", "excerpt": "x" * 400},
                                          {"url": "https://news.example.com/a", "excerpt": "y" * 301},
                                          {"url": "https://news.example.com/b", "excerpt": "短句"}]}}
            (root / "data" / "people" / "p.json").write_text(json.dumps(rec), encoding="utf-8")
            got = guard.long_excerpts(["data/people/p.json"], root)
            self.assertEqual([(h, n) for _, h, n in got], [("www.example.gov", 400), ("news.example.com", 301)])
            self.assertTrue(guard.is_federal("www.example.gov"))

    @unittest.skipUnless(shutil.which("git"), "要有 git")
    def test_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def git(*args):
                subprocess.run(["git", "-C", tmp, "-c", "commit.gpgsign=false", "-c", "core.hooksPath=no-hooks", *args], check=True, capture_output=True)

            git("init", "-q")
            (root / "a.txt").write_text(f'key = "{FAKE_KEY}"\n', encoding="utf-8")
            git("add", "a.txt")
            git("-c", "user.name=t", "-c", "user.email=someone@example.org", "commit", "-q", "-m", "x")
            (root / "a.txt").write_text("clean\n", encoding="utf-8")
            git("-c", "user.name=t", "-c", "user.email=1+t@users.noreply.github.com", "commit", "-q", "-am", "y")
            found, emails = guard.history(root)
            self.assertEqual([(p, k) for _, p, k, _ in found], [("a.txt", "AI 接口密钥")])
            self.assertEqual(dict(emails), {"someone@example.org": 1})


if __name__ == "__main__":
    unittest.main()
