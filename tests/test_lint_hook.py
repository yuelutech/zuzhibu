"""lint_hook 的测试：模块不存在、存在但没函数、正常返回、各种返回形态、运行出错。"""

import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from shared.backends import lint_hook


class ResolveTest(unittest.TestCase):
    def test_missing_module_is_reported_not_raised(self):
        fn, note = lint_hook.resolve("tools.lint.definitely_missing_lint")
        self.assertIsNone(fn)
        self.assertIn("未接入", note)
        report = lint_hook.run_lint("正文", genre="speech", module_name="tools.lint.definitely_missing_lint")
        self.assertFalse(report.available)
        self.assertEqual(report.issues, [])
        self.assertFalse(report.has_p0)
        self.assertEqual(report.to_dict()["counts"], {"P0": 0, "P1": 0, "P2": 0})

    def test_default_modules_are_present(self):
        for genre in ("speech", "newswire"):
            report = lint_hook.run_lint("正文", genre=genre)
            self.assertTrue(report.available, genre)
            self.assertEqual(report.module, lint_hook.DEFAULT_MODULES[genre])
            self.assertTrue(report.has_p0, "只有“正文”两个字，称谓或电头的 P0 应报出")

    def test_context_passed_only_to_declared_keywords(self):
        seen = {}

        def wants_source(text, source=None):
            seen["source"] = source
            return []

        def wants_nothing(text):
            return []

        context = {"source": "EN", "document": {"title": "t"}, "extra": {}, "glossary_dir": None}
        self.assertTrue(lint_hook.run_lint("x", genre="speech", func=wants_source, context=context).available)
        self.assertEqual(seen["source"], "EN")
        self.assertTrue(lint_hook.run_lint("x", genre="speech", func=wants_nothing, context=context).available)

    def test_unknown_genre_without_module(self):
        report = lint_hook.run_lint("正文", genre="poem")
        self.assertFalse(report.available)
        self.assertIn("没有默认模块", report.note)


class TempModuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zuzhibu-lint-")
        pkg = Path(self.tmp) / "zuzhibu_fakelint"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        (pkg / "good.py").write_text(
            textwrap.dedent(
                '''
                def lint_text(text, genre=None):
                    issues = []
                    if "坏词" in text:
                        issues.append({"level": "P0", "rule": "S17", "message": "出现坏词"})
                    if genre == "speech" and "同志们" not in text:
                        issues.append({"level": "P1", "rule": "S01", "message": "缺称谓"})
                    issues.append({"severity": "info", "code": "S99", "msg": "建议"})
                    return issues
                '''
            ),
            encoding="utf-8",
        )
        (pkg / "nofunc.py").write_text("X = 1\n", encoding="utf-8")
        (pkg / "broken.py").write_text("raise RuntimeError('import boom')\n", encoding="utf-8")
        (pkg / "crashes.py").write_text("def lint(text):\n    raise ValueError('run boom')\n", encoding="utf-8")
        (pkg / "positional.py").write_text("def check(text):\n    return (False, ['P0 占位符', '[P2] 太长'])\n", encoding="utf-8")
        sys.path.insert(0, self.tmp)

    def tearDown(self):
        import shutil

        sys.path.remove(self.tmp)
        for name in list(sys.modules):
            if name.startswith("zuzhibu_fakelint"):
                del sys.modules[name]
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_good_module_with_genre_kwarg(self):
        report = lint_hook.run_lint("有坏词的正文", genre="speech", module_name="zuzhibu_fakelint.good")
        self.assertTrue(report.available)
        self.assertEqual(report.module, "zuzhibu_fakelint.good")
        self.assertIn("已接入", report.note)
        self.assertEqual([i.level for i in report.issues], ["P0", "P1", "P2"])
        self.assertEqual(report.issues[0].rule, "S17")
        self.assertEqual(report.issues[2].message, "建议")
        self.assertTrue(report.has_p0)
        self.assertEqual(report.count("P2"), 1)

    def test_module_without_function(self):
        report = lint_hook.run_lint("正文", genre="speech", module_name="zuzhibu_fakelint.nofunc")
        self.assertFalse(report.available)
        self.assertIn("没有", report.note)

    def test_broken_import_does_not_raise(self):
        report = lint_hook.run_lint("正文", genre="speech", module_name="zuzhibu_fakelint.broken")
        self.assertFalse(report.available)
        self.assertIn("导入失败", report.note)

    def test_runtime_error_does_not_raise(self):
        report = lint_hook.run_lint("正文", genre="speech", module_name="zuzhibu_fakelint.crashes")
        self.assertFalse(report.available)
        self.assertIn("运行出错", report.note)

    def test_tuple_and_string_issues(self):
        report = lint_hook.run_lint("正文", genre="newswire", module_name="zuzhibu_fakelint.positional")
        self.assertTrue(report.available)
        self.assertEqual([(i.level, i.message) for i in report.issues], [("P0", "占位符"), ("P2", "太长")])

    def test_injected_function(self):
        def stub(text):
            return [lint_hook.LintIssue("P1", "X", "注入")]

        report = lint_hook.run_lint("正文", genre="speech", func=stub)
        self.assertTrue(report.available)
        self.assertIn("直接注入", report.note)
        self.assertEqual(report.issues[0].message, "注入")


class NormalizeTest(unittest.TestCase):
    def test_shapes(self):
        class Obj:
            level = 0
            rule = "R"
            message = "对象"

        issues = lint_hook.normalize_issues([Obj(), {"level": "warning", "message": "字典"}, "p1 字符串", LintReportLike()])
        self.assertEqual([i.level for i in issues[:3]], ["P0", "P1", "P1"])
        self.assertEqual(issues[2].message, "字符串")
        self.assertEqual(lint_hook.normalize_issues(None), [])
        self.assertEqual(lint_hook.normalize_issues({"issues": [{"level": 2, "message": "x"}]})[0].level, "P2")
        self.assertEqual(lint_hook.normalize_issues("单个字符串")[0].level, "P1")
        self.assertEqual(lint_hook.normalize_issues([{"level": "P7", "message": "x"}])[0].level, "P2")


class LintReportLike:
    level = "P2"
    rule = "L"
    message = "类"


if __name__ == "__main__":
    unittest.main()
