"""pipeline：秘书局、宣传教育局共用的顺序流水线。

    读英文原文 → 译名表匹配 → 组提示词（写法规范 + 译名表 + 原文）→ 调本机 codex exec
    → lint（tools/lint 调用点；P0 不过带着意见重写一次，仍不过标“交人工”）→ 输出 Markdown 与 JSON
    → 渲染 docx 红头件和公众号 HTML（shared/render）

两个局各自只提供一个 BureauSpec：文体、写法规范文件、输出 schema、提示词模板、稿件版式和渲染对象；
其余都在这里。只用标准库，一个进程顺序执行，不联网（联网检索在后端里关掉）。

产物（都在 --out 目录下，默认 output/<局>/，已被 .gitignore 覆盖）：

- <原文名>.md         成稿：YAML 头 + 正文 + 复核清单
- <原文名>.json       稿件对象（供渲染层）+ 渲染对象 + 原始草稿 + 译名、lint、后端记录
- <原文名>.redhead.docx  红头件（--no-render 不出）
- <原文名>.article.html   公众号 HTML 整页，正文片段在注释标出的 <section> 里（--no-render 不出）
- <原文名>.prompt.md  实际发给模型的提示词（--dry-run 也会写）
- <原文名>.glossary.json 译名表命中
- runs/<时间戳>-<原文名>-<id>/  后端过程记录（见 codex_cli）
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Sequence

from . import codex_cli, lint_hook
from .glossary import Glossary, Match, render_prompt_table

__all__ = [
    "REPO_ROOT",
    "SKILLS_DIR",
    "PromptContext",
    "DraftBody",
    "BureauSpec",
    "RunOptions",
    "PipelineResult",
    "strip_frontmatter",
    "read_source",
    "count_words",
    "run_bureau",
    "add_common_args",
    "options_from_args",
    "main_for",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "shared" / "skills"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.S)
_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'’\-]*")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class PromptContext:
    """组提示词时能用到的全部材料。"""

    source_text: str
    source_name: str
    word_count: int
    date: _dt.date
    matches: list[Match]
    skill_text: str
    extra: dict[str, str] = field(default_factory=dict)
    rewrite_feedback: str = ""

    @property
    def date_cn(self) -> str:
        """电头用：10月15日（不补零）。"""
        return f"{self.date.month}月{self.date.day}日"

    @property
    def date_full_cn(self) -> str:
        """日期行用：2026年10月15日。"""
        return f"{self.date.year}年{self.date.month}月{self.date.day}日"

    @property
    def glossary_table(self) -> str:
        return render_prompt_table(self.matches)

    @property
    def pending_glossary(self) -> list[Match]:
        return [m for m in self.matches if m.entry.needs_review]


@dataclass
class DraftBody:
    """稿件在文章页里的样子：标题、副题、顺序段落、落款。lint 和渲染层都从这里取。"""

    title: str
    subtitle: str
    paragraphs: list[str]
    signoff: str = ""

    def plain_text(self) -> str:
        """给 lint 的全文：标题、副题、段落、落款，空行隔开。"""
        head = [line for line in (self.title.strip(), self.subtitle.strip()) if line]
        parts = head + [p.strip() for p in self.paragraphs if p and p.strip()]
        if self.signoff.strip():
            parts.append(self.signoff.strip())
        return "\n\n".join(parts) + "\n"

    def body_text(self) -> str:
        """不含标题、副题的正文：译名首次提及规则不管标题（标题里可用简称）。"""
        parts = [p.strip() for p in self.paragraphs if p and p.strip()]
        if self.signoff.strip():
            parts.append(self.signoff.strip())
        return "\n\n".join(parts) + "\n"

    def markdown(self) -> str:
        lines: list[str] = []
        if self.title.strip():
            lines.append(f"# {self.title.strip()}")
            lines.append("")
        if self.subtitle.strip():
            lines.append(f"## {self.subtitle.strip()}")
            lines.append("")
        for p in self.paragraphs:
            if p and p.strip():
                lines.append(p.strip())
                lines.append("")
        if self.signoff.strip():
            lines.append(self.signoff.strip())
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


@dataclass
class BureauSpec:
    bureau: str
    bureau_zh: str
    genre: str
    genre_zh: str
    skill_path: Path
    schema_path: Path
    build_prompt: Callable[[PromptContext], str]
    build_body: Callable[[dict, PromptContext], DraftBody]
    lint_module: str | None = None
    build_render: Callable[[dict, PromptContext, DraftBody], dict] | None = None
    """草稿 → shared/render 的稿件对象（title、subtitle、byline、paragraphs、redhead）。None 时不渲染。"""

    @property
    def default_lint_module(self) -> str | None:
        return self.lint_module or lint_hook.DEFAULT_MODULES.get(self.genre)


@dataclass
class RunOptions:
    source_path: Path
    out_dir: Path
    date: _dt.date
    codex: codex_cli.CodexConfig
    dry_run: bool = False
    lint_module: str | None = None
    lint_func: Callable[..., Any] | None = None
    no_lint: bool = False
    max_rewrites: int = 1
    extra: dict[str, str] = field(default_factory=dict)
    glossary: Glossary | None = None
    glossary_dir: Path | None = None
    render: bool = True
    render_fonts: dict[str, str] | None = None
    render_layout: dict[str, Any] | None = None


@dataclass
class PipelineResult:
    status: str
    """ok | needs-review | dry-run | failed"""
    message: str
    markdown_path: Path | None = None
    json_path: Path | None = None
    prompt_path: Path | None = None
    run_dirs: list[Path] = field(default_factory=list)
    matches: list[Match] = field(default_factory=list)
    lint: lint_hook.LintReport | None = None
    glossary_issues: list[dict] = field(default_factory=list)
    rewrites: int = 0
    draft: dict | None = None
    body: DraftBody | None = None
    docx_path: Path | None = None
    html_path: Path | None = None
    render_note: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("ok", "dry-run")


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def strip_frontmatter(text: str) -> str:
    """去掉 SKILL.md 开头的 YAML 头，只留正文。"""
    return _FRONTMATTER_RE.sub("", text, count=1)


def read_source(path: Path | str) -> str:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"找不到原文：{path}")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_json(path: Path, data: Any) -> None:
    _write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _load_schema(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "；".join(_as_str(v) for v in value)
    return str(value)


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def run_bureau(spec: BureauSpec, options: RunOptions) -> PipelineResult:
    """跑一篇。失败不抛后端异常，返回 status=failed 的结果并把原因写进 message。"""
    source_text = read_source(options.source_path)
    stem = options.source_path.stem
    out_dir = Path(options.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if options.glossary is not None:
        glossary = options.glossary
    elif options.glossary_dir:
        glossary = Glossary.load(options.glossary_dir)
    else:
        glossary = Glossary.load()
    matches = glossary.match(source_text)

    skill_text = strip_frontmatter(spec.skill_path.read_text(encoding="utf-8"))
    ctx = PromptContext(
        source_text=source_text,
        source_name=options.source_path.name,
        word_count=count_words(source_text),
        date=options.date,
        matches=matches,
        skill_text=skill_text,
        extra=dict(options.extra),
    )
    prompt = spec.build_prompt(ctx)
    schema = _load_schema(spec.schema_path)

    prompt_path = out_dir / f"{stem}.prompt.md"
    _write_text(prompt_path, prompt)
    glossary_path = out_dir / f"{stem}.glossary.json"
    _write_json(glossary_path, {"source": options.source_path.name, "matches": [m.to_dict() for m in matches]})

    if options.dry_run:
        preview = codex_cli.build_command(
            options.codex,
            cwd=Path("<run_dir>/sandbox"),
            schema_path=Path("<run_dir>/schema.json"),
            last_message_path=Path("<run_dir>/last_message.txt"),
        )
        _write_text(out_dir / f"{stem}.command.txt", " ".join(preview) + "  < " + str(prompt_path) + "\n")
        return PipelineResult(
            status="dry-run",
            message=f"已生成提示词 {prompt_path}（命中译名表 {len(matches)} 条），未调用后端。",
            prompt_path=prompt_path,
            matches=matches,
        )

    runs_dir = out_dir / "runs"
    run_dirs: list[Path] = []
    lint_module = options.lint_module or spec.default_lint_module
    rewrites = 0
    draft: dict | None = None
    body: DraftBody | None = None
    lint: lint_hook.LintReport | None = None
    glossary_issues: list[dict] = []
    codex_meta: dict[str, Any] = {}
    elapsed = 0.0

    while True:
        label = stem if rewrites == 0 else f"{stem}-rewrite{rewrites}"
        try:
            result = codex_cli.run(prompt, schema, options.codex, runs_dir, label=label)
        except codex_cli.CodexError as exc:
            if exc.run_dir:
                run_dirs.append(exc.run_dir)
            return PipelineResult(
                status="failed",
                message=f"后端调用失败：{exc}" + (f"（过程记录 {exc.run_dir}）" if exc.run_dir else ""),
                prompt_path=prompt_path,
                run_dirs=run_dirs,
                matches=matches,
                rewrites=rewrites,
            )
        run_dirs.append(result.run_dir)
        draft = result.data
        elapsed += sum(a.duration for a in result.attempts)
        codex_meta = {
            "run_dir": str(result.run_dir),
            "attempts": result.attempts_used,
            "usage": result.usage,
            "thread_id": result.thread_id,
            "codex_version": result.codex_version,
            "model": options.codex.model,
            "reasoning_effort": options.codex.reasoning_effort,
            "ignore_user_config": options.codex.ignore_user_config,
            "seconds": round(sum(a.duration for a in result.attempts), 1),
            "seconds_total": round(elapsed, 1),
        }
        body = spec.build_body(draft, ctx)
        text = body.plain_text()
        glossary_issues = glossary.check_output(body.body_text(), matches)
        if options.no_lint:
            lint = lint_hook.LintReport(available=False, module=lint_module, note="lint 已按 --no-lint 跳过")
        else:
            lint_context = {
                "source": source_text,
                "document": {"title": body.title, "subtitle": body.subtitle, "paragraphs": list(body.paragraphs)},
                "extra": dict(ctx.extra),
                "glossary_dir": str(options.glossary_dir) if options.glossary_dir else None,
            }
            lint = lint_hook.run_lint(text, genre=spec.genre, module_name=lint_module, func=options.lint_func, context=lint_context)
        if not lint.has_p0 or rewrites >= options.max_rewrites:
            break
        rewrites += 1
        ctx.rewrite_feedback = _rewrite_feedback(lint, glossary_issues, draft)
        prompt = spec.build_prompt(ctx) + "\n\n" + ctx.rewrite_feedback
        _write_text(out_dir / f"{stem}.rewrite{rewrites}.prompt.md", prompt)

    assert draft is not None and body is not None and lint is not None
    status = "needs-review" if lint.has_p0 else "ok"
    meta = _build_meta(spec, options, ctx, lint, glossary_issues, codex_meta, rewrites, status)
    markdown = _render_markdown(meta, body, ctx, draft, lint, glossary_issues)
    markdown_path = out_dir / f"{stem}.md"
    _write_text(markdown_path, markdown)
    render_doc = spec.build_render(draft, ctx, body) if spec.build_render else None
    json_path = out_dir / f"{stem}.json"
    _write_json(
        json_path,
        {
            "meta": meta,
            "document": {
                "genre": spec.genre,
                "genre_zh": spec.genre_zh,
                "title": body.title,
                "subtitle": body.subtitle,
                "paragraphs": body.paragraphs,
                "signoff": body.signoff,
                "sources": [{"name": options.source_path.name, "words": ctx.word_count}],
            },
            "render": render_doc,
            "config": {"extra": dict(options.extra), "date": options.date.isoformat()},
            "draft": draft,
            "glossary": {"matches": [m.to_dict() for m in matches], "issues": glossary_issues},
            "lint": lint.to_dict(),
            "codex": codex_meta,
        },
    )
    docx_path = html_path = None
    render_note = ""
    if options.render and render_doc is not None:
        docx_path, html_path, render_note = _render_outputs(render_doc, out_dir, stem, options)
    if status == "ok":
        message = f"已生成 {markdown_path}"
    else:
        message = f"已生成 {markdown_path}，但 lint 仍有 {len(lint.p0)} 条 P0，交人工"
    if render_note:
        message += f"；{render_note}"
    return PipelineResult(
        status=status,
        message=message,
        markdown_path=markdown_path,
        json_path=json_path,
        prompt_path=prompt_path,
        run_dirs=run_dirs,
        matches=matches,
        lint=lint,
        glossary_issues=glossary_issues,
        rewrites=rewrites,
        draft=draft,
        body=body,
        docx_path=docx_path,
        html_path=html_path,
        render_note=render_note,
    )


def _render_outputs(render_doc: dict, out_dir: Path, stem: str, options: RunOptions) -> tuple[Path | None, Path | None, str]:
    """渲染红头件和公众号 HTML。渲染失败不影响成稿，原因写进返回的说明。"""
    from shared.render import docx_redhead, article_html
    from shared.render.common import RenderError

    try:
        docx_path = docx_redhead.render_docx(
            render_doc, out_dir / f"{stem}.redhead.docx", options.render_fonts, layout=options.render_layout
        )
        html_path = out_dir / f"{stem}.article.html"
        _write_text(html_path, article_html.render_article_page(render_doc))
    except (RenderError, OSError) as exc:
        return None, None, f"渲染失败：{exc}"
    return docx_path, html_path, ""


def _rewrite_feedback(lint: lint_hook.LintReport, glossary_issues: list[dict], draft: dict) -> str:
    lines = ["## 上一稿检查意见", "", "上一稿有下列问题。P0 必须全部修正；其余尽量修正。修正后重新输出完整的 JSON 对象，不要只输出改动部分。", ""]
    for issue in lint.issues:
        rule = f"{issue.rule}：" if issue.rule else ""
        lines.append(f"- [{issue.level}] {rule}{issue.message}")
    for issue in glossary_issues:
        lines.append(f"- [{issue.get('level', 'P1')}] {issue.get('rule', '')}：{issue.get('message', '')}")
    lines += ["", "## 上一稿（供修改）", "", "```json", json.dumps(draft, ensure_ascii=False, indent=2), "```"]
    return "\n".join(lines)


def _build_meta(
    spec: BureauSpec,
    options: RunOptions,
    ctx: PromptContext,
    lint: lint_hook.LintReport,
    glossary_issues: list[dict],
    codex_meta: dict[str, Any],
    rewrites: int,
    status: str,
) -> dict[str, Any]:
    pending = len(ctx.pending_glossary)
    g_counts = {lvl: sum(1 for i in glossary_issues if i.get("level") == lvl) for lvl in ("P1", "P2")}
    return {
        "文种": spec.genre_zh,
        "局": spec.bureau_zh,
        "原文": ctx.source_name,
        "原文词数": ctx.word_count,
        "日期": ctx.date.isoformat(),
        "后端": "codex exec" + (
            "（" + "，".join(x for x in (codex_meta.get("model"), codex_meta.get("reasoning_effort")) if x) + "）"
            if codex_meta.get("model") or codex_meta.get("reasoning_effort") else ""
        ),
        "尝试次数": codex_meta.get("attempts"),
        "重写次数": rewrites,
        "后端耗时秒": codex_meta.get("seconds_total"),
        "译名表命中": len(ctx.matches),
        "译名表待核": pending,
        "lint": lint.note,
        "lint计数": {lvl: lint.count(lvl) for lvl in lint_hook.LEVELS},
        "译名检查": g_counts,
        "状态": status,
        "过程记录": codex_meta.get("run_dir"),
    }


def _render_markdown(
    meta: dict[str, Any],
    body: DraftBody,
    ctx: PromptContext,
    draft: dict,
    lint: lint_hook.LintReport,
    glossary_issues: list[dict],
) -> str:
    head = ["---"]
    for key, value in meta.items():
        if isinstance(value, dict):
            value = "、".join(f"{k} {v}" for k, v in value.items())
        head.append(f"{key}: {value}")
    head.append("---")
    head.append("")

    review: list[str] = ["---", "", "## 复核清单（不随稿件发布）", ""]
    review.append(f"- 状态：{meta['状态']}（P0 为 0 才算通过；needs-review 表示重写一次后仍有 P0，交人工）")
    review.append(f"- lint：{lint.note}")
    review.append("")

    review.append("### 译名表命中")
    review.append("")
    if ctx.matches:
        review.append("| 原文 | 类别 | 首次提及 | 再次提及 | 状态 |")
        review.append("|---|---|---|---|---|")
        for m in ctx.matches:
            review.append(
                f"| {m.matched_text} | {m.entry.kind_zh} | {m.entry.first} | {m.entry.later} | {m.entry.status or '待核'} |"
            )
    else:
        review.append("（无）")
    review.append("")

    misses = _as_list(draft.get("glossary_misses"))
    review.append("### 表外专名（模型报告，须人工核）")
    review.append("")
    if misses:
        for item in misses:
            if isinstance(item, dict):
                review.append(
                    f"- {_as_str(item.get('english'))} → {_as_str(item.get('zh'))}"
                    + (f"（{_as_str(item.get('reason'))}）" if item.get("reason") else "")
                )
            else:
                review.append(f"- {_as_str(item)}")
    else:
        review.append("（无）")
    review.append("")

    numbers = _as_list(draft.get("numbers"))
    review.append("### 数字对照")
    review.append("")
    if numbers:
        review.append("| 成稿 | 原文 |")
        review.append("|---|---|")
        for item in numbers:
            if isinstance(item, dict):
                review.append(f"| {_as_str(item.get('zh'))} | {_as_str(item.get('source'))} |")
    else:
        review.append("（无）")
    review.append("")

    quotes = _as_list(draft.get("quotes"))
    review.append("### 引语对照")
    review.append("")
    if quotes:
        review.append("| 成稿 | 原文 |")
        review.append("|---|---|")
        for item in quotes:
            if isinstance(item, dict):
                review.append(f"| {_as_str(item.get('zh'))} | {_as_str(item.get('source'))} |")
    else:
        review.append("（无）")
    review.append("")

    review.append("### 检查意见")
    review.append("")
    combined = [(i.level, i.rule, i.message) for i in lint.issues] + [
        (str(i.get("level", "P1")), str(i.get("rule", "")), str(i.get("message", ""))) for i in glossary_issues
    ]
    if combined:
        for level, rule, message in sorted(combined, key=lambda x: x[0]):
            review.append(f"- [{level}] {rule + '：' if rule else ''}{message}")
    else:
        review.append("（无）")
    review.append("")

    notes = _as_str(draft.get("review_notes"))
    if notes.strip():
        review.append("### 模型说明")
        review.append("")
        review.append(notes.strip())
        review.append("")

    return "\n".join(head) + "\n" + body.markdown() + "\n" + "\n".join(review).rstrip() + "\n"


# ---------------------------------------------------------------------------
# 命令行公共部分
# ---------------------------------------------------------------------------


def add_common_args(parser: argparse.ArgumentParser, *, bureau: str) -> None:
    parser.add_argument("source", help="英文原文文件（UTF-8 文本）")
    parser.add_argument("-o", "--out", default=f"output/{bureau}", help="输出目录（默认 output/<局>/，已被 .gitignore 忽略）")
    parser.add_argument("--date", help="稿件日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--codex", default="codex",
                        help="codex 可执行程序（默认在 PATH 里找 codex，找不到再找 Windows 桌面版自带的 codex.exe）")
    parser.add_argument("--model", help="传给 codex -m 的模型名；不给则取本机 config.toml 的 model")
    parser.add_argument("--reasoning-effort", help="推理强度（-c model_reasoning_effort）；不给则取本机 config.toml 的设置")
    parser.add_argument("--use-user-config", action="store_true",
                        help="加载本机 config.toml 全部配置（默认不加载：MCP、插件、连接器、钩子不带进改写任务）")
    parser.add_argument("--keep-tools", action="store_true",
                        help="不关模型的工具（默认关掉 apps 连接器里的联网搜索、shell、补丁、看图、生图等，见 codex_cli.DEFAULT_DISABLED_FEATURES）")
    parser.add_argument("--timeout", type=float, default=600.0, help="单次调用超时秒数（默认 600）")
    parser.add_argument("--retries", type=int, default=2, help="失败后最多重试次数（默认 2）")
    parser.add_argument("--web-search", choices=[*codex_cli.WEB_SEARCH_MODES, "inherit"], default="disabled",
                        help="联网检索开关（默认 disabled；inherit 沿用本机配置）")
    parser.add_argument("--no-ephemeral", action="store_true", help="不传 --ephemeral（老版本 Codex 不认时用）")
    parser.add_argument("--codex-arg", action="append", default=[], metavar="ARG",
                        help="原样追加给 codex exec 的参数，可重复")
    parser.add_argument("--dry-run", action="store_true", help="只做译名表匹配和提示词，不调用后端")
    parser.add_argument("--lint-module", help=f"lint 模块名，默认按文体找 tools.lint.<文体>_lint")
    parser.add_argument("--no-lint", action="store_true", help="跳过 lint")
    parser.add_argument("--max-rewrites", type=int, default=1, help="lint 有 P0 时最多重写几次（默认 1）")
    parser.add_argument("--glossary-dir", help="译名表目录，默认 shared/glossary")
    parser.add_argument("--no-render", action="store_true", help="不渲染红头件和公众号 HTML")
    parser.add_argument("--local-fonts", action="store_true",
                        help="红头件用本机字体名：方正小标宋简体、仿宋_GB2312、黑体、楷体_GB2312、宋体、Times New Roman")
    parser.add_argument("--line-pitch", type=float, help="红头件正文固定行距（磅），默认按渲染器 LAYOUT")


def options_from_args(args: argparse.Namespace, extra: dict[str, str]) -> RunOptions:
    date = _dt.date.fromisoformat(args.date) if args.date else _dt.date.today()
    model, effort = args.model, args.reasoning_effort
    if not args.use_user_config and (not model or not effort):
        defaults = codex_cli.user_defaults()
        model = model or defaults.get("model")
        effort = effort or defaults.get("model_reasoning_effort")
    config = codex_cli.CodexConfig(
        command=(args.codex,),
        model=model,
        reasoning_effort=effort,
        ignore_user_config=not args.use_user_config,
        disabled_features=() if args.keep_tools else codex_cli.DEFAULT_DISABLED_FEATURES,
        timeout=args.timeout,
        retries=args.retries,
        web_search=None if args.web_search == "inherit" else args.web_search,
        ephemeral=not args.no_ephemeral,
        extra_args=tuple(args.codex_arg),
    )
    from shared.render import docx_redhead

    return RunOptions(
        source_path=Path(args.source),
        out_dir=Path(args.out),
        date=date,
        codex=config,
        dry_run=args.dry_run,
        lint_module=args.lint_module,
        no_lint=args.no_lint,
        max_rewrites=args.max_rewrites,
        extra=extra,
        glossary_dir=Path(args.glossary_dir) if args.glossary_dir else None,
        render=not args.no_render,
        render_fonts=dict(docx_redhead.LOCAL_FONTS) if args.local_fonts else None,
        render_layout={"line_pitch": args.line_pitch} if args.line_pitch else None,
    )


def main_for(
    spec: BureauSpec,
    argv: Sequence[str] | None,
    *,
    description: str,
    add_args: Callable[[argparse.ArgumentParser], None] | None = None,
    extra_from_args: Callable[[argparse.Namespace], dict[str, str]] | None = None,
) -> int:
    """各局 run.py 的 main：解析参数、跑流水线、打印结果。退出码 0 成功，2 交人工，1 失败。"""
    parser = argparse.ArgumentParser(description=description)
    add_common_args(parser, bureau=spec.bureau)
    if add_args:
        add_args(parser)
    args = parser.parse_args(argv)
    try:
        extra = extra_from_args(args) if extra_from_args else {}
        options = options_from_args(args, extra)
        result = run_bureau(spec, options)
    except (FileNotFoundError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    print(result.message)
    if result.status == "dry-run":
        print(f"命令行预览：{Path(args.out) / (Path(args.source).stem + '.command.txt')}")
        for m in result.matches:
            print(f"  译名表：{m.matched_text} → {m.entry.first}（再提 {m.entry.later}，{m.entry.status or '待核'}）")
        return 0
    if result.status == "failed":
        return 1
    if result.lint is not None:
        print(f"lint：{result.lint.note}；P0 {result.lint.count('P0')}，P1 {result.lint.count('P1')}，P2 {result.lint.count('P2')}")
    if result.glossary_issues:
        print(f"译名检查：{len(result.glossary_issues)} 条，见复核清单")
    for label, path in (("红头件", result.docx_path), ("公众号 HTML", result.html_path)):
        if path:
            print(f"{label}：{path}")
    return 0 if result.status == "ok" else 2
