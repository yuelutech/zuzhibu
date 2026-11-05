"""ai_api：用使用者自己的 AI 接口密钥直连，带联网检索。一局“立即更新”的研究、核查两次会话走这里（docs/PRODUCT.md 第七节）。

只用标准库 urllib。接两类有联网检索工具的接口，接口地址、模型名在“我的”页可改：

- openai：Responses 接口，POST {地址}/responses，工具 web_search（检索、打开网页、页内查找）；
  输出用 text.format 的严格 JSON Schema 约束。会话长，用后台模式提交后轮询；接口不认后台模式就直接等结果。
- anthropic：Messages 接口，POST {地址}/messages，服务端工具 web_search、web_fetch；遇 pause_turn 原样续上。
  输出靠提示词约束（附 schema），本地校验；不合格时在同一会话里要求按格式重答一次。

只能对话、没有联网检索工具的接口不能用：一局要求研究、核查两次会话都联网取证。能不能检索以接口的实际表现为准：
请求里的检索工具被拒，报 ApiNoWebSearch；会话里没有检索动作的，由调用方作废结果（bureaus/dispatch/research.py）。

密钥只放在请求头里，不写进过程记录、日志和报错；报错原文里出现密钥的一律隐去。
过程记录：每次调用在工作目录下建一个子目录，存提示词、schema、配置（不含密钥）、请求体和响应原文、汇总。

测试不联网：ApiConfig.transport 换成假的发送函数（tests/test_ai_api.py）。
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import http.client
import json
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import codex_cli, schema_lite

__all__ = [
    "PROVIDERS", "NO_WEB", "CHECK_MESSAGES", "REASONS", "ApiConfig", "ApiResult", "ApiError", "ApiAuthError", "ApiQuotaError",
    "ApiNoWebSearch", "ApiCancelled", "ApiHttpError", "ApiTimeout", "ApiOutputError", "capability", "config_from_settings",
    "normalize_base", "redact", "reason_of", "run", "probe",
]

#: 接口类型。web 为 False 的只能对话、没有联网检索，不能开一局更新。
PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {"label": "OpenAI 兼容接口", "base_url": "https://api.openai.com/v1", "model": "gpt-6.1-sol", "web": True},
    "anthropic": {"label": "Anthropic 接口", "base_url": "https://api.anthropic.com/v1", "model": "claude-opus-5-5",
                  "web": True},
    "other": {"label": "其他接口（只能对话，没有联网检索）", "base_url": "", "model": "", "web": False},
}
NO_WEB = "一局更新要求研究、核查两次会话都联网取证；这类接口没有联网检索工具，不能开更新"

ANTHROPIC_VERSION = "2023-06-01"
#: 基础版检索、取网页工具：模型直接调用，各型号都支持。
ANTHROPIC_TOOLS = ("web_search_20250305", "web_fetch_20250910")
USER_AGENT = "zuzhibu-ai-api/1"
RETRY_STATUS = (408, 409, 429, 500, 502, 503, 504, 529)
#: openai 接口里可以去掉再试的参数：兼容这类接口的服务不一定都认。
OPENAI_OPTIONAL = ("background", "store", "include", "max_tool_calls")

SCHEMA_HINT = "\n\n【输出格式】最终回复只写一个 JSON 对象，符合下面的 JSON Schema；不加说明文字，不用 Markdown 代码块。\n{schema}\n"
FIX_HINT = ("上面的最终回复不是符合 schema 的 JSON（{problems}）。不要再检索；按前面已经取得的证据，"
            "只输出一个符合 schema 的 JSON 对象，不加其他文字。")
PROBE_PROMPT = ("用联网检索查一下美国白宫官网（whitehouse.gov）现在首页上的一条新闻标题。必须先检索再回答，"
                "只回答这个标题，不超过 40 个字。")

Transport = Callable[[str, str, dict, "bytes | None", float], "tuple[int, dict, bytes]"]


# ──────────────────────────────────────────────────────────────── 报错


class ApiError(RuntimeError):
    """调用失败的基类。run_dir 指向过程记录，stats 是失败前统计到的联网动作和用量。"""

    def __init__(self, message: str, run_dir: Path | None = None, stats: dict[str, Any] | None = None):
        super().__init__(message)
        self.run_dir = run_dir
        self.stats = stats or empty_stats()


class ApiAuthError(ApiError):
    """密钥无效、没有权限或额度不足：不重试，整次更新停下。"""


class ApiQuotaError(ApiAuthError):
    """接口账户额度不足：不重试，整次更新停下。"""


class ApiNoWebSearch(ApiError):
    """这个接口或模型不能联网检索：整次更新停下。"""


class ApiCancelled(ApiError):
    """使用者停止了更新：不再发新请求；后台会话已取消。"""


class ApiHttpError(ApiError):
    def __init__(self, status: int, message: str, param: str = ""):
        super().__init__(f"接口返回 {status}：{message}")
        self.status = status
        self.detail = message
        self.param = param


class ApiTimeout(ApiError):
    """超过会话时限。"""


class ApiOutputError(ApiError):
    """回复没完成、被拒，或不是符合 schema 的 JSON。"""


def empty_stats() -> dict[str, Any]:
    return {"searches": 0, "opened": [], "input_tokens": 0, "output_tokens": 0, "errors": [], "requests": 0}


def redact(text: Any, *secrets: str) -> str:
    """把文字里的密钥原文换掉（报错、日志用）。"""
    text = str(text)
    for secret in secrets:
        if secret and len(secret) >= 8:
            text = text.replace(secret, "（密钥已隐去）")
    return text


# ──────────────────────────────────────────────────────────────── 配置


def normalize_base(url: str) -> str:
    """接口地址：https，本机测试可用 http://127.0.0.1；只写了域名的补 /v1；去掉结尾的 /。"""
    url = (url or "").strip().rstrip("/")
    parts = urllib.parse.urlsplit(url)
    local = parts.hostname in ("127.0.0.1", "localhost", "::1")
    if parts.scheme not in ("https", "http") or not parts.netloc or (parts.scheme == "http" and not local):
        raise ValueError("接口地址要用 https://（本机测试可用 http://127.0.0.1）")
    if parts.query or parts.fragment:
        raise ValueError("接口地址不能带 ? 或 #")
    return url + "/v1" if parts.path in ("", "/") else url


