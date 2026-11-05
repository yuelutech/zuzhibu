"""codex_cli：封装本机 Codex CLI 的 ``codex exec`` 非交互调用。

设计要点（对应 docs/ROADMAP.md“做法”一节和 shared/backends/README.md）：

- 只读沙箱：``--sandbox read-only``；关闭联网检索：``-c web_search="disabled"``；
  ``codex exec`` 本身是非交互模式，审批策略固定为 never，模型发起的写文件、执行命令审批一律被拒。
- 不读本机 config.toml：``--ignore-user-config``。本机配置里的 MCP 服务器、插件、应用连接器（邮箱、即时通讯、
  发邮件等）、钩子都不带进改写任务；模型名和推理强度由调用方给，或用 ``user_defaults()`` 只从
  config.toml 取这两项。认证仍用本机登录。
- 关掉模型的工具：``-c features.<名>=false``，见 ``DEFAULT_DISABLED_FEATURES``。ChatGPT 登录自带的
  apps 连接器（codex_apps）里有联网搜索，只关 ``web_search`` 关不住，2026-10-17 本机实跑时模型用它检索过；
  shell、补丁、看图、生图、浏览器、插件、记忆等改写任务都用不上，一并关掉。
- JSON 输出：``--json`` 让 Codex 按 JSONL 打印事件流，``--output-schema`` 约束最终回复的形状，
  ``-o`` 把最终回复另存一份。三者都用，互为备份。
- 超时：``subprocess`` 超时后杀掉子进程，算作一次失败。
- 重试：超时、非零退出、空输出、非 JSON、不符 schema 都重试，指数退避；
  未登录、找不到可执行程序不重试。
- 过程记录：每次调用在工作目录下建一个子目录，存提示词、schema、每次尝试的命令行、stdout、stderr、
  元数据，以及最终结果；提示词里有原文，所以工作目录应放在 .gitignore 覆盖的 output/ 下。
- 认证：用使用者本机已登录的 Codex（或本机环境里的 CODEX_API_KEY）。本模块不读取、不复制任何认证文件。

只用标准库。测试用 tests/fakes/fake_codex.py 冒充 codex，不联网。

命令行形态（供本机核对）::

    codex exec --json --color never --sandbox read-only -C <空目录> --skip-git-repo-check --ephemeral \\
        --ignore-user-config [-m 模型] [-c model_reasoning_effort="max"] -c web_search="disabled" \\
        -c features.apps=false -c features.shell_tool=false …（DEFAULT_DISABLED_FEATURES 逐项） \\
        --output-schema schema.json -o last_message.txt  < prompt.md

以上标志按 openai/codex 仓库 codex-rs/exec/src/cli.rs 与 codex-rs/utils/cli/src/shared_options.rs
（2026-10 核对），并在本机 codex-cli 0.163.0-alpha.10.2 上实跑确认；本机版本不同时用
``CodexConfig.extra_args`` 和 ``ephemeral``、``ignore_user_config`` 等开关调整。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import schema_lite

__all__ = [
    "CodexConfig",
    "Attempt",
    "CodexResult",
    "CodexError",
    "CodexNotFound",
    "CodexTimeout",
    "CodexExitError",
    "CodexAuthError",
    "CodexOutputError",
    "build_command",
    "resolve_command",
    "find_desktop_codex",
    "user_defaults",
    "extract_last_message",
    "parse_json_message",
    "run",
]

SANDBOX_MODES = ("read-only", "workspace-write", "danger-full-access")
WEB_SEARCH_MODES = ("disabled", "cached", "indexed", "live")

#: 默认关掉的 Codex 功能（``codex features list`` 里的名字，codex-cli 0.163 核对）。
#: 不认识的名字 Codex 会忽略（除非加 --strict-config），老版本照样能跑。
DEFAULT_DISABLED_FEATURES = (
    "apps",  # ChatGPT 连接器，含 search_service.web_run 联网搜索
    "shell_tool",
    "unified_exec",
    "code_mode_host",
    "multi_agent",
    "view_image",
    "browser_use",
    "browser_use_external",
    "in_app_browser",
    "computer_use",
    "image_generation",
    "memories",
    "plugins",
    "remote_plugin",
    "hooks",
    "skill_search",
    "tool_suggest",
    "skill_mcp_dependency_install",
    "goals",
    "sleep_tool",
)

# stderr 里出现这些字样时判定为认证问题，不重试。
_AUTH_MARKERS = (
    "not logged in",
    "login required",
    "please run `codex login`",
    "please run codex login",
    "unauthorized",
    "invalid api key",
    "authentication",
    "401",
)


@dataclass
class CodexConfig:
    """一次调用的全部可调参数。默认值就是 v0.1 的生产配置。"""

    command: Sequence[str] = ("codex",)
    """可执行程序及其固定前缀参数。测试时换成 (sys.executable, "tests/fakes/fake_codex.py")。"""
    model: str | None = None
    sandbox: str = "read-only"
    cwd: Path | None = None
    """传给 -C 的工作根目录。None 时在过程记录目录下建一个空目录，模型看不到仓库。"""
    timeout: float = 600.0
    retries: int = 2
    """首次之外最多再试几次。"""
    backoff: float = 2.0
    """第 n 次重试前等待 backoff * 2**(n-1) 秒。"""
    web_search: str | None = "disabled"
    """写进 -c web_search="..."；None 表示不传，沿用本机 config.toml。"""
    ephemeral: bool = True
    """--ephemeral：不把会话落盘到 ~/.codex/sessions。老版本不认这个标志时设 False。"""
    ignore_user_config: bool = True
    """--ignore-user-config：不加载本机 config.toml（MCP 服务器、插件、应用连接器、钩子都不进来），认证照旧。"""
    reasoning_effort: str | None = None
    """写进 -c model_reasoning_effort="..."；None 表示不传。"""
    disabled_features: Sequence[str] = DEFAULT_DISABLED_FEATURES
    """每项一个 -c features.<名>=false；() 表示不关。"""
    skip_git_repo_check: bool = True
    color: str = "never"
    config_overrides: Sequence[str] = ()
    """每项一个 -c key=value，原样传递。"""
    extra_args: Sequence[str] = ()
    """追加在 exec 之后、提示词之前的其他参数。"""
    env: Mapping[str, str] | None = None
    """叠加到 os.environ 上的环境变量。"""
    probe_version: bool = True
    """先跑一次 ``codex --version`` 记进过程记录；失败不影响主流程。"""
    sleep: Callable[[float], None] = time.sleep
    """退避等待函数，测试里替换掉以免真等。"""

    def __post_init__(self) -> None:
        if self.sandbox not in SANDBOX_MODES:
            raise ValueError(f"sandbox 只能是 {SANDBOX_MODES}，收到 {self.sandbox!r}")
        if self.web_search is not None and self.web_search not in WEB_SEARCH_MODES:
            raise ValueError(f"web_search 只能是 {WEB_SEARCH_MODES} 或 None，收到 {self.web_search!r}")
        if self.retries < 0:
            raise ValueError("retries 不能为负")
        if self.timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        self.command = tuple(self.command)
        if not self.command:
            raise ValueError("command 不能为空")
        self.disabled_features = tuple(self.disabled_features)

    def describe(self) -> dict[str, Any]:
        """可写进过程记录的 JSON 形态（不含函数和环境变量值）。"""
        d = asdict(self)
        d.pop("sleep", None)
        d["env"] = sorted(self.env.keys()) if self.env else []
        d["cwd"] = str(self.cwd) if self.cwd else None
        d["command"] = list(self.command)
        d["config_overrides"] = list(self.config_overrides)
        d["extra_args"] = list(self.extra_args)
        d["disabled_features"] = list(self.disabled_features)
        return d


@dataclass
class Attempt:
    """一次子进程调用的记录。"""

    index: int
    command: list[str]
    started_at: str
    duration: float = 0.0
    returncode: int | None = None
    timed_out: bool = False
    outcome: str = "pending"
    """ok | timeout | exit | auth | empty | bad_json | schema"""
    error: str | None = None
    stdout_path: str = ""
    stderr_path: str = ""
    message_chars: int = 0
    schema_errors: list[str] = field(default_factory=list)


@dataclass
class CodexResult:
    data: Any
    """解析并通过 schema 校验的 JSON（没给 schema 时是解析出的 JSON）。"""
    text: str
    """模型最终回复的原文。"""
    run_dir: Path
    attempts: list[Attempt]
    usage: dict[str, Any] | None = None
    thread_id: str | None = None
    codex_version: str | None = None

    @property
    def attempts_used(self) -> int:
        return len(self.attempts)


class CodexError(RuntimeError):
    """调用失败的基类。run_dir 指向过程记录，attempts 是每次尝试。"""

    def __init__(self, message: str, run_dir: Path | None = None, attempts: list[Attempt] | None = None):
        super().__init__(message)
        self.run_dir = run_dir
        self.attempts = attempts or []


class CodexNotFound(CodexError):
    """找不到 codex 可执行程序。"""


class CodexTimeout(CodexError):
    """每次尝试都超时。"""


class CodexExitError(CodexError):
    """子进程非零退出。"""


class CodexAuthError(CodexExitError):
    """未登录或凭据无效，不重试。"""


class CodexOutputError(CodexError):
    """输出不是 JSON，或不符合 schema。"""


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


def find_desktop_codex(local_appdata: str | os.PathLike | None = None) -> Path | None:
    """Windows 上 Codex 桌面版自带的命令行：%LOCALAPPDATA%\\OpenAI\\Codex\\bin\\<哈希>\\codex.exe。

    桌面版每次更新换一个哈希目录，旧目录随即删掉，写死路径会失效；这里取修改时间最新的一个。找不到返回 None。
    """
    base = local_appdata if local_appdata is not None else os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    candidates = [p for p in (Path(base) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe") if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def resolve_command(command: Sequence[str]) -> list[str] | None:
    """把 command[0] 解析成绝对路径（Windows 上会找到 codex.cmd）；找不到返回 None。

    command[0] 恰好是 ``codex`` 而 PATH 里没有时，再找 Windows 桌面版自带的 codex.exe。
    """
    if not command:
        return None
    head = command[0]
    candidate = Path(head)
    if candidate.is_file():
        # 子进程在沙箱目录里启动，相对路径必须先转成绝对路径；不解析符号链接，npm 的外壳脚本靠链接定位自己。
        absolute = os.path.abspath(str(candidate))
        if candidate.suffix.lower() == ".py":
            # 指向 .py 文件时用当前解释器跑，测试用的假 codex 走这条路，Windows 上也能用。
            return [sys.executable, absolute, *command[1:]]
        return [absolute, *command[1:]]
    found = shutil.which(head)
    if found is None and head == "codex":
        desktop = find_desktop_codex()
        found = str(desktop) if desktop else None
    if found is None:
        return None
    return [os.path.abspath(found), *command[1:]]


def user_defaults(codex_home: str | os.PathLike | None = None) -> dict[str, str]:
    """从本机 config.toml 只取顶层的 ``model`` 和 ``model_reasoning_effort`` 两项。

    配合 ``--ignore-user-config`` 用：MCP、插件等不带进来，但沿用使用者选定的模型和推理强度。
    文件不在、读不了、不是合法 TOML 或 Python 没有 tomllib（3.10）时返回空 dict。不读认证文件。
    """
    try:
        import tomllib
    except ImportError:  # pragma: no cover - Python 3.10
        return {}
    home = Path(codex_home or os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    try:
        data = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {key: data[key] for key in ("model", "model_reasoning_effort") if isinstance(data.get(key), str) and data[key]}


def build_command(
    config: CodexConfig,
    *,
    cwd: Path,
    schema_path: Path | None,
    last_message_path: Path,
    resolved: Sequence[str] | None = None,
) -> list[str]:
    """拼出 ``codex exec ...`` 命令行。提示词不在里面，走 stdin。"""
    base = list(resolved) if resolved is not None else list(config.command)
    cmd = [*base, "exec", "--json", "--color", config.color, "--sandbox", config.sandbox, "-C", str(cwd)]
    if config.skip_git_repo_check:
        cmd.append("--skip-git-repo-check")
    if config.ephemeral:
        cmd.append("--ephemeral")
    if config.ignore_user_config:
        cmd.append("--ignore-user-config")
    if config.model:
        cmd.extend(["-m", config.model])
    if config.reasoning_effort:
        cmd.extend(["-c", f'model_reasoning_effort="{config.reasoning_effort}"'])
    if config.web_search is not None:
        cmd.extend(["-c", f'web_search="{config.web_search}"'])
    for feature in config.disabled_features:
        cmd.extend(["-c", f"features.{feature}=false"])
    for override in config.config_overrides:
        cmd.extend(["-c", override])
    cmd.extend(config.extra_args)
    if schema_path is not None:
        cmd.extend(["--output-schema", str(schema_path)])
    cmd.extend(["-o", str(last_message_path)])
    return cmd


# ---------------------------------------------------------------------------
# 输出解析
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*\n(.*?)\n\s*```\s*$", re.S)


def extract_last_message(stdout_text: str) -> tuple[str | None, dict[str, Any]]:
    """从 ``--json`` 的 JSONL 事件流里取最后一条 agent_message。

    兼容两种事件形态：
    - 现行：``{"type":"item.completed","item":{"type":"agent_message","text":"..."}}``
    - 旧版：``{"msg":{"type":"agent_message","message":"..."}}`` 与 ``task_complete.last_agent_message``

    返回 (最后一条消息或 None, 附加信息{thread_id, usage, errors, events, non_json_lines})。
    """
    info: dict[str, Any] = {"thread_id": None, "usage": None, "errors": [], "events": 0, "non_json_lines": 0}
    last: str | None = None
    for raw in stdout_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            info["non_json_lines"] += 1
            continue
        if not isinstance(event, dict):
            info["non_json_lines"] += 1
            continue
        info["events"] += 1
        etype = event.get("type")
        if etype == "thread.started":
            info["thread_id"] = event.get("thread_id")
        elif etype == "turn.completed":
            info["usage"] = event.get("usage")
        elif etype in ("item.completed", "item.updated"):
            item = event.get("item") or {}
            if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                last = item["text"]
        elif etype == "turn.failed":
            err = event.get("error") or {}
            info["errors"].append(str(err.get("message") or err))
        elif etype == "error":
            info["errors"].append(str(event.get("message")))
        msg = event.get("msg")
        if isinstance(msg, dict):
            mtype = msg.get("type")
            if mtype == "agent_message" and isinstance(msg.get("message"), str):
                last = msg["message"]
            elif mtype == "task_complete" and isinstance(msg.get("last_agent_message"), str):
                last = msg["last_agent_message"]
            elif mtype == "error":
                info["errors"].append(str(msg.get("message")))
    return last, info


def parse_json_message(text: str) -> Any:
    """把模型回复解析成 JSON。容忍围栏代码块和前后杂文字，找最外层的对象或数组。"""
    stripped = text.strip()
    if not stripped:
        raise ValueError("回复为空")
    fence = _FENCE_RE.match(stripped)
    if fence:
        stripped = fence.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (stripped.find("{"), stripped.find("[")) if i >= 0]
    if not starts:
        raise ValueError("回复里没有 JSON 对象")
    start = min(starts)
    closer = "}" if stripped[start] == "{" else "]"
    end = stripped.rfind(closer)
    if end <= start:
        raise ValueError("回复里的 JSON 没有闭合")
    return json.loads(stripped[start : end + 1])


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_json(path: Path, data: Any) -> None:
    _write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _decode(data: bytes | None) -> str:
    if not data:
        return ""
    return data.decode("utf-8", errors="replace")


def _looks_like_auth_problem(stderr: str) -> bool:
    low = stderr.lower()
    return any(marker in low for marker in _AUTH_MARKERS)


def _probe_version(resolved: Sequence[str], env: Mapping[str, str]) -> str | None:
    try:
        proc = subprocess.run(
            [*resolved, "--version"], capture_output=True, timeout=20, env=dict(env), check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = _decode(proc.stdout).strip() or _decode(proc.stderr).strip()
    return text.splitlines()[0] if text else None


def _make_run_dir(workdir: Path, label: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.一-鿿-]+", "-", label).strip("-") or "codex"
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = workdir / f"{stamp}-{safe}-{uuid.uuid4().hex[:6]}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def run(
    prompt: str,
    schema: dict[str, Any] | None,
    config: CodexConfig,
    workdir: Path | str,
    *,
    label: str = "codex",
    repair_hint: str | None = None,
) -> CodexResult:
    """调用一次 ``codex exec``，返回校验过的 JSON。

    :param prompt: 完整提示词，经 stdin 送入。
    :param schema: 最终回复的 JSON Schema；None 时不传 --output-schema，只要求回复可解析为 JSON。
    :param config: 调用参数。
    :param workdir: 过程记录根目录，每次调用在其下新建子目录。
    :param label: 子目录名的一部分，便于找。
    :param repair_hint: 上次输出不是合法 JSON 或不符 schema 时，重试会把这段话追加到提示词末尾；
        None 用内置的一句话。
    """
    if not prompt.strip():
        raise ValueError("提示词为空")
    workdir = Path(workdir)
    run_dir = _make_run_dir(workdir, label)
    attempts: list[Attempt] = []

    env = dict(os.environ)
    if config.env:
        env.update(config.env)

    resolved = resolve_command(config.command)
    if resolved is None:
        msg = (
            f"找不到 codex 可执行程序：{config.command[0]!r}。本机需安装并登录 Codex CLI，"
            "或用 --codex 指定路径，或用 --dry-run 只生成提示词。"
        )
        _write_json(run_dir / "run.json", {"outcome": "not_found", "error": msg, "config": config.describe()})
        raise CodexNotFound(msg, run_dir, attempts)

    cwd = Path(config.cwd) if config.cwd else run_dir / "sandbox"
    cwd.mkdir(parents=True, exist_ok=True)

    _write_text(run_dir / "prompt.md", prompt)
    schema_path: Path | None = None
    if schema is not None:
        schema_path = run_dir / "schema.json"
        _write_json(schema_path, schema)
    last_message_path = run_dir / "last_message.txt"

    version = _probe_version(resolved, env) if config.probe_version else None
    summary: dict[str, Any] = {
        "label": label,
        "started_at": _now_iso(),
        "codex_version": version,
        "config": config.describe(),
        "resolved_command": resolved,
        "outcome": "pending",
        "attempts": [],
    }

    hint = repair_hint or (
        "\n\n【系统提示】上一次输出不是合法 JSON，或不符合给定的 JSON Schema。"
        "这次只输出一个符合 schema 的 JSON 对象，不要输出其他文字。\n"
    )
    needs_hint = False
    max_attempts = config.retries + 1
    final_error: CodexError | None = None
    result: CodexResult | None = None

    for index in range(1, max_attempts + 1):
        if index > 1:
            delay = config.backoff * (2 ** (index - 2))
            config.sleep(delay)
        this_prompt = prompt + hint if needs_hint else prompt
        if last_message_path.exists():
            last_message_path.unlink()
        cmd = build_command(config, cwd=cwd, schema_path=schema_path, last_message_path=last_message_path, resolved=resolved)
        attempt = Attempt(index=index, command=cmd, started_at=_now_iso())
        attempt.stdout_path = str(run_dir / f"attempt-{index}.stdout.jsonl")
        attempt.stderr_path = str(run_dir / f"attempt-{index}.stderr.txt")
        attempts.append(attempt)
        _write_text(run_dir / f"attempt-{index}.cmd.txt", " ".join(_quote(part) for part in cmd) + "\n")

        t0 = time.monotonic()
        stdout_text = stderr_text = ""
        try:
            proc = subprocess.run(
                cmd,
                input=this_prompt.encode("utf-8"),
                capture_output=True,
                timeout=config.timeout,
                env=env,
                cwd=str(cwd),
                check=False,
            )
            stdout_text, stderr_text = _decode(proc.stdout), _decode(proc.stderr)
            attempt.returncode = proc.returncode
        except subprocess.TimeoutExpired as exc:
            stdout_text, stderr_text = _decode(exc.stdout), _decode(exc.stderr)
            attempt.timed_out = True
            attempt.outcome = "timeout"
            attempt.error = f"超过 {config.timeout:g} 秒未结束，已终止"
        except OSError as exc:
            attempt.outcome = "exit"
            attempt.error = f"启动子进程失败：{exc}"
        finally:
            attempt.duration = round(time.monotonic() - t0, 3)
            _write_text(Path(attempt.stdout_path), stdout_text)
            _write_text(Path(attempt.stderr_path), stderr_text)

        if attempt.outcome == "pending":
            _classify(attempt, stdout_text, stderr_text, last_message_path, schema)

        _write_json(run_dir / f"attempt-{index}.meta.json", asdict(attempt))
        summary["attempts"].append(asdict(attempt))

        if attempt.outcome == "ok":
            text = attempt_text(attempt, stdout_text, last_message_path)
            data = parse_json_message(text)
            _, info = extract_last_message(stdout_text)
            result = CodexResult(
                data=data,
                text=text,
                run_dir=run_dir,
                attempts=attempts,
                usage=info.get("usage"),
                thread_id=info.get("thread_id"),
                codex_version=version,
            )
            _write_json(run_dir / "result.json", data)
            summary["outcome"] = "ok"
            summary["usage"] = info.get("usage")
            summary["thread_id"] = info.get("thread_id")
            break

        needs_hint = attempt.outcome in ("bad_json", "schema")
        if attempt.outcome == "auth":
            final_error = CodexAuthError(f"Codex 未登录或凭据无效：{attempt.error}", run_dir, attempts)
            break
        if attempt.outcome == "timeout":
            final_error = CodexTimeout(f"第 {index} 次：{attempt.error}", run_dir, attempts)
        elif attempt.outcome == "exit":
            final_error = CodexExitError(f"第 {index} 次：{attempt.error}", run_dir, attempts)
        else:
            final_error = CodexOutputError(f"第 {index} 次：{attempt.error}", run_dir, attempts)

    summary["finished_at"] = _now_iso()
    if result is None:
        summary["outcome"] = "failed"
        summary["error"] = str(final_error) if final_error else "未知错误"
    _write_json(run_dir / "run.json", summary)
    if result is None:
        assert final_error is not None
        raise final_error
    return result


def attempt_text(attempt: Attempt, stdout_text: str, last_message_path: Path) -> str:
    """最终回复的取用顺序：-o 文件 > JSONL 里最后一条 agent_message > 整个 stdout。"""
    if last_message_path.exists():
        text = last_message_path.read_text(encoding="utf-8", errors="replace")
        if text.strip():
            return text
    last, info = extract_last_message(stdout_text)
    if last is not None and last.strip():
        return last
    if info["events"] > 0:
        # 有事件流但没有 agent_message：是空回复，不能把事件流当回复去解析。
        return ""
    return stdout_text


def _classify(
    attempt: Attempt,
    stdout_text: str,
    stderr_text: str,
    last_message_path: Path,
    schema: dict[str, Any] | None,
) -> None:
    """根据退出码和输出给这次尝试定性，填 outcome 和 error。"""
    rc = attempt.returncode
    if rc is None or rc != 0:
        tail = stderr_text.strip().splitlines()[-1] if stderr_text.strip() else ""
        attempt.error = f"退出码 {rc}" + (f"：{tail}" if tail else "")
        attempt.outcome = "auth" if _looks_like_auth_problem(stderr_text) else "exit"
        return
    text = attempt_text(attempt, stdout_text, last_message_path)
    attempt.message_chars = len(text)
    if not text.strip():
        attempt.outcome = "empty"
        attempt.error = "退出码 0 但没有任何回复"
        return
    try:
        data = parse_json_message(text)
    except (ValueError, json.JSONDecodeError) as exc:
        attempt.outcome = "bad_json"
        attempt.error = f"回复不是合法 JSON：{exc}"
        return
    if schema is not None:
        errors = schema_lite.validate(data, schema)
        if errors:
            attempt.schema_errors = errors[:20]
            attempt.outcome = "schema"
            attempt.error = "回复不符合 schema：" + "；".join(errors[:5])
            return
    attempt.outcome = "ok"


def _quote(part: str) -> str:
    if re.search(r"[\s\"']", part):
        return "'" + part.replace("'", "'\\''") + "'"
    return part


# ---------------------------------------------------------------------------
# 命令行入口：本机冒烟测试用
# ---------------------------------------------------------------------------


def _main(argv: Sequence[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="直接调一次 codex exec，打印校验过的 JSON。")
    parser.add_argument("prompt_file", help="提示词文件（UTF-8）")
    parser.add_argument("--schema", help="JSON Schema 文件")
    parser.add_argument("--workdir", default="output/runs/codex", help="过程记录目录")
    parser.add_argument("--codex", default="codex", help="codex 可执行程序")
    parser.add_argument("--model")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--no-ephemeral", action="store_true", help="不传 --ephemeral（老版本用）")
    args = parser.parse_args(argv)

    prompt = Path(args.prompt_file).read_text(encoding="utf-8")
    schema = json.loads(Path(args.schema).read_text(encoding="utf-8")) if args.schema else None
    defaults = user_defaults()
    config = CodexConfig(
        command=(args.codex,),
        model=args.model or defaults.get("model"),
        reasoning_effort=defaults.get("model_reasoning_effort"),
        timeout=args.timeout,
        retries=args.retries,
        ephemeral=not args.no_ephemeral,
    )
    try:
        result = run(prompt, schema, config, args.workdir, label=Path(args.prompt_file).stem)
    except CodexError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        if exc.run_dir:
            print(f"过程记录：{exc.run_dir}", file=sys.stderr)
        return 1
    print(json.dumps(result.data, ensure_ascii=False, indent=2))
    print(f"过程记录：{result.run_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
