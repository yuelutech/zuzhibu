"""用户自带 AI 接口后端：两类接口的请求形态、联网动作统计、续接与重答、出错的分类、密钥不进记录。不联网，用假接口。"""

import json
import tempfile
import unittest
from pathlib import Path

from shared.backends import ai_api

from tests.fakes.fake_ai_api import (FAKE_KEY, FakeApi, anthropic_message, fetch_blocks, openai_response, search_blocks,
                                     text_block)

SCHEMA = {"type": "object", "additionalProperties": False, "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


def config(provider, handler, **kw):
    return ai_api.ApiConfig(provider=provider, api_key=FAKE_KEY, transport=FakeApi(handler), sleep=lambda s: None, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def assertNoKey(self):
        for path in self.work.rglob("*"):
            if path.is_file():
                self.assertNotIn(FAKE_KEY, path.read_text(encoding="utf-8", errors="replace"), path)


class SettingsTest(unittest.TestCase):
    def test_capability(self):
        self.assertTrue(ai_api.capability({"provider": "openai", "api_key": "k" * 20})[0])
        ok, why = ai_api.capability({"provider": "other", "api_key": "k" * 20})
        self.assertFalse(ok)
        self.assertIn("没有联网检索工具", why)
        self.assertEqual(ai_api.capability({"provider": "anthropic"}), (False, "还没有填密钥"))
        self.assertFalse(ai_api.capability({"provider": "openai", "api_key": "k" * 20, "base_url": "http://relay.example.com/v1"})[0],
                         "密钥不走明文 http")

    def test_config_from_settings(self):
        cfg = ai_api.config_from_settings({"provider": "anthropic", "api_key": " key-123456789 ", "base_url": "https://relay.example.com"})
        self.assertEqual(cfg.base_url, "https://relay.example.com/v1", "只写了域名的补 /v1")
        self.assertEqual(cfg.model, "claude-opus-5-5")
        self.assertEqual(cfg.api_key, "key-123456789")
        self.assertNotIn("key-123456789", repr(cfg))
        self.assertNotIn("key-123456789", json.dumps(cfg.describe(), ensure_ascii=False))
        with self.assertRaises(ai_api.ApiNoWebSearch):
            ai_api.config_from_settings({"provider": "other", "api_key": "k" * 20})
        with self.assertRaises(ai_api.ApiAuthError):
            ai_api.config_from_settings({"provider": "openai"})

    def test_redact(self):
        self.assertEqual(ai_api.redact(f"bad key {FAKE_KEY}!", FAKE_KEY), "bad key （密钥已隐去）!")


class OpenAITest(Base):
    def test_background_session_with_strict_schema(self):
        polls = []

        def handler(call):
            if call["method"] == "POST":
                return {"id": "resp_1", "status": "queued", "output": []}
            polls.append(call["url"])
            if len(polls) < 2:
                return {"id": "resp_1", "status": "in_progress", "output": []}
            return openai_response('{"answer": "ok"}', searches=3, opened=("https://example.gov/a", "https://example.gov/a"))

        cfg = config("openai", handler)
        result = ai_api.run("找一找", SCHEMA, cfg, self.work, label="research")
        self.assertEqual(result.data, {"answer": "ok"})
        self.assertEqual(result.stats["searches"], 5, "检索和打开网页都算联网动作")
        self.assertEqual(result.stats["opened"], ["https://example.gov/a"])
        calls = cfg.transport.calls
        body = calls[0]["body"]
        self.assertEqual(calls[0]["url"], "https://api.openai.com/v1/responses")
        self.assertEqual(calls[0]["headers"]["Authorization"], "Bearer " + FAKE_KEY)
        self.assertEqual(body["tools"], [{"type": "web_search"}])
        self.assertEqual(body["text"]["format"]["type"], "json_schema")
        self.assertTrue(body["text"]["format"]["strict"])
        self.assertTrue(body["background"])
        self.assertEqual(polls, ["https://api.openai.com/v1/responses/resp_1"] * 2)
        self.assertTrue((result.run_dir / "result.json").exists())
        self.assertNoKey()

    def test_endpoint_without_background_mode(self):
        def handler(call):
            if call["body"].get("background"):
                return 400, {"error": {"message": "Unsupported parameter: 'background'", "param": "background"}}
            return openai_response('{"answer": "sync"}')

        cfg = config("openai", handler)
        self.assertEqual(ai_api.run("p", SCHEMA, cfg, self.work).data, {"answer": "sync"})
        self.assertNotIn("store", cfg.transport.calls[1]["body"])

    def test_rejected_web_search_tool(self):
        def handler(call):
            return 400, {"error": {"message": "Tool 'web_search' is not supported with this model.", "param": "tools"}}

        with self.assertRaises(ai_api.ApiNoWebSearch):
            ai_api.run("p", SCHEMA, config("openai", handler), self.work)

    def test_bad_key_is_not_retried_and_not_echoed(self):
        def handler(call):
            return 401, {"error": {"message": f"Incorrect API key provided: {FAKE_KEY}"}}

        cfg = config("openai", handler)
        with self.assertRaises(ai_api.ApiAuthError) as cm:
            ai_api.run("p", SCHEMA, cfg, self.work)
        self.assertNotIn(FAKE_KEY, str(cm.exception))
        self.assertEqual(len(cfg.transport.calls), 1)
        self.assertNoKey()

    def test_quota_and_rate_limits(self):
        cfg = config("openai", lambda call: (429, {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}}))
        with self.assertRaises(ai_api.ApiAuthError):
            ai_api.run("p", SCHEMA, cfg, self.work)
        answers = iter([(429, {"error": {"message": "Rate limit reached"}}), (503, {"error": {"message": "busy"}}),
                        openai_response('{"answer": "later"}')])
        cfg = config("openai", lambda call: next(answers), background=False)
        self.assertEqual(ai_api.run("p", SCHEMA, cfg, self.work).data, {"answer": "later"})

    def test_network_errors_are_retried_then_reported(self):
        def handler(call):
            raise ConnectionResetError("reset by peer")

        cfg = config("openai", handler, retries=2)
        with self.assertRaises(ai_api.ApiError) as cm:
            ai_api.run("p", SCHEMA, cfg, self.work)
        self.assertEqual(len(cfg.transport.calls), 3)
        self.assertIn("连不上接口", str(cm.exception))

    def test_truncated_output_gets_a_bigger_budget_once(self):
        def handler(call):
            if call["body"]["max_output_tokens"] < 2000:
                return openai_response("", status="incomplete", reason="max_output_tokens")
            return openai_response('{"answer": "long"}')

        cfg = config("openai", handler, background=False, max_output_tokens=1000)
        self.assertEqual(ai_api.run("p", SCHEMA, cfg, self.work).data, {"answer": "long"})

    def test_deadline_cancels_background_response(self):
        now = [0.0]

        def handler(call):
            now[0] += 100
            return {"id": "resp_9", "status": "in_progress", "output": []}

        cfg = config("openai", handler, deadline=250)
        cfg.clock = lambda: now[0]
        with self.assertRaises(ai_api.ApiTimeout):
            ai_api.run("p", SCHEMA, cfg, self.work)
        self.assertTrue(any(c["url"].endswith("/responses/resp_9/cancel") for c in cfg.transport.calls))


class AnthropicTest(Base):
    def test_pause_turn_is_continued_with_content_unchanged(self):
        replies = iter([
            anthropic_message(search_blocks(1) + search_blocks(2), stop="pause_turn"),
            anthropic_message(fetch_blocks(3, "https://example.gov/a") + fetch_blocks(4, "https://example.gov/b", error="url_not_accessible")
                              + [text_block('{"answer": '), text_block('"done"}')]),
        ])
        cfg = config("anthropic", lambda call: next(replies))
        result = ai_api.run("核对一下", SCHEMA, cfg, self.work, label="verify")
        self.assertEqual(result.data, {"answer": "done"}, "被引用拆开的文字块照原样接上")
        self.assertEqual(result.stats["searches"], 3, "打不开的网页不算")
        self.assertEqual(result.stats["opened"], ["https://example.gov/a"])
        self.assertTrue(any("url_not_accessible" in e for e in result.stats["errors"]))
        first, second = cfg.transport.bodies("/messages")
        self.assertEqual(cfg.transport.calls[0]["headers"]["x-api-key"], FAKE_KEY)
        self.assertEqual([t["type"] for t in first["tools"]], list(ai_api.ANTHROPIC_TOOLS))
        self.assertIn("JSON Schema", first["messages"][0]["content"], "没有原生 JSON 输出：schema 附在提示词里")
        self.assertNotIn("output_config", first)
        self.assertEqual(second["messages"][1], {"role": "assistant", "content": search_blocks(1) + search_blocks(2)})
        self.assertEqual(result.stats["input_tokens"], 110)
        self.assertNoKey()

    def test_bad_format_gets_one_fix_turn_in_the_same_session(self):
        replies = iter([anthropic_message(search_blocks(1) + [text_block("我查到了：答案是 ok。")]),
                        anthropic_message([text_block('{"answer": "ok"}')])])
        cfg = config("anthropic", lambda call: next(replies))
        result = ai_api.run("p", SCHEMA, cfg, self.work)
        self.assertEqual(result.data, {"answer": "ok"})
        fix = cfg.transport.bodies("/messages")[1]["messages"]
        self.assertEqual([m["role"] for m in fix], ["user", "assistant", "user"])
        self.assertIn("不是符合 schema 的 JSON", fix[2]["content"])
        self.assertEqual(result.stats["searches"], 1, "重答前的检索照算")

    def test_still_bad_after_fix_fails(self):
        cfg = config("anthropic", lambda call: anthropic_message([text_block('{"other": 1}')]))
        with self.assertRaises(ai_api.ApiOutputError):
            ai_api.run("p", SCHEMA, cfg, self.work)

    def test_truncation_and_refusal(self):
        for stop in ("max_tokens", "refusal"):
            cfg = config("anthropic", lambda call: anthropic_message([text_block('{"answer"')], stop=stop))
            with self.assertRaises(ai_api.ApiOutputError, msg=stop):
                ai_api.run("p", SCHEMA, cfg, self.work)

    def test_overloaded_is_retried(self):
        answers = iter([(529, {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}),
                        anthropic_message(search_blocks(1) + [text_block('{"answer": "x"}')])])
        cfg = config("anthropic", lambda call: next(answers))
        self.assertEqual(ai_api.run("p", SCHEMA, cfg, self.work).data, {"answer": "x"})

    def test_low_credit_stops(self):
        cfg = config("anthropic", lambda call: (400, {"type": "error", "error": {
            "type": "invalid_request_error", "message": "Your credit balance is too low to access the API."}}))
        with self.assertRaises(ai_api.ApiAuthError):
            ai_api.run("p", SCHEMA, cfg, self.work)


class ProbeTest(Base):
    def test_probe_passes_only_when_the_model_searched(self):
        good = config("anthropic", lambda call: anthropic_message(search_blocks(1) + [text_block("白宫新闻标题")]))
        out = ai_api.probe(good, self.work)
        self.assertTrue(out["ok"], out)
        tools = good.transport.bodies("/messages")[0]["tools"]
        self.assertEqual([t["max_uses"] for t in tools], [1, 1], "检测只检索 1 次")
        lazy = config("openai", lambda call: openai_response("凭记忆回答", searches=0))
        out = ai_api.probe(lazy, self.work)
        self.assertFalse(out["ok"])
        self.assertEqual(out["message"], "该接口不支持联网检索，无法更新人事动态。", "检测结果照审定的文案")
        self.assertFalse(lazy.transport.calls[0]["body"].get("background"), "检测直接等结果，不用后台模式")
        denied = config("openai", lambda call: (401, {"error": {"message": "invalid key"}}))
        self.assertEqual(ai_api.probe(denied, self.work)["message"], "接口不可用：密钥无效或无权限，请核对后重试。")
        poor = config("openai", lambda call: (429, {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota"}}))
        self.assertEqual(ai_api.probe(poor, self.work)["message"], "接口不可用：接口额度不足，请核对后重试。")
        self.assertNoKey()


if __name__ == "__main__":
    unittest.main()