@dataclass
class ApiConfig:
    """一次会话的全部参数。密钥不进 repr、不进过程记录。"""

    provider: str
    api_key: str = field(default="", repr=False)
    model: str = ""
    base_url: str = ""
    effort: str | None = None
    """推理强度：openai 写进 reasoning.effort，anthropic 写进 output_config.effort；None 用接口默认。"""
    timeout: float = 600.0
    """单次请求等回复的时限（秒）。"""
    deadline: float = 1800.0
    """一次会话的总时限（秒），含续接、轮询和重试。"""
    retries: int = 3
    """限流、服务端错误、网络中断时重试几次。"""
    backoff: float = 5.0
    max_searches: int = 60
    """一次会话最多检索几次（anthropic web_search 的 max_uses；openai 的 max_tool_calls 按检索加打开网页算）。"""
    max_fetches: int = 60
    """一次会话最多打开几个网页（anthropic web_fetch 的 max_uses）。"""
    max_output_tokens: int = 32000
    fetch_tokens: int = 20000
    """anthropic 打开一个网页最多读进多少 token。"""
    poll: float = 5.0
    """openai 后台模式的轮询间隔（秒）。"""
    max_continuations: int = 20
    """anthropic pause_turn 最多续接几次。"""
    background: bool = True
    """openai 用后台模式。"""
    cancel: Callable[[], bool] | None = field(default=None, repr=False)
    """返回 True 表示使用者要停止：每次发请求、每次轮询之前看一眼。"""
    transport: Transport | None = field(default=None, repr=False)
    """发送函数 (方法, 网址, 请求头, 请求体, 时限) -> (状态码, 响应头, 响应体)；None 用 urllib。测试换成假的。"""
    sleep: Callable[[float], None] = field(default=time.sleep, repr=False)
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)

    def __post_init__(self) -> None:
        spec = PROVIDERS.get(self.provider)
        if spec is None:
            raise ValueError(f"不认识的接口类型：{self.provider!r}")
        if not spec["web"]:
            raise ApiNoWebSearch(NO_WEB)
        self.base_url = normalize_base(self.base_url or spec["base_url"])
        self.model = (self.model or spec["model"]).strip()
        self.effort = (self.effort or "").strip() or None

    @property
    def label(self) -> str:
        return PROVIDERS[self.provider]["label"]

    def describe(self) -> dict[str, Any]:
        """可写进过程记录的配置：不含密钥和函数。"""
        return {"provider": self.provider, "label": self.label, "base_url": self.base_url, "model": self.model,
                "effort": self.effort, "timeout": self.timeout, "deadline": self.deadline, "retries": self.retries,
                "max_searches": self.max_searches, "max_fetches": self.max_fetches,
                "max_output_tokens": self.max_output_tokens, "background": self.background, "api_key": "（不记录）"}


