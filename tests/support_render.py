"""渲染器测试共用的小工具：HTML 标签配对检查、docx 部件读取。文件名不以 test 开头，不会被当成测试收集。"""

from __future__ import annotations

import io
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

VOID_TAGS = {"img", "br", "meta", "link", "hr", "input"}


class TagCollector(HTMLParser):
    """收集开始标签及其属性，并检查标签是否正确配对。"""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag not in VOID_TAGS:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"意外的结束标签 </{tag}>，当前栈 {self.stack}")
        else:
            self.stack.pop()


def parse_html(text: str) -> TagCollector:
    collector = TagCollector()
    collector.feed(text)
    collector.close()
    if collector.stack:
        collector.errors.append(f"未闭合的标签 {collector.stack}")
    return collector


def w(tag: str) -> str:
    return f"{{{W_NS}}}{tag}"


def wattr(element, name: str):
    return element.get(w(name))


def read_docx(data: bytes):
    """返回 (document.xml 根元素, footer1.xml 文本或 None, 部件名列表)。"""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        root = ET.fromstring(archive.read("word/document.xml"))
        footer = archive.read("word/footer1.xml").decode("utf-8") if "word/footer1.xml" in names else None
    return root, footer, names


def body_paragraphs(root):
    return root.find(w("body")).findall(w("p"))


def paragraph_text(paragraph) -> str:
    return "".join(t.text or "" for t in paragraph.iter(w("t")))


def first_run_props(paragraph):
    run = paragraph.find(w("r"))
    return None if run is None else run.find(w("rPr"))
