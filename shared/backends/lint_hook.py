"""lint_hook：流水线里“量化检查”这一步的调用点。

tools/lint 由云会话任务 01、02 另行实现，本模块只约定接口并在模块不存在时留空：

- 默认按文体找模块：讲话稿 ``tools.lint.speech_lint``，新闻稿 ``tools.lint.newswire_lint``。
- 模块里按顺序找 ``lint_text``、``lint``、``check_text``、``check`` 四个函数之一，
  调用方式 ``fn(text)``；函数接受 ``genre`` 关键字时会一并传入。流水线另给 context：
  ``source``（英文原文）、``document``（{title, subtitle, paragraphs}）、``extra``（社名等配置）、
  ``glossary_dir``（译名表目录，None 为默认），函数接受哪个关键字就传哪个。
- 返回值尽量宽容：issue 列表（dict 或带属性的对象）、``(ok, issues)`` 元组、带 ``issues`` 属性的对象、
  字符串列表都认。每条 issue 归一成 {level, rule, message, where}，level 只取 P0/P1/P2。
- 模块不存在：返回 available=False 的报告，流水线把“lint 未接入”写进复核清单，不中断。
- 模块存在但导入或运行出错：同样不中断，错误写进报告的 note。

level 的含义照 SKILL.md 参数卡：P0 不过不交（流水线带着意见重写一次），P1 须改，P2 建议。
"""

from __future__ import annotations

import importlib
import inspect
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

__all__ = ["DEFAULT_MODULES", "LintIssue", "LintReport", "resolve", "run_lint", "normalize_issues"]

DEFAULT_MODULES = {
    "speech": "tools.lint.speech_lint",
    "newswire": "tools.lint.newswire_lint",
}
CANDIDATE_FUNCS = ("lint_text", "lint", "check_text", "check")
LEVELS = ("P0", "P1", "P2")


@dataclass
class LintIssue:
    level: str
    rule: str
    message: str
    where: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"level": self.level, "rule": self.rule, "message": self.message, "where": self.where}


@dataclass
class LintReport:
    available: bool
    module: str | None
    issues: list[LintIssue] = field(default_factory=list)
    note: str = ""

    @property
    def p0(self) -> list[LintIssue]:
        return [i for i in self.issues if i.level == "P0"]

    @property
    def has_p0(self) -> bool:
        return bool(self.p0)

    def count(self, level: str) -> int:
        return sum(1 for i in self.issues if i.level == level)

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "module": self.module,
            "note": self.note,
            "counts": {level: self.count(level) for level in LEVELS},
            "issues": [i.to_dict() for i in self.issues],
        }


def resolve(module_name: str) -> tuple[Callable[..., Any] | None, str]:
    """找 lint 函数。返回 (函数或 None, 说明)。"""
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        missing = exc.name or ""
        if missing and (module_name == missing or module_name.startswith(missing + ".")):
            return None, f"lint 未接入：模块 {module_name} 不存在"
        return None, f"lint 模块 {module_name} 导入失败：{exc}"
    except Exception as exc:  # noqa: BLE001 - 导入时的任何错误都不该中断生成
        return None, f"lint 模块 {module_name} 导入失败：{exc.__class__.__name__}: {exc}"
    for name in CANDIDATE_FUNCS:
        fn = getattr(module, name, None)
        if callable(fn):
            return fn, f"lint 已接入：{module_name}.{name}"
    return None, f"lint 模块 {module_name} 里没有 {'/'.join(CANDIDATE_FUNCS)} 函数"


def _accepts_kwarg(fn: Callable[..., Any], name: str) -> bool:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    for param in sig.parameters.values():
        if param.kind is inspect.Parameter.VAR_KEYWORD or param.name == name:
            return True
    return False


def _norm_level(raw: Any) -> str:
    if isinstance(raw, bool):
        return "P0" if raw else "P1"
    if isinstance(raw, int):
        return f"P{raw}" if 0 <= raw <= 2 else "P1"
    text = str(raw if raw is not None else "").strip().upper()
    if text in LEVELS:
        return text
    if text.isdigit():
        return f"P{int(text)}" if int(text) <= 2 else "P2"
    if text in ("ERROR", "FATAL", "BLOCK", "BLOCKER", "CRITICAL"):
        return "P0"
    if text in ("WARN", "WARNING", "MAJOR"):
        return "P1"
    if text in ("INFO", "NOTE", "MINOR", "SUGGESTION", "HINT"):
        return "P2"
    if text.startswith("P") and text[1:].isdigit():
        return "P2" if int(text[1:]) >= 2 else text
    return "P1"


