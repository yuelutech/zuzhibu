"""codex_cli 的测试：全部用 tests/fakes/fake_codex.py 冒充 codex，不联网。"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from shared.backends import codex_cli

FAKE = Path(__file__).resolve().parent / "fakes" / "fake_codex.py"
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"title": {"type": "string"}, "paragraphs": {"type": "array", "items": {"type": "string"}}},
    "required": ["title", "paragraphs"],
}


class FakeCodexCase(unittest.TestCase):
    """每个用例一个临时目录，假 codex 的状态和记录都放在里面。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="zuzhibu-codex-"))
        self.workdir = self.tmp / "runs"
        self.record = self.tmp / "record.jsonl"
        self.state = self.tmp / "state.txt"
        self.sleeps: list[float] = []

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def config(self, script="ok", **kwargs):
        env = {
            "FAKE_CODEX_SCRIPT": script,
            "FAKE_CODEX_STATE": str(self.state),
            "FAKE_CODEX_RECORD": str(self.record),
        }
        env.update(kwargs.pop("env", {}))
        defaults = dict(command=(sys.executable, str(FAKE)), env=env, sleep=self.sleeps.append, timeout=30.0)
        defaults.update(kwargs)
        return codex_cli.CodexConfig(**defaults)

    def records(self):
        if not self.record.exists():
            return []
        return [json.loads(line) for line in self.record.read_text(encoding="utf-8").splitlines() if line.strip()]

    # ------------------------------------------------------------------ 正常路径

    def test_success_writes_process_records_and_uses_expected_flags(self):
        result = codex_cli.run("请输出 JSON", SCHEMA, self.config(), self.workdir, label="demo")
        self.assertEqual(result.data, {"title": "示例", "paragraphs": []})
        self.assertEqual(result.attempts_used, 1)
        self.assertEqual(result.thread_id, "fake-thread-1")
        self.assertEqual(result.codex_version, "codex-cli 0.0.0-fake")
        self.assertIsNotNone(result.usage)
        self.assertEqual(result.attempts[0].outcome, "ok")

        run_dir = result.run_dir
        self.assertTrue(run_dir.is_dir())
        self.assertTrue(run_dir.name.endswith(tuple("0123456789abcdef")) and "-demo-" in run_dir.name)
        for name in ("prompt.md", "schema.json", "attempt-1.cmd.txt", "attempt-1.stdout.jsonl",
                     "attempt-1.stderr.txt", "attempt-1.meta.json", "last_message.txt", "result.json", "run.json"):
            self.assertTrue((run_dir / name).is_file(), name)
        self.assertTrue((run_dir / "sandbox").is_dir())
        self.assertEqual((run_dir / "prompt.md").read_text(encoding="utf-8"), "请输出 JSON")
        summary = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["outcome"], "ok")
        self.assertEqual(len(summary["attempts"]), 1)
        self.assertEqual(summary["config"]["sandbox"], "read-only")

        argv = self.records()[0]["argv"]
        self.assertEqual(argv[0], "exec")
        self.assertIn("--json", argv)
        self.assertIn("--skip-git-repo-check", argv)
        self.assertIn("--ephemeral", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        self.assertEqual(argv[argv.index("--color") + 1], "never")
        self.assertEqual(argv[argv.index("-C") + 1], str(run_dir / "sandbox"))
        self.assertEqual(argv[argv.index("--output-schema") + 1], str(run_dir / "schema.json"))
        self.assertEqual(argv[argv.index("-o") + 1], str(run_dir / "last_message.txt"))
        self.assertIn('web_search="disabled"', self.records()[0]["config"])
        self.assertNotIn("-m", argv)

    def test_prompt_goes_through_stdin_not_argv(self):
        prompt = "第一行\n第二行 with 'quotes' and \"double\" 和中文\n"
        codex_cli.run(prompt, SCHEMA, self.config(), self.workdir)
        rec = self.records()[0]
        self.assertEqual(rec["prompt"], prompt)
        self.assertNotIn(prompt, " ".join(rec["argv"]))

    def test_model_and_overrides_are_passed(self):
        cfg = self.config(model="gpt-test", config_overrides=('model_reasoning_effort="high"',), extra_args=("--full-auto",))
        codex_cli.run("x", SCHEMA, cfg, self.workdir)
        rec = self.records()[0]
        self.assertEqual(rec["argv"][rec["argv"].index("-m") + 1], "gpt-test")
        self.assertIn('model_reasoning_effort="high"', rec["config"])
        self.assertIn("--full-auto", rec["argv"])

    def test_without_schema_only_requires_json(self):
        result = codex_cli.run("x", None, self.config(), self.workdir)
        self.assertEqual(result.data, "示例")
        self.assertFalse((result.run_dir / "schema.json").exists())

    def test_plain_output_without_json_events_still_works(self):
        result = codex_cli.run("x", SCHEMA, self.config(script="plain"), self.workdir)
        self.assertEqual(result.data["title"], "示例")
        self.assertIsNone(result.thread_id)

    def test_canned_response_file(self):
        canned = self.tmp / "canned.json"
        canned.write_text(json.dumps({"title": "标题", "paragraphs": ["一", "二"]}, ensure_ascii=False), encoding="utf-8")
        result = codex_cli.run("x", SCHEMA, self.config(env={"FAKE_CODEX_RESPONSES": str(canned)}), self.workdir)
        self.assertEqual(result.data["paragraphs"], ["一", "二"])

    # ------------------------------------------------------------------ 重试

    def test_retry_after_nonzero_exit_with_backoff(self):
        result = codex_cli.run("x", SCHEMA, self.config(script="exit,ok", retries=2), self.workdir)
        self.assertEqual(result.attempts_used, 2)
        self.assertEqual(result.attempts[0].outcome, "exit")
        self.assertEqual(result.attempts[0].returncode, 1)
        self.assertIn("simulated failure", result.attempts[0].error)
        self.assertEqual(result.attempts[1].outcome, "ok")
        self.assertEqual(self.sleeps, [2.0])
        self.assertTrue((result.run_dir / "attempt-2.meta.json").is_file())

    def test_retry_after_bad_json_adds_repair_hint(self):
        result = codex_cli.run("提示词", SCHEMA, self.config(script="bad_json,ok"), self.workdir)
        self.assertEqual(result.attempts_used, 2)
        self.assertEqual(result.attempts[0].outcome, "bad_json")
        recs = self.records()
        self.assertEqual(recs[0]["prompt"], "提示词")
        self.assertTrue(recs[1]["prompt"].startswith("提示词"))
        self.assertIn("只输出一个符合 schema 的 JSON 对象", recs[1]["prompt"])

    def test_retry_after_schema_mismatch(self):
        result = codex_cli.run("x", SCHEMA, self.config(script="schema_bad,ok"), self.workdir)
        self.assertEqual(result.attempts[0].outcome, "schema")
        self.assertTrue(result.attempts[0].schema_errors)
        self.assertEqual(result.attempts[1].outcome, "ok")

    def test_retry_after_empty_reply(self):
        result = codex_cli.run("x", SCHEMA, self.config(script="empty,ok"), self.workdir)
        self.assertEqual(result.attempts[0].outcome, "empty")
        self.assertEqual(result.attempts_used, 2)

    def test_exhausted_retries_raise_output_error(self):
        with self.assertRaises(codex_cli.CodexOutputError) as ctx:
            codex_cli.run("x", SCHEMA, self.config(script="bad_json", retries=1), self.workdir)
        self.assertEqual(len(ctx.exception.attempts), 2)
        summary = json.loads((ctx.exception.run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["outcome"], "failed")
        self.assertEqual(self.sleeps, [2.0])

    def test_backoff_doubles(self):
        with self.assertRaises(codex_cli.CodexExitError):
            codex_cli.run("x", SCHEMA, self.config(script="exit", retries=3, backoff=1.5), self.workdir)
        self.assertEqual(self.sleeps, [1.5, 3.0, 6.0])

    # ------------------------------------------------------------------ 超时与不可重试

    def test_timeout_kills_and_retries_then_raises(self):
        cfg = self.config(script="sleep", env={"FAKE_CODEX_SLEEP": "5"}, timeout=0.5, retries=1)
        with self.assertRaises(codex_cli.CodexTimeout) as ctx:
            codex_cli.run("x", SCHEMA, cfg, self.workdir)
        attempts = ctx.exception.attempts
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(a.timed_out for a in attempts))
        self.assertTrue(all(a.duration < 4 for a in attempts))
        self.assertTrue(all(a.outcome == "timeout" for a in attempts))

    def test_auth_failure_is_not_retried(self):
        with self.assertRaises(codex_cli.CodexAuthError) as ctx:
            codex_cli.run("x", SCHEMA, self.config(script="auth", retries=3), self.workdir)
        self.assertEqual(len(ctx.exception.attempts), 1)
        self.assertEqual(self.sleeps, [])

    def test_missing_executable(self):
        cfg = codex_cli.CodexConfig(command=("definitely-not-codex-xyz",), probe_version=False)
        with self.assertRaises(codex_cli.CodexNotFound) as ctx:
            codex_cli.run("x", SCHEMA, cfg, self.workdir)
        self.assertTrue((ctx.exception.run_dir / "run.json").is_file())

    def test_py_path_runs_with_current_interpreter(self):
        cfg = self.config(command=(str(FAKE),))
        result = codex_cli.run("x", SCHEMA, cfg, self.workdir)
        self.assertEqual(result.attempts[0].command[0], sys.executable)

    def test_relative_py_path_is_absolutized(self):
        # 子进程在沙箱目录里跑，相对路径的 --codex 必须先转成绝对路径。
        relative = os.path.relpath(FAKE, os.getcwd())
        self.assertFalse(os.path.isabs(relative))
        cfg = self.config(command=(relative,))
        result = codex_cli.run("x", SCHEMA, cfg, self.workdir)
        self.assertEqual(result.attempts[0].command[0], sys.executable)
        self.assertTrue(os.path.isabs(result.attempts[0].command[1]))
        self.assertEqual(Path(result.attempts[0].command[1]).resolve(), FAKE)
        self.assertEqual(result.attempts[0].outcome, "ok")

    def test_empty_prompt_rejected(self):
        with self.assertRaises(ValueError):
            codex_cli.run("   ", SCHEMA, self.config(), self.workdir)


class PureFunctionTest(unittest.TestCase):
    def test_build_command_variants(self):
        cfg = codex_cli.CodexConfig(command=("codex",), model="m1", web_search=None, ephemeral=False, skip_git_repo_check=False,
                                    disabled_features=())
        cmd = codex_cli.build_command(cfg, cwd=Path("/w"), schema_path=None, last_message_path=Path("/w/last.txt"))
        self.assertEqual(cmd[:3], ["codex", "exec", "--json"])
        self.assertNotIn("--ephemeral", cmd)
        self.assertNotIn("--skip-git-repo-check", cmd)
        self.assertNotIn("--output-schema", cmd)
        self.assertNotIn("-c", cmd)
        self.assertEqual(cmd[cmd.index("-m") + 1], "m1")
        self.assertEqual(cmd[-2:], ["-o", str(Path("/w/last.txt"))])

    def test_config_validation(self):
        with self.assertRaises(ValueError):
            codex_cli.CodexConfig(sandbox="yolo")
        with self.assertRaises(ValueError):
            codex_cli.CodexConfig(web_search="sometimes")
        with self.assertRaises(ValueError):
            codex_cli.CodexConfig(retries=-1)
        with self.assertRaises(ValueError):
            codex_cli.CodexConfig(command=())

    def test_extract_last_message_modern_format(self):
        lines = [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps({"type": "turn.started"}),
            json.dumps({"type": "item.completed", "item": {"id": "a", "type": "reasoning", "text": "想想"}}),
            json.dumps({"type": "item.completed", "item": {"id": "b", "type": "agent_message", "text": "第一条"}}),
            json.dumps({"type": "item.completed", "item": {"id": "c", "type": "agent_message", "text": '{"ok": 1}'}}),
            "not json at all",
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 4}}),
        ]
        last, info = codex_cli.extract_last_message("\n".join(lines))
        self.assertEqual(last, '{"ok": 1}')
        self.assertEqual(info["thread_id"], "t1")
        self.assertEqual(info["usage"]["output_tokens"], 4)
        self.assertEqual(info["non_json_lines"], 1)
        self.assertEqual(info["errors"], [])

    def test_extract_last_message_legacy_and_errors(self):
        lines = [
            json.dumps({"id": "0", "msg": {"type": "agent_message", "message": "旧格式"}}),
            json.dumps({"id": "1", "msg": {"type": "task_complete", "last_agent_message": "最终"}}),
            json.dumps({"type": "turn.failed", "error": {"message": "boom"}}),
            json.dumps({"type": "error", "message": "again"}),
        ]
        last, info = codex_cli.extract_last_message("\n".join(lines))
        self.assertEqual(last, "最终")
        self.assertEqual(info["errors"], ["boom", "again"])

    def test_parse_json_message_tolerates_fences_and_prose(self):
        self.assertEqual(codex_cli.parse_json_message('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(codex_cli.parse_json_message('好的，结果如下：{"a": [1, 2]} 以上。'), {"a": [1, 2]})
        self.assertEqual(codex_cli.parse_json_message("[1, 2]"), [1, 2])
        with self.assertRaises(ValueError):
            codex_cli.parse_json_message("没有任何对象")
        with self.assertRaises(ValueError):
            codex_cli.parse_json_message("   ")

    def test_resolve_command_handles_missing(self):
        self.assertIsNone(codex_cli.resolve_command(("definitely-not-codex-xyz",)))
        self.assertIsNone(codex_cli.resolve_command(()))
        resolved = codex_cli.resolve_command((sys.executable, "--x"))
        self.assertTrue(resolved and resolved[0].endswith(os.path.basename(sys.executable)))

    def test_ignore_user_config_and_reasoning_effort_flags(self):
        cfg = codex_cli.CodexConfig(command=("codex",), model="m1", reasoning_effort="max")
        cmd = codex_cli.build_command(cfg, cwd=Path("/w"), schema_path=None, last_message_path=Path("/w/last.txt"))
        self.assertIn("--ignore-user-config", cmd)
        self.assertIn('model_reasoning_effort="max"', cmd)
        # 默认关掉 apps 连接器（含联网搜索）和 shell 等工具
        for feature in ("apps", "shell_tool", "unified_exec", "image_generation"):
            self.assertIn(f"features.{feature}=false", cmd)
        cfg = codex_cli.CodexConfig(command=("codex",), ignore_user_config=False)
        cmd = codex_cli.build_command(cfg, cwd=Path("/w"), schema_path=None, last_message_path=Path("/w/last.txt"))
        self.assertNotIn("--ignore-user-config", cmd)
        self.assertFalse(any("model_reasoning_effort" in part for part in cmd))

    def test_user_defaults_reads_only_model_and_effort(self):
        with tempfile.TemporaryDirectory() as home:
            Path(home, "config.toml").write_text(
                'model = "m-local"\nmodel_reasoning_effort = "high"\napproval_policy = "never"\n[mcp_servers.mail]\ncommand = "x"\n',
                encoding="utf-8",
            )
            self.assertEqual(codex_cli.user_defaults(home), {"model": "m-local", "model_reasoning_effort": "high"})
            Path(home, "config.toml").write_text("not = [valid toml", encoding="utf-8")
            self.assertEqual(codex_cli.user_defaults(home), {})
        with tempfile.TemporaryDirectory() as empty:
            self.assertEqual(codex_cli.user_defaults(empty), {})

    def test_find_desktop_codex_picks_newest_hash_dir(self):
        with tempfile.TemporaryDirectory() as appdata:
            self.assertIsNone(codex_cli.find_desktop_codex(appdata))
            old = Path(appdata, "OpenAI", "Codex", "bin", "aaa", "codex.exe")
            new = Path(appdata, "OpenAI", "Codex", "bin", "bbb", "codex.exe")
            for path, stamp in ((old, 1_700_000_000), (new, 1_800_000_000)):
                path.parent.mkdir(parents=True)
                path.write_bytes(b"")
                os.utime(path, (stamp, stamp))
            self.assertEqual(codex_cli.find_desktop_codex(appdata), new)


if __name__ == "__main__":
    unittest.main()