def capability(api: dict[str, Any] | None) -> tuple[bool, str]:
    """设置页填的接口能不能开一局更新：(能不能, 说明)。只看设置，不联网。"""
    api = api or {}
    provider = api.get("provider") or "openai"
    spec = PROVIDERS.get(provider)
    if spec is None:
        return False, f"不认识的接口类型：{provider}"
    if not spec["web"]:
        return False, NO_WEB
    if not str(api.get("api_key") or "").strip():
        return False, "还没有填密钥"
    try:
        normalize_base(api.get("base_url") or spec["base_url"])
    except ValueError as exc:
        return False, str(exc)
    return True, f"{spec['label']}，模型 {(api.get('model') or spec['model']).strip()}"


def config_from_settings(api: dict[str, Any] | None, **overrides: Any) -> ApiConfig:
    """设置页的 api 一节 → ApiConfig。不能开更新的抛 ApiNoWebSearch（没有检索）、ApiAuthError（没填密钥）或 ValueError。"""
    api = api or {}
    ok, why = capability(api)
    provider = api.get("provider") or "openai"
    if not ok:
        if provider in PROVIDERS and not PROVIDERS[provider]["web"]:
            raise ApiNoWebSearch(why)
        if why == "还没有填密钥":
            raise ApiAuthError(why)
        raise ValueError(why)
    return ApiConfig(provider=provider, api_key=str(api["api_key"]).strip(), model=str(api.get("model") or ""),
                     base_url=str(api.get("base_url") or ""), effort=api.get("effort") or None, **overrides)


# ──────────────────────────────────────────────────────────────── 发送与过程记录


def urllib_transport(method: str, url: str, headers: dict, body: bytes | None, timeout: float) -> tuple[int, dict, bytes]:
    """标准库发送：走系统代理设置；HTTP 错误码照常返回，网络错误抛 OSError 一类。"""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            raw = exc.read()
        finally:
            exc.close()
        return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, raw


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


class _Record:
    """过程记录目录：<时间>-<标签>-<随机码>/。写进去的一切先隐去密钥（接口报错时可能把密钥原样回显）。"""

    def __init__(self, workdir: Path | str, label: str, secret: str = ""):
        safe = re.sub(r"[^A-Za-z0-9_.一-鿿-]+", "-", label).strip("-") or "api"
        self.dir = Path(workdir) / f"{_dt.datetime.now():%Y%m%d-%H%M%S}-{safe}-{uuid.uuid4().hex[:6]}"
        self.dir.mkdir(parents=True, exist_ok=False)
        self.secret = secret

    def write(self, name: str, obj: Any) -> None:
        text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=1) + "\n"
        (self.dir / name).write_text(redact(text, self.secret), encoding="utf-8", newline="\n")


