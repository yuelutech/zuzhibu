"""公众号 HTML 渲染器：全部行内样式，可直接粘进公众号编辑器。

版式自上而下：副题、浅蓝提要框、居中照片、首段、现任段、带缩进的履历行、正文段落、尾段、
固定的“写在最后”段。不做公众号自动发布。

用法::

    python -m shared.render.article_html 稿件.json -o 输出.html        # 带预览外壳的整页
    python -m shared.render.article_html 稿件.json --fragment          # 只打印可粘贴的正文片段

    from shared.render.article_html import render_article_html, render_article_page
    fragment = render_article_html({"title": "××简历", "lead": "……", "careers": [...]})
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .common import RenderError, attr_style, esc, first_sentence, is_heading, is_salutation, load_doc

#: “写在最后”段的标题和默认文字。导出物只留正文，默认不加；稿件对象的 afterword 字段给了文字才加。
AFTERWORD_TITLE = "写在最后"
AFTERWORD_DEFAULT: list[str] = []

ACCENT = "#3771BB"  # 与 3:4 图文卡底色同一蓝
BOX_BG = "#EAF3FB"  # 浅蓝提要框底色

#: 各部件的行内样式。只用公众号编辑器保留得住的属性：颜色、字号、行距、边距、对齐、边框。
STYLES = {
    "wrap": "max-width:677px;margin:0 auto;padding:0;font-size:16px;line-height:1.75;color:#333333;letter-spacing:0.5px;",
    "subtitle": "margin:0 0 16px 0;text-align:center;color:#888888;font-size:14px;",
    "subtitle_tight": "margin:0 0 4px 0;text-align:center;color:#888888;font-size:14px;",
    "byline": "margin:0 0 4px 0;text-align:center;color:#888888;font-size:14px;",
    "byline_last": "margin:0 0 16px 0;text-align:center;color:#888888;font-size:14px;",
    "box": (
        f"background-color:{BOX_BG};border-left:4px solid {ACCENT};border-radius:4px;padding:12px 16px;"
        "margin:0 0 24px 0;color:#2B4A6F;font-size:15px;line-height:1.7;text-align:justify;"
    ),
    "photo_wrap": "text-align:center;margin:0 0 24px 0;",
    "photo": "display:inline-block;width:60%;max-width:300px;height:auto;vertical-align:top;",
    "photo_credit": "margin:6px 0 0 0;font-size:12px;color:#888888;line-height:1.5;text-align:center;",
    "para": "margin:0 0 16px 0;text-indent:2em;text-align:justify;",
    "salutation": "margin:0 0 16px 0;text-align:left;",
    "heading": "margin:24px 0 16px 0;text-indent:2em;text-align:left;font-weight:bold;",
    "careers": "margin:8px 0 24px 0;",
    "career": "margin:0 0 8px 2em;text-align:left;line-height:1.7;",
    "career_period": "margin-right:1em;white-space:nowrap;",
    "afterword_title": (
        f"margin:32px 0 12px 0;padding-left:10px;border-left:4px solid {ACCENT};"
        f"font-size:16px;font-weight:bold;color:{ACCENT};line-height:1.4;"
    ),
    "afterword": "margin:0 0 10px 0;font-size:14px;color:#888888;line-height:1.7;text-align:justify;",
}


def digest_text(doc: dict[str, Any]) -> str:
    """提要框文字：提要 summary，缺省取现任段，再缺省取首段第一句。"""
    return doc["summary"] or doc["current"] or first_sentence(doc["lead"])


def afterword_paragraphs(doc: dict[str, Any]) -> list[str]:
    """“写在最后”的段落：缺省用 AFTERWORD_DEFAULT（现为空，即不加）；给了字符串或列表就用它；空字符串表示不要这一段。"""
    afterword = doc["afterword"]
    if afterword is None:
        return list(AFTERWORD_DEFAULT)
    if isinstance(afterword, str):
        return [afterword] if afterword else []
    return list(afterword)


def _p(text: str, style_key: str) -> str:
    return f"<p {attr_style(STYLES[style_key])}>{esc(text)}</p>"


def render_article_html(source: dict[str, Any] | str | Path) -> str:
    """渲染可粘贴进公众号编辑器的正文片段（不含标题，标题在编辑器里单独填）。"""
    doc = load_doc(source)
    parts = [f"<section {attr_style(STYLES['wrap'])}>"]

    if doc["subtitle"]:
        parts.append(_p(doc["subtitle"], "subtitle" if not doc["byline"] else "subtitle_tight"))
    for index, line in enumerate(doc["byline"]):
        parts.append(_p(line, "byline_last" if index == len(doc["byline"]) - 1 else "byline"))

    digest = digest_text(doc)
    if digest:
        parts.append(f"<section {attr_style(STYLES['box'])}>{esc(digest)}</section>")

    if doc["photo"]:
        parts.append(
            f"<section {attr_style(STYLES['photo_wrap'])}>"
            f"<img src=\"{esc(doc['photo'])}\" alt=\"{esc(doc['name'])}\" {attr_style(STYLES['photo'])}>"
            + (f"<p {attr_style(STYLES['photo_credit'])}>{esc(doc['photo_credit'])}</p>" if doc["photo_credit"] else "")
            + "</section>"
        )

    if doc["lead"]:
        parts.append(_p(doc["lead"], "para"))
    if doc["current"]:
        parts.append(_p(doc["current"], "para"))

    if doc["careers"]:
        lines = "".join(
            f"<p {attr_style(STYLES['career'])}>"
            f"<span {attr_style(STYLES['career_period'])}>{esc(entry['period'])}</span>{esc(entry['content'])}</p>"
            for entry in doc["careers"]
        )
        parts.append(f"<section {attr_style(STYLES['careers'])}>{lines}</section>")

    for text in doc["paragraphs"]:
        parts.append(_p(text, "salutation" if is_salutation(text) else "heading" if is_heading(text) else "para"))

    if doc["closing"]:
        parts.append(_p(doc["closing"], "para"))

    afterword = afterword_paragraphs(doc)
    if afterword:
        parts.append(f"<section {attr_style(STYLES['afterword_title'])}>{esc(AFTERWORD_TITLE)}</section>")
        parts.extend(_p(text, "afterword") for text in afterword)

    parts.append("</section>")
    return "\n".join(parts) + "\n"


def render_article_page(source: dict[str, Any] | str | Path) -> str:
    """整页 HTML：浏览器预览用的外壳加上正文片段。外壳也只用行内样式，整份文件没有 <style>。"""
    doc = load_doc(source)
    fragment = render_article_html(doc)
    body_style = (
        "margin:0;padding:24px 16px;background:#F2F2F2;"
        "font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;"
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(doc['title'])}</title>\n"
        "</head>\n"
        f"<body {attr_style(body_style)}>\n"
        f"<div {attr_style('max-width:677px;margin:0 auto;background:#FFFFFF;padding:24px 20px;')}>\n"
        f"<h1 {attr_style('font-size:22px;line-height:1.4;margin:0 0 20px 0;color:#222222;')}>{esc(doc['title'])}</h1>\n"
        "<!-- 下面从 <section 到最后一个 </section> 是可粘贴进公众号编辑器的正文；照片要在编辑器里重新上传 -->\n"
        f"{fragment}"
        "</div>\n"
        "</body>\n"
        "</html>\n"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把稿件 JSON 渲染成全部行内样式的公众号 HTML。")
    parser.add_argument("source", help="稿件 JSON 文件")
    parser.add_argument("-o", "--output", help="输出 HTML 文件；缺省打印到标准输出")
    parser.add_argument("--fragment", action="store_true", help="只输出可粘贴的正文片段，不带预览外壳")
    args = parser.parse_args(argv)
    try:
        html = render_article_html(args.source) if args.fragment else render_article_page(args.source)
    except RenderError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    if args.output:
        Path(args.output).write_text(html, encoding="utf-8")
    else:
        sys.stdout.write(html)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
