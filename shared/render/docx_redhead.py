"""红头件渲染器：只用标准库（zipfile 加手写 WordprocessingML）生成可在常用文字处理软件里修改的 .docx。

自上而下：发文机关标志（红字）、发文字号、红色分隔线、标题、副题、署名行（日期行、讲话人行）、主送机关、正文、落款；
页脚是页码，需要时可在页码下加一行说明（footer_note）。版心按 A4 公文的常用值：上 37 下 35 左 28 右 26 毫米，正文 3 号字、
固定行距、首行缩进 2 字、两端对齐，文档网格每行 28 字、每页 22 行。正文里“一、”开头的一级标题用黑体，
“（一）”开头的二级标题用楷体（标题句之后的正文仍用正文字体）。页码 4 号“—　1　—”，单页居右、双页居左，各空一字。

字体只写名字，可配置，默认免费字体；字体文件不进仓库。

用法::

    python -m shared.render.docx_redhead 稿件.json -o 输出.docx
    python -m shared.render.docx_redhead 稿件.json -o 输出.docx --local-fonts      # 方正小标宋简体、仿宋_GB2312、黑体、楷体_GB2312、宋体、Times New Roman
    python -m shared.render.docx_redhead 稿件.json -o 输出.docx --font-body 仿宋_GB2312 --font-latin "Times New Roman"

    from shared.render.docx_redhead import render_docx
    render_docx(doc, "输出.docx", fonts={"body": "仿宋_GB2312", "latin": "Times New Roman"})
"""

from __future__ import annotations

import argparse
import datetime as _dt
import io
import re
import sys
import zipfile
from pathlib import Path
from typing import IO, Any
from xml.sax.saxutils import escape

from .common import FULLWIDTH_SPACE, RenderError, is_heading, is_salutation, load_doc

#: 默认字体：全部是免费字体（思源宋体、思源黑体为 OFL 许可，Liberation Serif 与 Times New Roman 同度量）。
DEFAULT_FONTS = {
    "header": "Noto Serif CJK SC",  # 发文机关标志
    "title": "Noto Serif CJK SC",  # 标题
    "body": "Noto Serif CJK SC",  # 正文汉字
    "latin": "Liberation Serif",  # 数字和西文
    "heading": "Noto Sans CJK SC",  # “一、”一级标题（黑体位）
    "kai": "Noto Serif CJK SC",  # “（一）”二级标题、署名行（楷体位）
    "page": "Noto Serif CJK SC",  # 页码（宋体位）
}

#: 本机常用配置（照《标准公文模板》）。只是名字，字体由本机安装。
LOCAL_FONTS = {
    "header": "方正小标宋简体",
    "title": "方正小标宋简体",
    "body": "仿宋_GB2312",
    "latin": "Times New Roman",
    "heading": "黑体",
    "kai": "楷体_GB2312",
    "page": "宋体",
}

#: 版式参数，单位：毫米（页面、边距）或磅（字号、行距）。
LAYOUT = {
    "page_width_mm": 210,
    "page_height_mm": 297,
    "margin_top_mm": 37,
    "margin_bottom_mm": 35,
    "margin_left_mm": 28,
    "margin_right_mm": 26,
    "footer_distance_mm": 17.5,
    "header_size": 36,  # 发文机关标志
    "header_space_before_mm": 35,  # 发文机关标志上边缘到版心上边缘
    "number_size": 16,  # 发文字号，3 号
    "rule_size_eighths": 12,  # 红线粗细，1/8 磅为单位：12 = 1.5 磅
    "title_size": 22,  # 标题，2 号
    "title_line_pitch": 30,
    "body_size": 16,  # 正文，3 号
    "line_pitch": 28,  # 正文行距（固定值）
    "chars_per_line": 28,  # 文档网格：每行字数
    "lines_per_page": 22,  # 文档网格：每页行数
    "page_number_size": 14,  # 页码，4 号
    "footer_size": 9,
}