def _payload(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, ValueError):
        return {"raw": raw.decode("utf-8", "replace")[:2000]}


def _error_text(payload: Any) -> tuple[str, str]:
    """(报错文字, 出错的参数名)。"""
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("type") or err)[:600], str(err.get("param") or "")
        if err:
            return str(err)[:600], ""
        if payload.get("message"):
            return str(payload["message"])[:600], ""
        if payload.get("raw"):
            return str(payload["raw"])[:300], ""
    return "（没有报错说明）", ""


_QUOTA = re.compile(r"insufficient_quota|exceeded your current quota|credit balance|billing", re.I)


class _Client:
    def __init__(self, config: ApiConfig, record: _Record):
        self.config = config
        self.record = record
        self.send = config.transport or urllib_transport
        self.started = config.clock()
        self.requests = 0

    def left(self) -> float:
        return self.config.deadline - (self.config.clock() - self.started)

    def headers(self) -> dict[str, str]:
        c = self.config
        out = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT}
        if c.provider == "anthropic":
            out.update({"x-api-key": c.api_key, "anthropic-version": ANTHROPIC_VERSION})
        else:
            out["Authorization"] = "Bearer " + c.api_key
        return out

    def call(self, method: str, path: str, body: Any = None, *, keep: bool = True, timeout: float | None = None,
             final: bool = False) -> Any:
        """发一次请求，返回解析后的 JSON。keep：把请求体和响应存进过程记录（轮询的中间结果不存）。
        final：会话时限已到时的收尾请求（取消后台会话），不受时限挡、不重试。"""
        c = self.config
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        tries = 1 if final else c.retries + 1
        for attempt in range(tries):
            if not final and c.cancel is not None and c.cancel():
                raise ApiCancelled("使用者停止了更新")
            if self.left() <= 0 and not final:
                raise ApiTimeout(f"超过会话时限 {c.deadline:g} 秒")
            self.requests += 1
            n = self.requests
            if keep and body is not None:
                self.record.write(f"request-{n:03d}.json", body)
            wait = 20.0 if final else max(1.0, min(timeout or c.timeout, self.left()))
            try:
                status, headers, raw = self.send(method, c.base_url + path, self.headers(), data, wait)
            except (OSError, http.client.HTTPException, ValueError) as exc:  # 网络不通、超时、连接中断
                why = redact(f"{type(exc).__name__}：{exc}", c.api_key)
                self.record.write(f"response-{n:03d}.txt", "网络错误：" + why + "\n")
                if attempt < tries - 1:
                    c.sleep(c.backoff * 2 ** attempt)
                    continue
                raise ApiError(f"连不上接口（{why}）") from None
            payload = _payload(raw)
            if keep or status >= 300:
                self.record.write(f"response-{n:03d}.json", payload)
            if status < 300:
                return payload
            message, param = _error_text(payload)
            message = redact(message, c.api_key)
            if status in (401, 403):
                raise ApiAuthError(f"密钥无效或没有权限（{status}：{message}）")
            if _QUOTA.search(message):
                raise ApiQuotaError(f"接口账户额度不足（{status}：{message}）")
            if status in RETRY_STATUS and attempt < tries - 1:
                try:
                    wait = float(headers.get("retry-after") or 0)
                except ValueError:
                    wait = 0.0
                c.sleep(min(60.0, wait or c.backoff * 2 ** attempt))
                continue
            raise ApiHttpError(status, message, param)
        raise ApiError("重试次数用完")  # pragma: no cover - 循环里已经返回或抛出


def _no_web(exc: ApiHttpError) -> ApiError:
    """请求被拒是因为检索工具（这个接口或模型不支持）时，换成 ApiNoWebSearch。"""
    text = f"{exc.param} {exc.detail}".lower()
    if exc.status in (400, 404, 422) and re.search(r"web_search|web_fetch|\btools?\b", text):
        return ApiNoWebSearch(f"这个接口或模型不能用联网检索工具：{exc.detail}")
    return exc


