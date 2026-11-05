#!/usr/bin/env python3
"""fake_codex.py：测试用的假 codex 可执行程序，不联网、不调用任何模型。

模仿 ``codex exec`` 的参数和 ``--json`` 的 JSONL 事件流（thread.started、turn.started、item.completed、
turn.completed），认的参数照 openai/codex 的 cli.rs 与 shared_options.rs；不认识的参数像 clap 一样
以退出码 2 报错，这样 build_command 拼错标志会被测试抓到。

行为由环境变量控制：

FAKE_CODEX_SCRIPT      逗号分隔的每次调用行为，按调用次数依次取，取完重复最后一项。默认 ok。
                       ok         正常：输出事件流，写 -o 文件，退出 0
                       plain      老版本：不输出事件流，只把回复打到 stdout，写 -o 文件，退出 0
                       exit       退出码 FAKE_CODEX_EXIT_CODE（默认 1），stderr 写 simulated failure
                       auth       退出 1，stderr 写未登录
                       bad_json   回复不是 JSON
                       schema_bad 回复是 JSON 但不符 schema
                       empty      退出 0 但没有 agent_message，也不写 -o
                       sleep      先睡 FAKE_CODEX_SLEEP 秒（默认 5）再按 ok 处理
FAKE_CODEX_STATE       计数文件路径，记录已调用次数；不给则每次都算第 1 次。
FAKE_CODEX_RESPONSES   逗号分隔的 JSON 文件路径，ok/plain/sleep 时按调用次数依次取（取完重复最后一项）；
                       不给则按 --output-schema 合成一个最小合法对象。
FAKE_CODEX_RECORD      JSONL 文件路径，每次调用追加一行 {call, argv, prompt, cwd}。
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

VALUE_OPTIONS = {
    "--output-schema",
    "-o",
    "--output-last-message",
    "-C",
    "--cd",
    "-s",
    "--sandbox",
    "-m",
    "--model",
    "-c",
    "--config",
    "--color",
    "-p",
    "--profile",
    "-i",
    "--image",
    "--add-dir",
    "--local-provider",
    "--thread-source",
    "--cyber-access-program",
}
FLAG_OPTIONS = {
    "--json",
    "--experimental-json",
    "--skip-git-repo-check",
    "--ephemeral",
    "--full-auto",
    "--oss",
    "--strict-config",
    "--ignore-user-config",
    "--ignore-rules",
    "--dangerously-bypass-approvals-and-sandbox",
    "--yolo",
    "--approve-for-me",
    "--dangerously-bypass-hook-trust",
    "--worktree",
}
SANDBOX_VALUES = {"read-only", "workspace-write", "danger-full-access"}


def out(text: str) -> None:
    sys.stdout.buffer.write(text.encode("utf-8"))
    sys.stdout.buffer.flush()


def err(text: str) -> None:
    sys.stderr.buffer.write(text.encode("utf-8"))
    sys.stderr.buffer.flush()


def die(message: str, code: int = 2) -> None:
    err(f"error: {message}\n")
    sys.exit(code)


def next_call() -> int:
    state = os.environ.get("FAKE_CODEX_STATE")
    if not state:
        return 1
    path = Path(state)
    count = 0
    if path.exists():
        try:
            count = int(path.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            count = 0
    count += 1
    path.write_text(str(count), encoding="utf-8")
    return count


def pick(items: list[str], call: int) -> str:
    if not items:
        return ""
    return items[min(call, len(items)) - 1]


def synthesize(schema: object) -> object:
    if not isinstance(schema, dict):
        return "示例"
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if isinstance(kind, list):
        kind = kind[0]
    if kind == "object":
        props = schema.get("properties", {})
        required = schema.get("required", list(props))
        return {k: synthesize(v) for k, v in props.items() if k in required}
    if kind == "array":
        return []
    if kind == "string":
        return "示例"
    if kind == "integer":
        return 0
    if kind == "number":
        return 0.0
    if kind == "boolean":
        return False
    return None


def parse(argv: list[str]) -> tuple[dict, str | None]:
    opts: dict = {"flags": set(), "config": [], "values": {}}
    prompt: str | None = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in FLAG_OPTIONS:
            opts["flags"].add(arg)
        elif arg in VALUE_OPTIONS:
            if i + 1 >= len(argv):
                die(f"a value is required for '{arg}' but none was supplied")
            value = argv[i + 1]
            i += 1
            if arg in ("-c", "--config"):
                opts["config"].append(value)
            else:
                opts["values"][arg] = value
        elif arg.startswith("-") and arg != "-":
            die(f"unexpected argument '{arg}' found")
        else:
            if prompt is not None:
                die(f"unexpected argument '{arg}' found")
            prompt = arg
        i += 1
    sandbox = opts["values"].get("-s") or opts["values"].get("--sandbox")
    if sandbox is not None and sandbox not in SANDBOX_VALUES:
        die(f"invalid value '{sandbox}' for '--sandbox <SANDBOX_MODE>'")
    color = opts["values"].get("--color")
    if color is not None and color not in ("always", "never", "auto"):
        die(f"invalid value '{color}' for '--color <COLOR>'")
    return opts, prompt


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("--version", "-V"):
        out("codex-cli 0.0.0-fake\n")
        return 0
    if not argv or argv[0] != "exec":
        die("the fake only supports `codex exec` and `codex --version`")
    opts, prompt = parse(argv[1:])

    if prompt is None or prompt == "-":
        data = sys.stdin.buffer.read()
        if not data.strip():
            err("No prompt provided via stdin.\n")
            return 1
        prompt = data.decode("utf-8")

    call = next_call()
    record = os.environ.get("FAKE_CODEX_RECORD")
    if record:
        with open(record, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {"call": call, "argv": argv, "prompt": prompt, "cwd": os.getcwd(), "config": opts["config"]},
                    ensure_ascii=False,
                )
                + "\n"
            )

    script = [s.strip() for s in os.environ.get("FAKE_CODEX_SCRIPT", "ok").split(",") if s.strip()]
    behavior = pick(script, call) or "ok"

    if behavior == "exit":
        err("simulated failure\n")
        return int(os.environ.get("FAKE_CODEX_EXIT_CODE", "1"))
    if behavior == "auth":
        err("Not logged in. Please run `codex login`.\n")
        return 1
    if behavior == "sleep":
        time.sleep(float(os.environ.get("FAKE_CODEX_SLEEP", "5")))
        behavior = "ok"

    schema_path = opts["values"].get("--output-schema")
    schema = json.loads(Path(schema_path).read_text(encoding="utf-8")) if schema_path else None
    responses = [s.strip() for s in os.environ.get("FAKE_CODEX_RESPONSES", "").split(",") if s.strip()]
    if behavior == "bad_json":
        message = "这不是 JSON，{ 只是半个括号"
    elif behavior == "schema_bad":
        message = json.dumps({"unexpected": True}, ensure_ascii=False)
    elif behavior == "empty":
        message = None
    else:
        if responses:
            message = Path(pick(responses, call)).read_text(encoding="utf-8").strip()
        else:
            message = json.dumps(synthesize(schema), ensure_ascii=False)

    want_json = "--json" in opts["flags"] or "--experimental-json" in opts["flags"]
    last_path = opts["values"].get("-o") or opts["values"].get("--output-last-message")

    if behavior == "plain":
        if message is not None:
            out(message + "\n")
    elif want_json:
        out(json.dumps({"type": "thread.started", "thread_id": f"fake-thread-{call}"}) + "\n")
        out(json.dumps({"type": "turn.started"}) + "\n")
        if message is not None:
            out(json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": message}}, ensure_ascii=False) + "\n")
        out(
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": len(prompt), "cached_input_tokens": 0, "output_tokens": len(message or "")},
                }
            )
            + "\n"
        )
    elif message is not None:
        out(message + "\n")
    if last_path and message is not None:
        Path(last_path).write_text(message, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