DEFAULT_AGENCY = "AI政见·组织部文件"
DEFAULT_FOOTER_NOTE = ""  # 默认不加页脚说明
RED = "FF0000"
GREY = "808080"

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
HEADING2_RE = re.compile(r"^（[一二三四五六七八九十]+）")


def resolve_fonts(fonts: dict[str, str] | None = None) -> dict[str, str]:
    """把部分指定的字体配置补全成全部各项；键不认识就报错。"""
    merged = dict(DEFAULT_FONTS)
    for key, value in (fonts or {}).items():
        if key not in DEFAULT_FONTS:
            raise RenderError(f"字体配置不认识的键：{key}；可用：{'、'.join(DEFAULT_FONTS)}")
        if value:
            merged[key] = str(value)
    return merged


def body_paragraphs(doc: dict[str, Any]) -> list[tuple[str, str]]:
    """正文段落的顺序与种类：(kind, text)。kind 是 lead、current、career、salutation、heading、para、closing。"""
    items: list[tuple[str, str]] = []
    if doc["lead"]:
        items.append(("lead", doc["lead"]))
    if doc["current"]:
        items.append(("current", doc["current"]))
    for entry in doc["careers"]:
        items.append(("career", f"{entry['period']}{FULLWIDTH_SPACE}{entry['content']}"))
    for text in doc["paragraphs"]:
        if is_salutation(text):
            items.append(("salutation", text))
        elif is_heading(text):
            items.append(("heading", text))
        else:
            items.append(("para", text))
    if doc["closing"]:
        items.append(("closing", doc["closing"]))
    return items


# ---------------------------------------------------------------- 包

class DocxPackage:
    """生成好的 .docx：部件名到字节。save 接受路径或二进制文件对象。"""

    def __init__(self, parts: dict[str, bytes]):
        self.parts = parts

    @property
    def names(self) -> list[str]:
        return list(self.parts)

    def save(self, target: str | Path | IO[bytes]) -> None:
        if isinstance(target, (str, Path)):
            with open(target, "wb") as handle:
                self._write(handle)
        else:
            self._write(target)

    def to_bytes(self) -> bytes:
        buffer = io.BytesIO()
        self._write(buffer)
        return buffer.getvalue()

    def _write(self, handle: IO[bytes]) -> None:
        # 固定时间戳，同样的输入得到同样的字节，样例比对和版本管理都省事。
        stamp = (2026, 1, 1, 0, 0, 0)
        with zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in self.parts.items():
                info = zipfile.ZipInfo(name, date_time=stamp)
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, data)


# ---------------------------------------------------------------- 单位与 XML 小件

def _twips_mm(mm: float) -> int:
    return round(mm / 25.4 * 1440)


def _twips_pt(pt: float) -> int:
    return round(pt * 20)


def _half_points(pt: float) -> int:
    return round(pt * 2)


def _attr(value: Any) -> str:
    return escape(str(value), {'"': "&quot;"})


