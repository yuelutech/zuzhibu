"""秘书局、宣传教育局 run.py 的端到端测试：假 codex、夹具译名表、虚构原文，不联网。"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from bureaus.publicity import run as publicity
from bureaus.secretariat import run as secretariat
from shared.backends import codex_cli, lint_hook, pipeline
from shared.backends.glossary import Glossary

TESTS = Path(__file__).resolve().parent
FAKE = TESTS / "fakes" / "fake_codex.py"
FIXTURES = TESTS / "fixtures"
GLOSSARY_DIR = FIXTURES / "glossary"


class BureauCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zuzhibu-bureau-"))
        self.out = self.tmp / "out"
        self.record = self.tmp / "record.jsonl"
        self.env = {
            "FAKE_CODEX_SCRIPT": "ok",
            "FAKE_CODEX_STATE": str(self.tmp / "state.txt"),
            "FAKE_CODEX_RECORD": str(self.record),
        }
        self.patcher = mock.patch.dict(os.environ, self.env)
        self.patcher.start()
        # run.py 会往 stdout 打进度，测试里收进缓冲区，不混进 unittest 的输出。
        self.stdout = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.stdout)
        self.redirect.__enter__()

    def tearDown(self):
        self.redirect.__exit__(None, None, None)
        self.patcher.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def common_args(self, source: str, *extra: str) -> list[str]:
        return [str(FIXTURES / source), "--out", str(self.out), "--codex", str(FAKE), "--glossary-dir", str(GLOSSARY_DIR), *extra]

    def records(self):
        return [json.loads(line) for line in self.record.read_text(encoding="utf-8").splitlines() if line.strip()]


class SecretariatTest(BureauCase):
    def test_dry_run_writes_prompt_and_glossary(self):
        code = secretariat.main(self.common_args("speech_sample.txt", "--dry-run", "--date", "2026-10-16", "--speaker", "丹娜·雷耶斯"))
        self.assertEqual(code, 0)
        prompt = (self.out / "speech_sample.prompt.md").read_text(encoding="utf-8")
        self.assertIn("# 领导讲话体写法规范", prompt)
        self.assertNotIn("name: cadre-speech", prompt, "YAML 头不该进提示词")
        self.assertIn("| Example Dynamics | 机构 | 美国示例动力公司（Example Dynamics） | 示例动力 | 已核 |", prompt)
        self.assertIn("Harbor Research Institute、HRI", prompt)
        self.assertIn("<source>", prompt)
        self.assertIn("we shipped twelve thousand", prompt)
        self.assertIn("（2026年10月16日）", prompt)
        self.assertIn("讲话人：丹娜·雷耶斯", prompt)
        glossary = json.loads((self.out / "speech_sample.glossary.json").read_text(encoding="utf-8"))
        englishes = [m["english"] for m in glossary["matches"]]
        self.assertEqual(englishes[0], "Example Dynamics")
        self.assertIn("Dana Reyes", englishes)
        self.assertIn("President (company)", englishes)
        command = (self.out / "speech_sample.command.txt").read_text(encoding="utf-8")
        self.assertIn("--sandbox read-only", command)
        self.assertIn('web_search="disabled"', command)
        self.assertFalse(self.record.exists(), "dry-run 不该调后端")
        self.assertFalse((self.out / "speech_sample.md").exists())
        printed = self.stdout.getvalue()
        self.assertIn("未调用后端", printed)
        self.assertIn("译名表：Example Dynamics → 美国示例动力公司（Example Dynamics）", printed)

    def test_full_run_produces_markdown_and_json(self):
        os.environ["FAKE_CODEX_RESPONSES"] = str(FIXTURES / "speech_response.json")
        code = secretariat.main(self.common_args("speech_sample.txt", "--date", "2026-10-16"))
        self.assertEqual(code, 0)

        md = (self.out / "speech_sample.md").read_text(encoding="utf-8")
        self.assertTrue(md.startswith("---\n文种: 讲话稿\n局: 秘书局\n"))
        self.assertIn("状态: ok", md)
        self.assertIn("lint 已接入：tools.lint.speech_lint.lint_text", md)
        self.assertNotIn("[P0]", md)
        self.assertIn("# 在年度大会上的讲话", md)
        body = md.split("---\n\n", 1)[1]
        self.assertTrue(body.startswith("# 在年度大会上的讲话\n\n（2026年10月16日）\n\n丹娜·雷耶斯\n\n同志们、朋友们：\n\n"))
        self.assertIn("\n一、去年的成绩来之不易\n", md)
        self.assertIn("谢谢大家！", md)
        self.assertIn("## 复核清单（不随稿件发布）", md)
        self.assertIn("| Harbor Research Institute | 机构 | 美国海港研究院（HRI） | 海港研究院 | 已核 |", md)
        self.assertIn("- Harbor platform → 海港平台（表外产品名", md)
        self.assertIn("| 约3亿美元 | about three hundred million dollars |", md)
        self.assertIn("### 模型说明", md)
        self.assertIn("译名表待核: 0", md)

        data = json.loads((self.out / "speech_sample.json").read_text(encoding="utf-8"))
        self.assertEqual(data["meta"]["状态"], "ok")
        self.assertEqual(data["meta"]["尝试次数"], 1)
        self.assertEqual(data["meta"]["重写次数"], 0)
        self.assertEqual(data["document"]["genre"], "speech")
        self.assertEqual(data["document"]["title"], "在年度大会上的讲话")
        self.assertEqual(data["document"]["paragraphs"][2], "同志们、朋友们：")
        self.assertIn("一、去年的成绩来之不易", data["document"]["paragraphs"])
        self.assertEqual(data["draft"]["genre"], "speech")
        self.assertEqual(data["glossary"]["issues"], [], "讲话样稿里没有待核条目，译名检查应为空")
        self.assertTrue(data["lint"]["available"])
        self.assertEqual(data["lint"]["counts"]["P0"], 0)
        self.assertEqual(data["render"]["byline"], ["（2026年10月16日）", "丹娜·雷耶斯"])
        self.assertEqual(data["render"]["paragraphs"][0], "同志们、朋友们：")
        self.assertTrue((self.out / "speech_sample.redhead.docx").is_file())
        html = (self.out / "speech_sample.article.html").read_text(encoding="utf-8")
        self.assertIn("丹娜·雷耶斯", html)
        self.assertIn("font-weight:bold", html, "一级标题在公众号里加粗")
        self.assertEqual(data["codex"]["attempts"], 1)
        self.assertTrue(Path(data["codex"]["run_dir"]).is_dir())
        self.assertTrue((self.out / "runs").is_dir())

        rec = self.records()
        self.assertEqual(len(rec), 1)
        self.assertIn("# 写法规范", rec[0]["prompt"])
        argv = rec[0]["argv"]
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertTrue(argv[argv.index("--output-schema") + 1].endswith("schema.json"))


class PublicityTest(BureauCase):
    def test_full_run_dateline_and_review(self):
        os.environ["FAKE_CODEX_RESPONSES"] = str(FIXTURES / "newswire_response.json")
        code = publicity.main(self.common_args("newswire_sample.txt", "--date", "2026-10-15", "--city", "旧金山", "--source-name", "美国《每日账簿报》"))
        self.assertEqual(code, 0)
        md = (self.out / "newswire_sample.md").read_text(encoding="utf-8")
        self.assertIn("文种: 新闻稿\n局: 宣传教育局\n", md)
        body = md.split("---\n\n", 1)[1]
        self.assertTrue(body.startswith("# 示例动力公司拟投资25亿美元在内华达州建芯片厂\n\n政见社旧金山10月15日电美国示例动力公司（Example Dynamics）14日宣布"))
        self.assertIn("| 我们押注美国制造业。 | We are betting on American manufacturing, |", md)
        self.assertIn("[P2] glossary-pending：“Northwind Group”", md)
        prompt = (self.out / "newswire_sample.prompt.md").read_text(encoding="utf-8")
        self.assertIn("电头固定写“政见社旧金山10月15日电”", prompt)
        self.assertIn("原文媒体写作“美国《每日账簿报》”", prompt)
        self.assertIn("# 中共和央国企风格新闻稿写法规范", prompt)
        data = json.loads((self.out / "newswire_sample.json").read_text(encoding="utf-8"))
        self.assertTrue(data["document"]["paragraphs"][0].startswith("政见社旧金山10月15日电美国"))
        self.assertEqual(data["draft"]["attributions"][0]["zh"], "据美国《每日账簿报》（The Daily Ledger）报道")
        issues = {(i["rule"], i["english"]) for i in data["glossary"]["issues"]}
        self.assertEqual(issues, {("glossary-pending", "Northwind Group")})

    def test_dry_run_without_city_lets_model_decide(self):
        code = publicity.main(self.common_args("newswire_sample.txt", "--dry-run", "--date", "2026-10-15"))
        self.assertEqual(code, 0)
        prompt = (self.out / "newswire_sample.prompt.md").read_text(encoding="utf-8")
        self.assertIn("电头写“政见社＋发电地城市＋10月15日电”", prompt)
        self.assertIn("| Northwind Group | 机构 | 美国北风集团（Northwind Group） | 北风集团 | 待核 | 待核，照表写并列入复核清单 |", prompt)

    def test_real_media_agency_rejected(self):
        code = publicity.main(self.common_args("newswire_sample.txt", "--dry-run", "--agency", "新华社"))
        self.assertEqual(code, 1)
        self.assertFalse((self.out / "newswire_sample.prompt.md").exists())

    def test_backend_failure_returns_1(self):
        os.environ["FAKE_CODEX_SCRIPT"] = "auth"
        code = publicity.main(self.common_args("newswire_sample.txt", "--date", "2026-10-15"))
        self.assertEqual(code, 1)
        self.assertFalse((self.out / "newswire_sample.md").exists())
        self.assertTrue((self.out / "newswire_sample.prompt.md").exists())

    def test_missing_source_returns_1(self):
        code = publicity.main([str(self.tmp / "nope.txt"), "--out", str(self.out), "--codex", str(FAKE), "--dry-run"])
        self.assertEqual(code, 1)


class RewriteLoopTest(BureauCase):
    """lint 报 P0：带着意见重写一次；仍 P0 标 needs-review，退出码 2。"""

    def options(self, lint_func=None, max_rewrites=1, lint_module=None):
        config = codex_cli.CodexConfig(command=(sys.executable, str(FAKE)), sleep=lambda s: None, timeout=30.0)
        return pipeline.RunOptions(
            source_path=FIXTURES / "speech_sample.txt",
            out_dir=self.out,
            date=__import__("datetime").date(2026, 10, 16),
            codex=config,
            lint_func=lint_func,
            lint_module=lint_module,
            max_rewrites=max_rewrites,
            glossary=Glossary.load(GLOSSARY_DIR),
        )

    def test_p0_triggers_one_rewrite_then_passes(self):
        os.environ["FAKE_CODEX_RESPONSES"] = str(FIXTURES / "speech_response.json")
        calls = []

        def stub(text, genre=None):
            calls.append(genre)
            if len(calls) == 1:
                return [{"level": "P0", "rule": "S21", "message": "占位符"}, {"level": "P2", "rule": "S23", "message": "叹号多"}]
            return []

        result = pipeline.run_bureau(secretariat.SPEC, self.options(lint_func=stub))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.rewrites, 1)
        self.assertEqual(calls, ["speech", "speech"])
        self.assertEqual(len(self.records()), 2)
        second_prompt = self.records()[1]["prompt"]
        self.assertIn("## 上一稿检查意见", second_prompt)
        self.assertIn("- [P0] S21：占位符", second_prompt)
        self.assertIn('"title": "在年度大会上的讲话"', second_prompt)
        self.assertTrue((self.out / "speech_sample.rewrite1.prompt.md").is_file())
        self.assertEqual(len(result.run_dirs), 2)
        self.assertTrue(result.run_dirs[1].name.split("-", 2)[2].startswith("speech_sample-rewrite1"))
        meta = json.loads((self.out / "speech_sample.json").read_text(encoding="utf-8"))["meta"]
        self.assertEqual(meta["重写次数"], 1)
        self.assertEqual(meta["lint计数"], {"P0": 0, "P1": 0, "P2": 0})

    def test_persistent_p0_marks_needs_review(self):
        os.environ["FAKE_CODEX_RESPONSES"] = str(FIXTURES / "speech_response.json")
        stub = lambda text: [lint_hook.LintIssue("P0", "S18", "禁用指称")]  # noqa: E731
        result = pipeline.run_bureau(secretariat.SPEC, self.options(lint_func=stub))
        self.assertEqual(result.status, "needs-review")
        self.assertEqual(result.rewrites, 1)
        self.assertIn("交人工", result.message)
        md = (self.out / "speech_sample.md").read_text(encoding="utf-8")
        self.assertIn("状态: needs-review", md)
        self.assertIn("- [P0] S18：禁用指称", md)

    def test_max_rewrites_zero(self):
        os.environ["FAKE_CODEX_RESPONSES"] = str(FIXTURES / "speech_response.json")
        stub = lambda text: [{"level": "P0", "message": "x"}]  # noqa: E731
        result = pipeline.run_bureau(secretariat.SPEC, self.options(lint_func=stub, max_rewrites=0))
        self.assertEqual(result.status, "needs-review")
        self.assertEqual(result.rewrites, 0)
        self.assertEqual(len(self.records()), 1)

    def test_cli_lint_module_and_exit_code_2(self):
        os.environ["FAKE_CODEX_RESPONSES"] = str(FIXTURES / "speech_response.json")
        pkg = self.tmp / "zuzhibu_cli_lint"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "always_p0.py").write_text(textwrap.dedent("""
            def lint_text(text):
                return [{"level": "P0", "rule": "T", "message": "总是不过"}]
            """), encoding="utf-8")
        sys.path.insert(0, str(self.tmp))
        try:
            code = secretariat.main(self.common_args("speech_sample.txt", "--date", "2026-10-16", "--lint-module", "zuzhibu_cli_lint.always_p0"))
        finally:
            sys.path.remove(str(self.tmp))
            sys.modules.pop("zuzhibu_cli_lint.always_p0", None)
            sys.modules.pop("zuzhibu_cli_lint", None)
        self.assertEqual(code, 2)
        self.assertEqual(len(self.records()), 2)

    def test_no_lint_flag(self):
        os.environ["FAKE_CODEX_RESPONSES"] = str(FIXTURES / "speech_response.json")
        code = secretariat.main(self.common_args("speech_sample.txt", "--date", "2026-10-16", "--no-lint"))
        self.assertEqual(code, 0)
        md = (self.out / "speech_sample.md").read_text(encoding="utf-8")
        self.assertIn("lint 已按 --no-lint 跳过", md)


class HelpersTest(unittest.TestCase):
    def test_strip_frontmatter_and_word_count(self):
        text = "---\nname: x\n---\n# 正文\n"
        self.assertEqual(pipeline.strip_frontmatter(text), "# 正文\n")
        self.assertEqual(pipeline.strip_frontmatter("# 没头\n"), "# 没头\n")
        self.assertEqual(pipeline.count_words("Hello, world! It's 2026 — ok."), 5)

    def test_draft_body_text_and_markdown(self):
        body = pipeline.DraftBody(title="标题", subtitle="——副题", paragraphs=["同志们：", "", "第一段"], signoff="落款")
        self.assertEqual(body.plain_text(), "标题\n\n——副题\n\n同志们：\n\n第一段\n\n落款\n")
        self.assertEqual(body.markdown(), "# 标题\n\n## ——副题\n\n同志们：\n\n第一段\n\n落款\n")

    def test_both_specs_point_to_existing_files(self):
        for spec in (secretariat.SPEC, publicity.SPEC):
            self.assertTrue(spec.skill_path.is_file(), spec.skill_path)
            self.assertTrue(spec.schema_path.is_file(), spec.schema_path)
            self.assertEqual(spec.default_lint_module, lint_hook.DEFAULT_MODULES[spec.genre])


if __name__ == "__main__":
    unittest.main()