# ──────────────────────────────────────────────────────────────── 两类接口


def _dedupe(stats: dict[str, Any]) -> dict[str, Any]:
    stats["opened"] = list(dict.fromkeys(stats["opened"]))
    return stats


class _OpenAI:
    """Responses 接口：web_search 工具，严格 JSON Schema 输出，后台模式提交后轮询。"""

    def __init__(self, client: _Client):
        self.client = client
        self.stats = empty_stats()

    def ask(self, prompt: str, schema: dict[str, Any] | None, *, probe: bool = False) -> str:
        c = self.client.config
        body: dict[str, Any] = {"model": c.model, "input": prompt, "tools": [{"type": "web_search"}], "tool_choice": "auto",
                                "max_output_tokens": c.max_output_tokens, "max_tool_calls": c.max_searches + c.max_fetches,
                                "include": ["web_search_call.action.sources"]}
        if schema is not None:
            body["text"] = {"format": {"type": "json_schema", "name": "result", "schema": schema, "strict": True}}
        if c.effort:
            body["reasoning"] = {"effort": c.effort}
        background = c.background and not probe
        for budget in range(2):
            resp = self._create(body, background)
            reason = (resp.get("incomplete_details") or {}).get("reason")
            if resp.get("status") == "incomplete" and reason == "max_output_tokens" and budget == 0:
                body["max_output_tokens"] = min(128000, body["max_output_tokens"] * 2)  # 写不完：放宽一次再来
                continue
            break
        self._count(resp)
        status = resp.get("status")
        if status == "failed":
            err = resp.get("error") or {}
            why = redact(err.get("message") or err, c.api_key)
            if re.search(r"web_search|\btools?\b", why, re.I):
                raise ApiNoWebSearch(f"这个接口或模型不能用联网检索工具：{why}")
            raise ApiError(f"会话失败：{why}")
        if status != "completed":
            raise ApiOutputError(f"回复没有完成（{status}：{reason or ''}）")
        texts, refusal = [], ""
        for item in resp.get("output") or []:
            if item.get("type") == "message":
                parts = item.get("content") or []
                texts = [p.get("text") or "" for p in parts if p.get("type") == "output_text"] or texts
                refusal = next((p.get("refusal") or "" for p in parts if p.get("type") == "refusal"), refusal)
        if not texts and refusal:
            raise ApiOutputError(f"模型拒绝回答：{refusal[:200]}")
        return "".join(texts)

    def fix(self, problems: list[str]) -> str:
        raise ApiOutputError("回复不符合 schema：" + "；".join(problems[:5]))

    def _create(self, body: dict[str, Any], background: bool) -> dict[str, Any]:
        client = self.client
        body = {**body, **({"background": True, "store": True} if background else {})}
        for _ in range(len(OPENAI_OPTIONAL) + 1):
            try:
                resp = client.call("POST", "/responses", body, timeout=None if background else client.left())
            except ApiHttpError as exc:
                dropped = next((k for k in OPENAI_OPTIONAL if k in body and (exc.param == k or (
                    exc.status == 400 and re.search(rf"\b{k}\b", exc.detail, re.I)))), None)
                if dropped is None:
                    raise _no_web(exc) from None
                body = {k: v for k, v in body.items() if k != dropped}  # 这个接口不认这个参数：去掉再试
                if dropped == "background":
                    body.pop("store", None)
                continue
            return self._wait(resp)
        raise ApiError("接口一再拒绝请求参数")

    def _wait(self, resp: dict[str, Any]) -> dict[str, Any]:
        client, c = self.client, self.client.config
        rid = resp.get("id")
        while resp.get("status") in ("queued", "in_progress") and rid:
            stopped = c.cancel is not None and c.cancel()
            if stopped or client.left() <= 0:
                try:
                    client.call("POST", f"/responses/{rid}/cancel", {}, keep=False, final=True)  # 不再花额度
                except ApiError:
                    pass
                if stopped:
                    raise ApiCancelled("使用者停止了更新，后台会话已取消")
                raise ApiTimeout(f"超过会话时限 {c.deadline:g} 秒，已取消")
            c.sleep(c.poll)
            resp = client.call("GET", f"/responses/{rid}", keep=False)
        client.record.write("response-final.json", resp)
        return resp

    def _count(self, resp: dict[str, Any]) -> None:
        s = self.stats
        for item in resp.get("output") or []:
            if item.get("type") != "web_search_call":
                continue
            action = item.get("action") or {}
            if item.get("status") in ("failed", "incomplete"):
                s["errors"].append(f"联网动作失败：{action.get('type')} {action.get('url') or action.get('query') or ''}".strip())
                continue
            s["searches"] += 1
            if action.get("type") in ("open_page", "find_in_page") and action.get("url"):
                s["opened"].append(action["url"])
        usage = resp.get("usage") or {}
        s["input_tokens"] += int(usage.get("input_tokens") or 0)
        s["output_tokens"] += int(usage.get("output_tokens") or 0)
        s["requests"] = self.client.requests
        _dedupe(s)