def _rpr(east_asian: str, latin: str, size_pt: float, *, color: str | None = None, all_fonts: str | None = None) -> str:
    """西文、数字用 latin，汉字用 east_asian；w:hint=eastAsia 让全角标点也走中文字体。all_fonts 给了就四项都用它（页码）。"""
    ascii_font = all_fonts or latin
    east = all_fonts or east_asian
    parts = [
        f'<w:rFonts w:ascii="{_attr(ascii_font)}" w:hAnsi="{_attr(ascii_font)}" w:eastAsia="{_attr(east)}" '
        f'w:cs="{_attr(ascii_font)}" w:hint="eastAsia"/>'
    ]
    if color:
        parts.append(f'<w:color w:val="{color}"/>')
    size = _half_points(size_pt)
    parts.append(f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>')
    return "<w:rPr>" + "".join(parts) + "</w:rPr>"


def _run(text: str, rpr: str) -> str:
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _ppr(
    *,
    align: str | None = None,
    line_pitch: float | None = None,
    space_before: int = 0,
    space_after: int = 0,
    first_line_chars: int = 0,
    first_line_twips: int = 0,
    right_chars: int = 0,
    right_twips: int = 0,
    left_chars: int = 0,
    left_twips: int = 0,
    snap_to_grid: bool = True,
    border: str = "",
) -> str:
    """段落属性，子元素按 schema 顺序：pBdr、snapToGrid、spacing、ind、jc。"""
    parts = []
    if border:
        parts.append(border)
    if not snap_to_grid:
        parts.append('<w:snapToGrid w:val="0"/>')
    spacing = f'<w:spacing w:before="{space_before}" w:after="{space_after}"'
    if line_pitch:
        spacing += f' w:line="{_twips_pt(line_pitch)}" w:lineRule="exact"'
    parts.append(spacing + "/>")
    ind = []
    if first_line_chars:
        ind.append(f'w:firstLineChars="{first_line_chars * 100}" w:firstLine="{first_line_twips}"')
    if left_chars:
        ind.append(f'w:left="{left_twips}" w:leftChars="{left_chars * 100}"')
    if right_chars:
        ind.append(f'w:right="{right_twips}" w:rightChars="{right_chars * 100}"')
    if ind:
        parts.append("<w:ind " + " ".join(ind) + "/>")
    if align:
        parts.append(f'<w:jc w:val="{align}"/>')
    return "<w:pPr>" + "".join(parts) + "</w:pPr>"


def _p(ppr: str, runs: str = "") -> str:
    return f"<w:p>{ppr}{runs}</w:p>"


# ---------------------------------------------------------------- 文档

def _redhead_paragraphs(paragraphs: list[str], redhead: dict[str, Any], fonts: dict[str, str], cfg: dict[str, Any],
                        pitch: float, body_rpr) -> None:
    """红头：发文机关标志、发文字号、红色分隔线。"""
    # 发文机关标志
    paragraphs.append(_p(
        _ppr(align="center", space_before=_twips_mm(cfg["header_space_before_mm"]), space_after=_twips_pt(pitch * 2), snap_to_grid=False),
        _run(redhead["agency"] or DEFAULT_AGENCY, _rpr(fonts["header"], fonts["latin"], cfg["header_size"], color=RED)),
    ))
    # 发文字号
    if redhead["doc_number"]:
        paragraphs.append(_p(
            _ppr(align="center", space_after=_twips_mm(4), snap_to_grid=False),
            _run(redhead["doc_number"], body_rpr(size=cfg["number_size"])),
        ))
    # 红色分隔线：一个空段落，下边框画红线，与版心等宽
    border = (
        f'<w:pBdr><w:bottom w:val="single" w:sz="{cfg["rule_size_eighths"]}" w:space="1" w:color="{RED}"/></w:pBdr>'
    )
    paragraphs.append(_p(_ppr(line_pitch=2, space_after=_twips_pt(pitch * 2), snap_to_grid=False, border=border)))


def build_document(
    source: dict[str, Any] | str | Path,
    fonts: dict[str, str] | None = None,
    *,
    footer_note: str | None = DEFAULT_FOOTER_NOTE,
    layout: dict[str, Any] | None = None,
    page_numbers: bool = True,
    plain: bool = False,
) -> DocxPackage:
    """生成 .docx 的全部部件，不落盘。plain=True 时不要红头（发文机关标志、发文字号、红线），出普通文件。"""
    doc = load_doc(source)
    fonts = resolve_fonts(fonts)
    cfg = dict(LAYOUT, **(layout or {}))
    redhead = doc["redhead"]
    body_size = cfg["body_size"]
    pitch = cfg["line_pitch"]
    pitch_twips = _twips_pt(pitch)
    indent2 = _twips_pt(body_size * 2)

    def body_rpr(east: str | None = None, size: float | None = None, color: str | None = None) -> str:
        return _rpr(east or fonts["body"], fonts["latin"], size or body_size, color=color)

    paragraphs: list[str] = []
    if not plain:
        _redhead_paragraphs(paragraphs, redhead, fonts, cfg, pitch, body_rpr)
    # 标题、副题、署名行
    tail = [x for x in (doc["subtitle"], *doc["byline"]) if x]
    paragraphs.append(_p(
        _ppr(align="center", line_pitch=cfg["title_line_pitch"], space_after=0 if tail else pitch_twips, snap_to_grid=False),
        _run(doc["title"], _rpr(fonts["title"], fonts["latin"], cfg["title_size"])),
    ))
    if doc["subtitle"]:
        last = not doc["byline"]
        paragraphs.append(_p(
            _ppr(align="center", line_pitch=pitch, space_after=pitch_twips if last else 0, snap_to_grid=False),
            _run(doc["subtitle"], body_rpr()),
        ))
    for index, line in enumerate(doc["byline"]):
        last = index == len(doc["byline"]) - 1
        paragraphs.append(_p(
            _ppr(align="center", line_pitch=pitch, space_after=pitch_twips if last else 0, snap_to_grid=False),
            _run(line, body_rpr(east=fonts["kai"])),
        ))
    # 主送机关
    if redhead["recipient"]:
        paragraphs.append(_p(_ppr(line_pitch=pitch), _run(redhead["recipient"], body_rpr())))
    # 正文
    for kind, text in body_paragraphs(doc):
        if kind == "salutation":
            paragraphs.append(_p(_ppr(align="left", line_pitch=pitch), _run(text, body_rpr())))
            continue
        ppr = _ppr(align="both", line_pitch=pitch, first_line_chars=2, first_line_twips=indent2)
        if kind == "heading":
            runs = _run(text, body_rpr(east=fonts["heading"]))
        elif HEADING2_RE.match(text):
            # “（一）……。”标题句用楷体，句后的正文用正文字体
            cut = text.find("。")
            head, rest = (text[: cut + 1], text[cut + 1:]) if 0 <= cut < 45 else (text, "")
            runs = _run(head, body_rpr(east=fonts["kai"])) + (_run(rest, body_rpr()) if rest else "")
        else:
            runs = _run(text, body_rpr())
        paragraphs.append(_p(ppr, runs))
    # 落款：署名右空二字，成文日期右空四字
    first = True
    for text, right_chars in ((redhead["signoff"], 2), (redhead["date"], 4)):
        if not text:
            continue
        paragraphs.append(_p(
            _ppr(align="right", line_pitch=pitch, right_chars=right_chars, right_twips=_twips_pt(body_size * right_chars),
                 space_before=_twips_pt(pitch * 2) if first else 0),
            _run(text, body_rpr()),
        ))
        first = False

    footers = _footers(fonts, cfg, footer_note, page_numbers)
    parts = _package(doc, cfg, fonts, paragraphs, footers, even_odd=page_numbers and bool(footers))
    return DocxPackage(parts)


def _footers(fonts: dict[str, str], cfg: dict[str, Any], footer_note: str | None, page_numbers: bool) -> dict[str, str]:
    """页脚部件：{"default": xml, "even": xml}。只有标识没有页码时只出 default。"""
    note = ""
    if footer_note:
        note = _p(_ppr(align="center", snap_to_grid=False), _run(footer_note, _rpr(fonts["body"], fonts["latin"], cfg["footer_size"], color=GREY)))
    if not page_numbers:
        return {"default": _footer_xml(note)} if note else {}
    size = cfg["page_number_size"]
    rpr = _rpr(fonts["page"], fonts["latin"], size, all_fonts=fonts["page"])
    field = (
        _run("—" + FULLWIDTH_SPACE, rpr)
        + f'<w:r>{rpr}<w:fldChar w:fldCharType="begin"/></w:r>'
        + f'<w:r>{rpr}<w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
        + f'<w:r>{rpr}<w:fldChar w:fldCharType="separate"/></w:r>'
        + _run("1", rpr)
        + f'<w:r>{rpr}<w:fldChar w:fldCharType="end"/></w:r>'
        + _run(FULLWIDTH_SPACE + "—", rpr)
    )
    one_char = _twips_pt(size)
    odd = _p(_ppr(align="right", right_chars=1, right_twips=one_char, snap_to_grid=False), field)
    even = _p(_ppr(align="left", left_chars=1, left_twips=one_char, snap_to_grid=False), field)
    return {"default": _footer_xml(odd + note), "even": _footer_xml(even + note)}


def _footer_xml(paragraphs: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:ftr xmlns:w="{W_NS}" xmlns:r="{R_NS}">{paragraphs}</w:ftr>'
    )


def _package(
    doc: dict[str, Any],
    cfg: dict[str, Any],
    fonts: dict[str, str],
    paragraphs: list[str],
    footers: dict[str, str],
    *,
    even_odd: bool,
) -> dict[str, bytes]:
    page_w, page_h = _twips_mm(cfg["page_width_mm"]), _twips_mm(cfg["page_height_mm"])
    top, bottom = _twips_mm(cfg["margin_top_mm"]), _twips_mm(cfg["margin_bottom_mm"])
    left, right = _twips_mm(cfg["margin_left_mm"]), _twips_mm(cfg["margin_right_mm"])
    # 文档网格：每页 lines_per_page 行，每行 chars_per_line 字（charSpace 以 1/4096 磅为单位，相对正文字号）
    text_width_pt = (cfg["page_width_mm"] - cfg["margin_left_mm"] - cfg["margin_right_mm"]) / 25.4 * 72
    text_height_pt = (cfg["page_height_mm"] - cfg["margin_top_mm"] - cfg["margin_bottom_mm"]) / 25.4 * 72
    char_space = round((text_width_pt / cfg["chars_per_line"] - cfg["body_size"]) * 4096)
    grid_pitch = round(text_height_pt / cfg["lines_per_page"] * 20)

    footer_refs = ""
    rels = ['<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>',
            '<Relationship Id="rIdSettings" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>']
    overrides = []
    parts: dict[str, bytes] = {}
    for index, (kind, xml) in enumerate(sorted(footers.items()), start=1):
        name = f"footer{index}.xml"
        rid = f"rIdFooter{index}"
        footer_refs += f'<w:footerReference w:type="{kind}" r:id="{rid}"/>'
        rels.append(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="{name}"/>')
        overrides.append(f'<Override PartName="/word/{name}" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>')
        parts[f"word/{name}"] = xml.encode("utf-8")
    sect = (
        f"<w:sectPr>{footer_refs}"
        f'<w:pgSz w:w="{page_w}" w:h="{page_h}"/>'
        f'<w:pgMar w:top="{top}" w:right="{right}" w:bottom="{bottom}" w:left="{left}" w:header="851" '
        f'w:footer="{_twips_mm(cfg["footer_distance_mm"])}" w:gutter="0"/>'
        f'<w:cols w:space="425"/>'
        f'<w:docGrid w:type="linesAndChars" w:linePitch="{grid_pitch}" w:charSpace="{char_space}"/>'
        "</w:sectPr>"
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}"><w:body>{"".join(paragraphs)}{sect}</w:body></w:document>'
    )
    body_size = _half_points(cfg["body_size"])
    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:styles xmlns:w="{W_NS}"><w:docDefaults><w:rPrDefault><w:rPr>'
        f'<w:rFonts w:ascii="{_attr(fonts["latin"])}" w:hAnsi="{_attr(fonts["latin"])}" w:eastAsia="{_attr(fonts["body"])}" '
        f'w:cs="{_attr(fonts["latin"])}"/>'
        f'<w:kern w:val="2"/><w:sz w:val="{body_size}"/><w:szCs w:val="{body_size}"/>'
        '<w:lang w:val="en-US" w:eastAsia="zh-CN" w:bidi="ar-SA"/></w:rPr></w:rPrDefault>'
        '<w:pPrDefault><w:pPr><w:widowControl w:val="0"/><w:jc w:val="both"/></w:pPr></w:pPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>'
        '<w:style w:type="character" w:default="1" w:styleId="DefaultParagraphFont"><w:name w:val="Default Paragraph Font"/>'
        "<w:uiPriority w:val=\"1\"/><w:semiHidden/><w:unhideWhenUsed/></w:style></w:styles>"
    )
    settings = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<w:settings xmlns:w="{W_NS}"><w:zoom w:percent="100"/>'
        + ("<w:evenAndOddHeaders/>" if even_odd else "")
        + '<w:defaultTabStop w:val="420"/><w:drawingGridVerticalSpacing w:val="156"/>'
        '<w:characterSpacingControl w:val="compressPunctuation"/>'
        '<w:compat><w:spaceForUL/><w:balanceSingleByteDoubleByteWidth/><w:doNotLeaveBackslashAlone/><w:ulTrailSpace/>'
        '<w:doNotExpandShiftReturn/><w:adjustLineHeightInTable/><w:useFELayout/>'
        '<w:compatSetting w:name="compatibilityMode" w:uri="http://schemas.microsoft.com/office/word" w:val="15"/></w:compat>'
        "</w:settings>"
    )
    now = _dt.datetime(2026, 1, 1).strftime("%Y-%m-%dT%H:%M:%SZ")
    core = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{escape(doc['title'])}</dc:title><dc:creator>AI政见·组织部</dc:creator>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>'
    )
    app = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
        "<Application>AI政见·组织部 docx_redhead</Application></Properties>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
        + "".join(overrides)
        + '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
        '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
        "</Relationships>"
    )
    doc_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(rels) + "</Relationships>"
    )
    ordered = {
        "[Content_Types].xml": content_types.encode("utf-8"),
        "_rels/.rels": root_rels.encode("utf-8"),
        "docProps/core.xml": core.encode("utf-8"),
        "docProps/app.xml": app.encode("utf-8"),
        "word/document.xml": document.encode("utf-8"),
        "word/styles.xml": styles.encode("utf-8"),
        "word/settings.xml": settings.encode("utf-8"),
        "word/_rels/document.xml.rels": doc_rels.encode("utf-8"),
    }
    ordered.update(parts)
    return ordered