def _pick(obj: Any, *names: str, default: str = "") -> str:
    for name in names:
        if isinstance(obj, dict):
            if name in obj and obj[name] is not None:
                return str(obj[name])
        elif hasattr(obj, name):
            value = getattr(obj, name)
            if value is not None and not callable(value):
                return str(value)
    return default


def normalize_issues(raw: Any) -> list[LintIssue]:
    """把各种形态的返回值归一成 LintIssue 列表。"""
    if raw is None:
        return []
    if isinstance(raw, LintReport):
        return list(raw.issues)
    if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[0], bool):
        return normalize_issues(raw[1])
    if isinstance(raw, dict) and "issues" in raw:
        return normalize_issues(raw["issues"])
    if not isinstance(raw, (list, tuple)) and hasattr(raw, "issues"):
        return normalize_issues(getattr(raw, "issues"))
    if isinstance(raw, (list, tuple)):
        out: list[LintIssue] = []
        for item in raw:
            if isinstance(item, LintIssue):
                out.append(item)
            elif isinstance(item, str):
                level = "P1"
                text = item.strip()
                head = text[:2].upper()
                if head in LEVELS:
                    level = head
                    text = text[2:].lstrip(" :：-]")
                elif text[:1] == "[" and text[1:3].upper() in LEVELS:
                    level = text[1:3].upper()
                    text = text[4:].lstrip(" :：-")
                out.append(LintIssue(level=level, rule="", message=text))
            else:
                out.append(
                    LintIssue(
                        level=_norm_level(_pick(item, "level", "severity", "priority", default="P1")),
                        rule=_pick(item, "rule", "code", "id", "name"),
                        message=_pick(item, "message", "msg", "text", "detail", "description"),
                        where=_pick(item, "where", "location", "line", "span", "path"),
                    )
                )
        return out
    return [LintIssue(level="P1", rule="", message=str(raw))]


CONTEXT_KEYS = ("source", "document", "extra", "glossary_dir")


def run_lint(
    text: str,
    *,
    genre: str,
    module_name: str | None = None,
    func: Callable[..., Any] | None = None,
    context: dict[str, Any] | None = None,
) -> LintReport:
    """跑一遍 lint。func 给了就直接用（测试注入），否则按 module_name 或文体默认模块找。

    context 里的 source、document、extra 只传给声明了同名关键字（或 **kwargs）的函数。
    """
    module = module_name or DEFAULT_MODULES.get(genre)
    if func is None:
        if not module:
            return LintReport(available=False, module=None, note=f"lint 未接入：文体 {genre!r} 没有默认模块")
        func, note = resolve(module)
        if func is None:
            return LintReport(available=False, module=module, note=note)
    else:
        module = f"{getattr(func, '__module__', '?')}.{getattr(func, '__name__', 'func')}"
        note = f"lint 已接入：{module}（直接注入）"
    kwargs: dict[str, Any] = {}
    if _accepts_kwarg(func, "genre"):
        kwargs["genre"] = genre
    for key in CONTEXT_KEYS:
        if context and key in context and _accepts_kwarg(func, key):
            kwargs[key] = context[key]
    try:
        raw = func(text, **kwargs)
    except Exception as exc:  # noqa: BLE001 - lint 崩了不该让生成白跑
        detail = traceback.format_exc(limit=3).strip().splitlines()[-1]
        return LintReport(available=False, module=module, note=f"lint 运行出错：{detail}")
    return LintReport(available=True, module=module, issues=normalize_issues(raw), note=note)


def issues_from(items: Iterable[dict]) -> list[LintIssue]:
    """把流水线自己产生的 issue dict（如译名检查）转成 LintIssue。"""
    return [
        LintIssue(
            level=_norm_level(item.get("level", "P1")),
            rule=str(item.get("rule", "")),
            message=str(item.get("message", "")),
            where=str(item.get("where", item.get("english", ""))),
        )
        for item in items
    ]