class _Anthropic:
    """Messages 接口：web_search、web_fetch 服务端工具；pause_turn 原样续上；输出靠提示词约束、本地校验。"""

    def __init__(self, client: _Client):
        self.client = client
        self.stats = empty_stats()
        self.messages: list[dict[str, Any]] = []
        self.blocks: list[dict[str, Any]] = []   # 全部回复块，按顺序（统计联网动作用）
        self.container: str | None = None
        self.probe = False

    def tools(self, probe: bool = False) -> list[dict[str, Any]]:
        c = self.client.config
        search, fetch = ANTHROPIC_TOOLS
        return [{"type": search, "name": "web_search", "max_uses": 1 if probe else c.max_searches},
                {"type": fetch, "name": "web_fetch", "max_uses": 1 if probe else c.max_fetches,
                 "max_content_tokens": c.fetch_tokens}]

    def ask(self, prompt: str, schema: dict[str, Any] | None, *, probe: bool = False) -> str:
        text = prompt + (SCHEMA_HINT.format(schema=json.dumps(schema, ensure_ascii=False)) if schema is not None else "")
        self.messages = [{"role": "user", "content": text}]
        self.probe = probe
        return self._turn()

    def fix(self, problems: list[str]) -> str:
        self.messages.append({"role": "user", "content": FIX_HINT.format(problems="；".join(problems[:5]))})
        return self._turn()

    def _turn(self) -> str:
        """一轮回答：遇 pause_turn 把已有内容原样送回续上，直到结束。返回这一轮最后的文字。"""
        client, c = self.client, self.client.config
        content: list[dict[str, Any]] = []
        try:
            for _ in range(c.max_continuations + 1):
                body: dict[str, Any] = {"model": c.model, "max_tokens": c.max_output_tokens, "tools": self.tools(self.probe),
                                        "messages": self.messages + ([{"role": "assistant", "content": content}] if content else [])}
                if c.effort:
                    body["output_config"] = {"effort": c.effort}
                if self.container:
                    body["container"] = self.container
                first = not content
                try:
                    resp = client.call("POST", "/messages", body, keep=first)  # 续接的请求体含全部历史，不另存
                except ApiHttpError as exc:
                    raise _no_web(exc) from None
                if not first:
                    client.record.write(f"response-{client.requests:03d}.json", resp)
                blocks = resp.get("content") or []
                content += blocks
                self.blocks += blocks
                self.container = ((resp.get("container") or {}).get("id")) or self.container
                usage = resp.get("usage") or {}
                self.stats["input_tokens"] += sum(int(usage.get(k) or 0) for k in (
                    "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
                self.stats["output_tokens"] += int(usage.get("output_tokens") or 0)
                stop = resp.get("stop_reason")
                if stop == "pause_turn":
                    continue
                if stop == "refusal":
                    raise ApiOutputError("模型拒绝回答")
                if stop == "max_tokens":
                    raise ApiOutputError(f"回复超过长度上限 {c.max_output_tokens} token，没写完")
                self.messages.append({"role": "assistant", "content": content})
                return _final_text(content)
            raise ApiTimeout(f"续接 {c.max_continuations} 次仍没有结束")
        finally:
            self._count()

    def _count(self) -> None:
        """联网动作：成功的 web_search、web_fetch 各算一次；打开过的网页记链接。结果块报错的不算。"""
        s = self.stats
        results = {b.get("tool_use_id"): b for b in self.blocks if b.get("type") in ("web_search_tool_result", "web_fetch_tool_result")}
        s["searches"], s["opened"], s["errors"] = 0, [], []
        for b in self.blocks:
            if b.get("type") != "server_tool_use" or b.get("name") not in ("web_search", "web_fetch"):
                continue
            arg = (b.get("input") or {}).get("url") or (b.get("input") or {}).get("query") or ""
            res = results.get(b.get("id"))
            content = (res or {}).get("content")
            if res is None or (isinstance(content, dict) and content.get("error_code")):
                code = content.get("error_code") if isinstance(content, dict) else "没有结果"
                s["errors"].append(f"{b['name']} {arg}：{code}")
                continue
            s["searches"] += 1
            if b["name"] == "web_fetch":
                s["opened"].append((content or {}).get("url") or arg)
        s["requests"] = self.client.requests
        _dedupe(s)


def _final_text(content: list[dict[str, Any]]) -> str:
    """这一轮最后的文字：最后一个工具块之后的文字块连起来（引用把一段文字拆成几块的，照原样接上）。"""
    last_tool = max((i for i, b in enumerate(content) if b.get("type") != "text"), default=-1)
    tail = [b.get("text") or "" for b in content[last_tool + 1:] if b.get("type") == "text"]
    return "".join(tail) or "".join(b.get("text") or "" for b in content if b.get("type") == "text")


def _session(client: _Client) -> _OpenAI | _Anthropic:
    return _Anthropic(client) if client.config.provider == "anthropic" else _OpenAI(client)


# ──────────────────────────────────────────────────────────────── 入口


@dataclass
class ApiResult:
    data: Any
    """解析并通过 schema 校验的 JSON。"""
    text: str
    run_dir: Path
    stats: dict[str, Any]
    """联网动作和用量：searches（检索加打开网页的次数）、opened（打开过的网页）、input_tokens、output_tokens、errors、requests。"""


def check_output(text: str, schema: dict[str, Any] | None) -> tuple[Any, list[str]]:
    """(解析出的 JSON, 不合格的地方)。"""
    try:
        data = codex_cli.parse_json_message(text)
    except (ValueError, json.JSONDecodeError) as exc:
        return None, [f"不是合法 JSON：{exc}"]
    if schema is not None:
        problems = schema_lite.validate(data, schema)
        if problems:
            return data, problems[:10]
    return data, []


def run(prompt: str, schema: dict[str, Any] | None, config: ApiConfig, workdir: Path | str, *, label: str = "api") -> ApiResult:
    """开一次联网会话，返回校验过的 JSON。失败抛 ApiError 一类，带过程记录目录和已统计的联网动作。"""
    if not prompt.strip():
        raise ValueError("提示词为空")
    record = _Record(workdir, label, config.api_key)
    record.write("prompt.md", prompt)
    if schema is not None:
        record.write("schema.json", schema)
    record.write("config.json", config.describe())
    client = _Client(config, record)
    session = _session(client)
    summary: dict[str, Any] = {"label": label, "started_at": _now(), "config": config.describe()}
    text = ""
    try:
        text = session.ask(prompt, schema)
        data, problems = check_output(text, schema)
        if problems:
            record.write("first_answer.txt", text)
            text = session.fix(problems)
            data, problems = check_output(text, schema)
            if problems:
                raise ApiOutputError("回复不符合 schema：" + "；".join(problems[:5]))
    except ApiError as exc:
        exc.run_dir, exc.stats = record.dir, session.stats
        record.write("last_message.txt", text)
        record.write("run.json", {**summary, "finished_at": _now(), "outcome": "failed",
                                  "error": f"{type(exc).__name__}：{redact(exc, config.api_key)}", "stats": session.stats})
        raise
    record.write("last_message.txt", text)
    record.write("result.json", data)
    record.write("run.json", {**summary, "finished_at": _now(), "outcome": "ok", "stats": session.stats})
    return ApiResult(data=data, text=text, run_dir=record.dir, stats=session.stats)


#: 检测结果：维护者 2026-10-21 审定的文案（设置一节）；文案没写到的情形照同一句式。
CHECK_MESSAGES = {
    "ok": "接口可用，支持联网检索。",
    "no_web": "该接口不支持联网检索，无法更新人事动态。",
    "auth": "接口不可用：密钥无效或无权限，请核对后重试。",
}


#: 出错的类别和给使用者看的短语，用在“更新未完成：××。”“接口不可用：××，请核对后重试。”里。
REASONS = {
    "quota": "接口额度不足",
    "auth": "密钥无效或无权限",
    "no_web": "该接口不支持联网检索",
    "timeout": "接口响应超时",
    "address": "接口地址或模型名称有误",
    "network": "网络连接失败",
    "error": "接口出错",
}


def reason_of(exc: BaseException) -> tuple[str, str]:
    """出错的类别和短语：(code, REASONS[code])。"""
    if isinstance(exc, ApiQuotaError):
        code = "quota"
    elif isinstance(exc, ApiAuthError):
        code = "auth"
    elif isinstance(exc, ApiNoWebSearch):
        code = "no_web"
    elif isinstance(exc, ApiTimeout):
        code = "timeout"
    elif isinstance(exc, ValueError) or (isinstance(exc, ApiHttpError) and exc.status in (400, 404, 422)):
        code = "address"
    elif isinstance(exc, ApiError) and str(exc).startswith("连不上接口"):
        code = "network"
    else:
        code = "error"
    return code, REASONS[code]


def probe(config: ApiConfig, workdir: Path | str | None = None) -> dict[str, Any]:
    """检测这个接口和模型能不能联网检索：问一个必须检索才答得上的小问题（约检索 1 次，花费很少）。

    返回 {ok, code, reason, message, detail, searches, answer}：message 照文案，reason 是 REASONS 里的短语，
    detail 是给维护者看的原因（已隐去密钥）。不抛异常。
    """
    cfg = dataclasses.replace(config, max_searches=1, max_fetches=1, max_output_tokens=4000, deadline=min(config.deadline, 240),
                              retries=min(config.retries, 1))
    tmp = tempfile.TemporaryDirectory() if workdir is None else None
    try:
        client = _Client(cfg, _Record(workdir or tmp.name, "probe", cfg.api_key))
        session = _session(client)
        try:
            answer = session.ask(PROBE_PROMPT, None, probe=True)
        except ApiError as exc:
            code, why = reason_of(exc)
            return {"ok": False, "code": code, "reason": why, "searches": session.stats["searches"], "answer": "",
                    "message": CHECK_MESSAGES.get(code) or f"接口不可用：{why}，请核对后重试。",
                    "detail": redact(exc, cfg.api_key)}
        n = session.stats["searches"]
        if n <= 0:
            return {"ok": False, "code": "no_web", "reason": REASONS["no_web"], "searches": 0, "answer": answer[:200],
                    "message": CHECK_MESSAGES["no_web"],
                    "detail": f"接口能连上，但模型没有联网检索（{cfg.label}，模型 {cfg.model}）"}
        return {"ok": True, "code": "ok", "reason": "", "searches": n, "answer": answer[:200], "message": CHECK_MESSAGES["ok"],
                "detail": f"{cfg.label}，模型 {cfg.model}，这次检索 {n} 次"}
    finally:
        if tmp is not None:
            tmp.cleanup()