def render_docx(
    source: dict[str, Any] | str | Path,
    output: str | Path,
    fonts: dict[str, str] | None = None,
    *,
    footer_note: str | None = DEFAULT_FOOTER_NOTE,
    layout: dict[str, Any] | None = None,
    page_numbers: bool = True,
    plain: bool = False,
) -> Path:
    """渲染并保存到 ``output``，返回路径。plain=True 出不带红头的普通文件。"""
    package = build_document(source, fonts, footer_note=footer_note, layout=layout, page_numbers=page_numbers, plain=plain)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    package.save(output)
    return output


# ---------------------------------------------------------------- 命令行

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把稿件 JSON 渲染成 docx 红头件（只用标准库）。")
    parser.add_argument("source", help="稿件 JSON 文件")
    parser.add_argument("-o", "--output", required=True, help="输出 .docx 文件")
    parser.add_argument("--local-fonts", action="store_true", help="用本机常用字体名：方正小标宋简体、仿宋_GB2312、黑体、楷体_GB2312、宋体、Times New Roman")
    for key in DEFAULT_FONTS:
        parser.add_argument(f"--font-{key}", metavar="字体名", help=f"{key} 字体名，缺省 {DEFAULT_FONTS[key]}")
    parser.add_argument("--footer-note", default=DEFAULT_FOOTER_NOTE, help="页码下面加一行说明文字；默认不加")
    parser.add_argument("--no-page-numbers", action="store_true", help="不加页码")
    parser.add_argument("--line-pitch", type=float, help=f"正文固定行距（磅），缺省 {LAYOUT['line_pitch']}")
    args = parser.parse_args(argv)

    fonts = dict(LOCAL_FONTS) if args.local_fonts else {}
    for key in DEFAULT_FONTS:
        value = getattr(args, f"font_{key}")
        if value:
            fonts[key] = value
    layout = {"line_pitch": args.line_pitch} if args.line_pitch else None
    try:
        output = render_docx(args.source, args.output, fonts, footer_note=args.footer_note or None, layout=layout,
                             page_numbers=not args.no_page_numbers)
    except RenderError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print(f"已写入 {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
