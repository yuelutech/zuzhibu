"""3:4 图文卡渲染器：输出 HTML，样式在同目录的 card.css，截图成 1620×2160 的 PNG 交给本机做。

一张卡 540×720（1px 当 1 磅），首页放标题、照片、首段和现任段；履历一律放续页，每条日期一段、
内容一段，条目之间空一段，一页约 23 行，超过就分页；内容行超过约 22 个字宽时在机构名后、
逗号后或括注前断行（rules/cadre.md 第 5 节）。

用法::

    python -m shared.render.card_html 稿件.json -o 输出.html [--caption 配文.txt] [--link-css]

    from shared.render.card_html import render_card_html, paginate, render_caption
    html = render_card_html(doc)            # 一份 HTML 含全部页，样式内联
    pages = paginate(doc)                   # 续页分页结果，供检查

Playwright 截图步骤见 shared/render/README.md，云上不跑。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

from .common import LINE_LIMIT, RenderError, esc, estimate_lines, is_salutation, load_doc, wrap_content

CSS_PATH = Path(__file__).with_name("card.css")

#: 续页最多约 23 行（rules/cadre.md 第 5.3 条）。
MAX_LINES = 23

#: 卡片尺寸（磅）与导出倍数。
CARD_WIDTH = 540
CARD_HEIGHT = 720
EXPORT_SCALE = 3


def paginate(
    source: dict[str, Any] | str | Path, max_lines: int = MAX_LINES, limit: float = LINE_LIMIT
) -> list[list[dict[str, Any]]]:
    """把履历条目、正文段落和尾段分到续页。

    每个块记 height（占几行）：履历条目 = 日期 1 行 + 内容各行；正文段落和尾段按字宽估算。
    块与块之间空 1 行。返回每页的块列表；没有可分的内容时返回空列表。
    """
    doc = load_doc(source)
    blocks: list[dict[str, Any]] = []
    for entry in doc["careers"]:
        lines = wrap_content(entry, limit)
        blocks.append(
            {
                "kind": "entry",
                "period": entry["period"],
                "lines": lines,
                "height": 1 + sum(estimate_lines(line, limit) for line in lines),
            }
        )
    for text in doc["paragraphs"]:
        blocks.append({"kind": "para", "text": text, "height": estimate_lines(text, limit)})
    if doc["closing"]:
        blocks.append({"kind": "closing", "text": doc["closing"], "height": estimate_lines(doc["closing"], limit)})

    pages: list[list[dict[str, Any]]] = []
    page: list[dict[str, Any]] = []
    used = 0
    for block in blocks:
        need = block["height"] + (1 if page else 0)
        if page and used + need > max_lines:
            pages.append(page)
            page, used = [], 0
            need = block["height"]
        page.append(block)
        used += need
    if page:
        pages.append(page)
    return pages


def render_cover(doc: dict[str, Any]) -> str:
    """首页：标题、照片、首段、现任段。"""
    parts = ['<section class="card card-cover" data-page="1">', f'<h1 class="card-title">{esc(doc["title"])}</h1>']
    if doc["subtitle"]:
        parts.append(f'<p class="card-subtitle">{esc(doc["subtitle"])}</p>')
    if doc["photo"]:
        credit = f'<figcaption class="card-credit">{esc(doc["photo_credit"])}</figcaption>' if doc["photo_credit"] else ""
        parts.append(f'<figure class="card-photo"><img src="{esc(doc["photo"])}" alt="">{credit}</figure>')
    if doc["lead"] or doc["current"]:
        parts.append('<div class="card-body">')
        if doc["lead"]:
            parts.append(f'<p class="card-para">{esc(doc["lead"])}</p>')
        if doc["current"]:
            parts.append(f'<p class="card-para">{esc(doc["current"])}</p>')
        parts.append("</div>")
    parts.append("</section>")
    return "\n".join(parts)


def render_continuation(blocks: list[dict[str, Any]], page_no: int) -> str:
    """续页：整页高的文本框，垂直居中，段落居中。"""
    parts = [f'<section class="card card-cont" data-page="{page_no}">', '<div class="card-entries">']
    for block in blocks:
        if block["kind"] == "entry":
            parts.append('<div class="card-block card-entry">')
            parts.append(f'<p class="card-date">{esc(block["period"])}</p>')
            parts.append(f'<p class="card-text">{"<br>".join(esc(line) for line in block["lines"])}</p>')
            parts.append("</div>")
        elif block["kind"] == "para":
            css = "card-block card-para card-salutation" if is_salutation(block["text"]) else "card-block card-para"
            parts.append(f'<p class="{css}">{esc(block["text"])}</p>')
        else:
            parts.append(f'<p class="card-block card-closing">{esc(block["text"])}</p>')
    parts.append("</div>")
    parts.append("</section>")
    return "\n".join(parts)


def render_card_html(
    source: dict[str, Any] | str | Path,
    *,
    inline_css: bool = True,
    css_href: str = "card.css",
    max_lines: int = MAX_LINES,
    limit: float = LINE_LIMIT,
) -> str:
    """渲染整份 HTML：首页加若干续页。缺省把 card.css 内联进去，一个文件就能预览和截图。"""
    doc = load_doc(source)
    if inline_css:
        style = f"<style>\n{CSS_PATH.read_text(encoding='utf-8').rstrip()}\n</style>"
    else:
        style = f'<link rel="stylesheet" href="{esc(css_href)}">'
    sections = [render_cover(doc)]
    for index, blocks in enumerate(paginate(doc, max_lines=max_lines, limit=limit), start=2):
        sections.append(render_continuation(blocks, index))
    body = "\n".join(sections)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        f'<meta name="viewport" content="width={CARD_WIDTH}">\n'
        f"<title>{esc(doc['title'])}</title>\n"
        f"{style}\n"
        "</head>\n"
        "<body>\n"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


def render_caption(source: dict[str, Any] | str | Path) -> str:
    """配文 txt（rules/cadre.md 第 6.2 条）：标题、空行、首段原文、空行、话题标签。"""
    doc = load_doc(source)
    tags: list[str] = []
    for tag in ["AI政见", doc["name"], *doc["tags"], "干部简历"]:
        if tag and tag not in tags:
            tags.append(tag)
    blocks = [doc["title"]]
    if doc["lead"]:
        blocks.append(doc["lead"])
    blocks.append(" ".join(f"#{tag}" for tag in tags))
    return "\n\n".join(blocks) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把稿件 JSON 渲染成 3:4 图文卡的 HTML。")
    parser.add_argument("source", help="稿件 JSON 文件")
    parser.add_argument("-o", "--output", help="输出 HTML 文件；缺省打印到标准输出")
    parser.add_argument("--caption", metavar="TXT", help="同时写出配文 txt")
    parser.add_argument("--link-css", action="store_true", help="不内联样式，改为链接同目录的 card.css（会把 card.css 拷到输出目录）")
    parser.add_argument("--max-lines", type=int, default=MAX_LINES, help=f"续页最多几行，缺省 {MAX_LINES}")
    parser.add_argument("--limit", type=float, default=LINE_LIMIT, help=f"一行约几个字宽，缺省 {LINE_LIMIT}")
    args = parser.parse_args(argv)
    try:
        html = render_card_html(args.source, inline_css=not args.link_css, max_lines=args.max_lines, limit=args.limit)
        caption = render_caption(args.source) if args.caption else ""
    except RenderError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    if args.output:
        output = Path(args.output)
        output.write_text(html, encoding="utf-8")
        if args.link_css:
            target = output.parent / CSS_PATH.name
            if target.resolve() != CSS_PATH.resolve():
                shutil.copyfile(CSS_PATH, target)
    else:
        sys.stdout.write(html)
    if args.caption:
        Path(args.caption).write_text(caption, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
