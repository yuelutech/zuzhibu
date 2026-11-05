"""重建 shared/render/examples/ 里的生成样例，或检查它们是否过期。

用法::

    python -m shared.render.build_examples          # 重新生成全部样例
    python -m shared.render.build_examples --check  # 只比对，过期的列出来并以非零退出

样例输入全部是虚构人物与虚构机关；docx 的比对只看 word/*.xml，因为 zip 里的时间戳每次不同。
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from pathlib import Path

from . import card_html, docx_redhead, article_html
from .common import RenderError

EXAMPLES_DIR = Path(__file__).with_name("examples")

#: (输入文件, 渲染器, 输出文件)
JOBS = (
    ("cadre.json", "article", "cadre.article.html"),
    ("cadre.json", "card", "cadre.card.html"),
    ("cadre.json", "caption", "cadre.caption.txt"),
    ("cadre.json", "docx", "cadre.redhead.docx"),
    ("politician.json", "article", "politician.article.html"),
    ("politician.json", "card", "politician.card.html"),
    ("speech.json", "article", "speech.article.html"),
    ("speech.json", "docx", "speech.redhead.docx"),
)


def docx_available() -> bool:
    """红头件渲染器只用标准库，任何机器都能生成 docx 样例。保留这个函数是为了调用方不用改。"""
    return True


def render_job(kind: str, source: Path) -> bytes:
    if kind == "article":
        return article_html.render_article_page(source).encode("utf-8")
    if kind == "card":
        return card_html.render_card_html(source).encode("utf-8")
    if kind == "caption":
        return card_html.render_caption(source).encode("utf-8")
    if kind == "docx":
        buffer = io.BytesIO()
        docx_redhead.build_document(source).save(buffer)
        return buffer.getvalue()
    raise RenderError(f"不认识的渲染器：{kind}")


def docx_xml_parts(data: bytes) -> dict[str, bytes]:
    """docx 里 word/ 下的 XML 部件，用来比对内容而不比对 zip 时间戳。"""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in sorted(archive.namelist()) if name.startswith("word/") and name.endswith(".xml")}


def same_output(kind: str, fresh: bytes, existing: bytes) -> bool:
    if kind == "docx":
        return docx_xml_parts(fresh) == docx_xml_parts(existing)
    return fresh == existing


def build(check: bool = False, examples_dir: Path = EXAMPLES_DIR, include_docx: bool | None = None) -> list[str]:
    """生成（或只检查）全部样例，返回过期或缺失的输出文件名列表。"""
    if include_docx is None:
        include_docx = docx_available()
    stale: list[str] = []
    for source_name, kind, output_name in JOBS:
        if kind == "docx" and not include_docx:
            continue
        source = examples_dir / source_name
        output = examples_dir / output_name
        fresh = render_job(kind, source)
        if output.exists() and same_output(kind, fresh, output.read_bytes()):
            continue
        stale.append(output_name)
        if not check:
            output.write_bytes(fresh)
    return stale


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="重建或检查 shared/render/examples/ 的生成样例。")
    parser.add_argument("--check", action="store_true", help="只检查，不写文件；有过期样例时退出码为 1")
    args = parser.parse_args(argv)
    try:
        stale = build(check=args.check)
    except RenderError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    if args.check:
        if stale:
            print("过期的样例：" + "、".join(stale))
            return 1
        print("样例都是最新的")
        return 0
    print("已更新：" + ("、".join(stale) if stale else "无（都是最新的）"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
