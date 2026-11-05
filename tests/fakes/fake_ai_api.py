"""假的 AI 接口：冒充 Responses 接口和 Messages 接口回放事先写好的回复，不联网、不花额度。

用法：ApiConfig(transport=FakeApi(handler))；handler(调用) 返回 (状态码, JSON) 或回复字典（当 200），也可以抛 OSError 冒充断网。
每次调用记在 FakeApi.calls：方法、网址、请求头、请求体（已解析）。
"""

from __future__ import annotations

import json
from typing import Any, Callable

FAKE_KEY = "sk-fake-0123456789abcdefABCDEF"


class FakeApi:
    def __init__(self, handler: Callable[[dict[str, Any]], Any]):
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method: str, url: str, headers: dict, body: bytes | None, timeout: float):
        call = {"method": method, "url": url, "headers": dict(headers), "body": json.loads(body) if body else None,
                "timeout": timeout}
        self.calls.append(call)
        reply = self.handler(call)
        status, data = reply if isinstance(reply, tuple) else (200, reply)
        return status, {}, json.dumps(data, ensure_ascii=False).encode("utf-8")

    def bodies(self, path: str = "") -> list[dict[str, Any]]:
        return [c["body"] for c in self.calls if c["body"] is not None and c["url"].endswith(path)]


def openai_response(text: str = "", *, searches: int = 1, opened: tuple[str, ...] = (), status: str = "completed",
                    rid: str = "resp_1", reason: str | None = None) -> dict[str, Any]:
    """一次 Responses 接口的回复：若干检索、打开网页，最后一条消息。"""
    output: list[dict[str, Any]] = [{"type": "web_search_call", "id": f"ws_{n}", "status": "completed",
                                     "action": {"type": "search", "query": f"q{n}"}} for n in range(searches)]
    output += [{"type": "web_search_call", "id": f"wo_{n}", "status": "completed", "action": {"type": "open_page", "url": u}}
               for n, u in enumerate(opened)]
    if text:
        output.append({"type": "message", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": text, "annotations": []}]})
    return {"id": rid, "object": "response", "status": status, "output": output,
            "incomplete_details": {"reason": reason} if reason else None,
            "usage": {"input_tokens": 100, "output_tokens": 20}}


def search_blocks(n: int, query: str = "q", error: str | None = None) -> list[dict[str, Any]]:
    """Messages 接口的一次检索：server_tool_use 加结果块。"""
    uid = f"srvtoolu_s{n}"
    content: Any = {"type": "web_search_tool_result_error", "error_code": error} if error else [
        {"type": "web_search_result", "url": f"https://example.gov/{n}", "title": "t", "encrypted_content": "enc", "page_age": None}]
    return [{"type": "server_tool_use", "id": uid, "name": "web_search", "input": {"query": f"{query}{n}"}},
            {"type": "web_search_tool_result", "tool_use_id": uid, "content": content}]


def fetch_blocks(n: int, url: str, error: str | None = None) -> list[dict[str, Any]]:
    """Messages 接口的一次打开网页。"""
    uid = f"srvtoolu_f{n}"
    content = {"type": "web_fetch_tool_result_error", "error_code": error} if error else {
        "type": "web_fetch_result", "url": url, "retrieved_at": "2026-10-21T00:00:00Z",
        "content": {"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": "page"}}}
    return [{"type": "server_tool_use", "id": uid, "name": "web_fetch", "input": {"url": url}},
            {"type": "web_fetch_tool_result", "tool_use_id": uid, "content": content}]


def anthropic_message(blocks: list[dict[str, Any]], stop: str = "end_turn") -> dict[str, Any]:
    return {"id": "msg_1", "type": "message", "role": "assistant", "content": blocks, "stop_reason": stop,
            "usage": {"input_tokens": 50, "output_tokens": 10, "cache_read_input_tokens": 5,
                      "server_tool_use": {"web_search_requests": 1}}}


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}
