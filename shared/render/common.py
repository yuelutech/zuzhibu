"""三个渲染器共用的稿件对象规范与小工具。

稿件对象是一个简单的 dict（或同构的 JSON 文件），字段见 ``FIELDS``；
履历条目接受 dict、list 或 "起止时间 单位职务" 字符串三种写法，
``normalize_doc`` 把它们统一成固定形状，三个渲染器只认统一后的形状。

只用标准库。
"""

from __future__ import annotations

import html
import json
import math
import re
import unicodedata
from pathlib import Path
from typing import Any

FULLWIDTH_SPACE = "　"

#: 续页一行约 22 个字宽（rules/cadre.md 第 5.4 条）。
LINE_LIMIT = 22

#: 稿件对象的字段：英文键 → (中文名, 说明)。未列出的键报错，下划线开头的键（如 _comment）忽略。
FIELDS: dict[str, tuple[str, str]] = {
    "title": ("标题", "必填。简历写“××简历”，讲话稿、新闻稿写稿件标题。"),
    "subtitle": ("副题", "可选。如讲话稿的“——在××上的讲话”或“（2026年10月16日）”。"),
    "byline": ("署名行", "可选。标题、副题下居中的几行，如讲话稿的日期行和讲话人行；字符串或字符串列表。"),
    "summary": ("提要", "可选。公众号浅蓝提要框的文字；缺省依次取现任段、首段第一句。"),
    "photo": ("照片路径", "可选。相对输出文件的路径或 URL；公众号粘贴时需在编辑器里重新上传。"),
    "photo_credit": ("照片署名", "可选。CC BY 等要署名的照片写在照片下面，如“照片：某某，CC BY 2.0，有裁切”；公有领域的不写。"),
    "lead": ("首段", "可选。干部简历固定句式的首段。"),
    "current": ("现任段", "可选。政治人物独立成段的“现任……”。"),
    "careers": ("履历条目", "可选。列表，每条写法见 normalize_career。"),
    "paragraphs": ("正文段落", "可选。讲话稿、新闻稿的正文，字符串列表。"),
    "closing": ("尾段", "可选。正文之后的结束段。"),
    "afterword": ("写在最后", "可选，只公众号用。“写在最后”段的文字，字符串或列表；缺省不加这一段。"),
    "redhead": ("红头", "可选，只红头件用。agency 发文机关标志、doc_number 发文字号、recipient 主送机关、signoff 落款署名、date 成文日期。"),
    "name": ("人名", "可选。配文和标签用；缺省从标题去掉“简历”得到。"),
    "tags": ("话题标签", "可选，只图文卡配文用。公司名等，不带 #。"),
}

#: 履历条目的字段。
CAREER_FIELDS = ("period", "organization", "role", "text", "note")

#: 红头字段。
REDHEAD_FIELDS = ("agency", "doc_number", "recipient", "signoff", "date")


class RenderError(ValueError):
    """稿件对象不合规。"""


# ---------------------------------------------------------------- 读入与规范化


def load_doc(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    """读入稿件对象并规范化。``source`` 是 dict，或 JSON 文件路径。"""
    if isinstance(source, dict):
        return normalize_doc(source)
    path = Path(source)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise RenderError(f"读不到稿件文件：{path}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RenderError(f"稿件文件不是合法 JSON：{path}：{exc}") from exc
    return normalize_doc(raw)


def normalize_doc(raw: Any) -> dict[str, Any]:
    """把任意写法的稿件对象整理成固定形状；字段不认识或类型不对就报 RenderError。"""
    if not isinstance(raw, dict):
        raise RenderError("稿件对象必须是 dict/JSON 对象")
    unknown = sorted(k for k in raw if k not in FIELDS and not str(k).startswith("_"))
    if unknown:
        raise RenderError(f"稿件对象有不认识的字段：{'、'.join(unknown)}；可用字段：{'、'.join(FIELDS)}")

    doc: dict[str, Any] = {}
    for key in ("title", "subtitle", "summary", "photo", "photo_credit", "lead", "current", "closing", "name"):
        doc[key] = _clean_text(raw.get(key), key)
    if not doc["title"]:
        raise RenderError("稿件对象缺标题 title")

    byline = raw.get("byline")
    byline_items = [byline] if isinstance(byline, str) else _as_list(byline, "byline")
    doc["byline"] = [text for text in (_clean_text(b, "byline") for b in byline_items) if text]
    doc["careers"] = [normalize_career(entry) for entry in _as_list(raw.get("careers"), "careers")]
    doc["paragraphs"] = [
        text for text in (_clean_text(p, "paragraphs") for p in _as_list(raw.get("paragraphs"), "paragraphs")) if text
    ]
    doc["tags"] = [tag for tag in (_clean_text(t, "tags").lstrip("#") for t in _as_list(raw.get("tags"), "tags")) if tag]

    afterword = raw.get("afterword")
    if afterword is None:
        doc["afterword"] = None
    elif isinstance(afterword, (list, tuple)):
        doc["afterword"] = [t for t in (_clean_text(p, "afterword") for p in afterword) if t]
    else:
        doc["afterword"] = _clean_text(afterword, "afterword")

    redhead = raw.get("redhead") or {}
    if not isinstance(redhead, dict):
        raise RenderError("redhead 必须是对象")
    bad = sorted(k for k in redhead if k not in REDHEAD_FIELDS and not str(k).startswith("_"))
    if bad:
        raise RenderError(f"redhead 有不认识的字段：{'、'.join(bad)}；可用字段：{'、'.join(REDHEAD_FIELDS)}")
    doc["redhead"] = {k: _clean_text(redhead.get(k), f"redhead.{k}") for k in REDHEAD_FIELDS}

    if not doc["name"]:
        doc["name"] = derive_name(doc["title"])
    return doc


def normalize_career(entry: Any) -> dict[str, str]:
    """把一条履历整理成 {period, organization, role, text, note, content}。

    接受三种写法：
    - dict：period 起止时间（必填）；organization 单位 + role 职务，或直接给 text 单位职务整行；note 括注（其间、兼任），不带括号也行。
    - list：[起止时间, 单位职务] 或 [起止时间, 单位, 职务]。
    - str："起止时间 单位职务"，第一个空白处分开。
    content 是 text 加括注，图上显示的就是它。
    """
    if isinstance(entry, str):
        parts = re.split(r"[\s　]+", entry.strip(), maxsplit=1)
        if len(parts) != 2:
            raise RenderError(f"履历条目字符串要写成“起止时间 单位职务”：{entry!r}")
        data: dict[str, Any] = {"period": parts[0], "text": parts[1]}
    elif isinstance(entry, (list, tuple)):
        if len(entry) == 2:
            data = {"period": entry[0], "text": entry[1]}
        elif len(entry) == 3:
            data = {"period": entry[0], "organization": entry[1], "role": entry[2]}
        else:
            raise RenderError(f"履历条目列表要有 2 项或 3 项：{entry!r}")
    elif isinstance(entry, dict):
        # content 是规范化后派生的字段，再次规范化时允许出现，但一律重算。
        unknown = sorted(k for k in entry if k not in CAREER_FIELDS and k != "content" and not str(k).startswith("_"))
        if unknown:
            raise RenderError(f"履历条目有不认识的字段：{'、'.join(unknown)}；可用字段：{'、'.join(CAREER_FIELDS)}")
        data = entry
    else:
        raise RenderError(f"履历条目要是 dict、list 或字符串：{entry!r}")

    period = _clean_text(data.get("period"), "period")
    organization = _clean_text(data.get("organization"), "organization")
    role = _clean_text(data.get("role"), "role")
    text = _clean_text(data.get("text"), "text")
    note = _clean_text(data.get("note"), "note")
    if not period:
        raise RenderError(f"履历条目缺起止时间 period：{entry!r}")
    if organization or role:
        joined = join_org_role(organization, role)
        # 规范化过的条目同时带 text 和 organization/role，二者一致就放行；不一致说明写法混用。
        if text and text != joined:
            raise RenderError(f"履历条目 text 与 organization/role 只能用一种：{entry!r}")
        text = joined
    if not text:
        raise RenderError(f"履历条目缺单位职务：{entry!r}")
    if note and not note.startswith(("（", "(")):
        note = f"（{note}）"
    return {
        "period": period,
        "organization": organization,
        "role": role,
        "text": text,
        "note": note,
        "content": text + note,
    }


def join_org_role(organization: str, role: str) -> str:
    """单位＋职务直接相连；英文和汉字相接时空一格（rules/cadre.md 第 2.7 条）。"""
    if not organization or not role:
        return organization or role
    if _needs_space(organization[-1], role[0]):
        return f"{organization} {role}"
    return organization + role


def derive_name(title: str) -> str:
    """从“××简历”“××同志简历”得到人名；其他标题原样返回。"""
    return re.sub(r"(同志)?简历$", "", title.strip()) or title.strip()


def first_sentence(text: str) -> str:
    """取到第一个句号（含）为止；没有句号就整段返回。"""
    text = text.strip()
    pos = text.find("。")
    return text[: pos + 1] if pos >= 0 else text


#: 称谓里的称呼词。称谓行要以冒号结尾、含其中之一，才排成顶格。
_ADDRESS_WORDS = (
    "同志们", "同志", "各位", "尊敬的", "女士们", "先生们", "朋友们", "来宾", "嘉宾", "代表们", "委员",
    "主席", "老师们", "同学们", "家长", "大家",
)


def is_salutation(text: str) -> bool:
    """讲话稿的称谓行（“同志们：”“各位来宾、各位朋友：”“尊敬的各位嘉宾，女士们、先生们，朋友们：”）：
    以冒号结尾、不超过 40 个字宽、没有句号问号叹号分号、含称呼词，排版时顶格不缩进。"""
    text = text.strip()
    if not text.endswith(("：", ":")) or text_width(text) > 40 or re.search(r"[。！？；!?;]", text):
        return False
    return any(word in text for word in _ADDRESS_WORDS)


_HEADING1_RE = re.compile(r"^[一二三四五六七八九十]+、")


def is_heading(text: str) -> bool:
    """正文里独立成段的“一、……”一级标题：短、不以句号结尾。红头件用黑体，公众号加粗。"""
    text = text.strip()
    return bool(_HEADING1_RE.match(text)) and len(text) <= 45 and not text.endswith("。")


# ---------------------------------------------------------------- 字宽与断行


def text_width(text: str) -> float:
    """估算一行占几个汉字宽：全角、宽字符和东亚模糊宽度算 1，其余（数字、英文）算 0.5。"""
    total = 0.0
    for ch in text:
        if ch in "\r\n":
            continue
        total += 1.0 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 0.5
    return total


def estimate_lines(text: str, limit: float = LINE_LIMIT) -> int:
    """一段居中文字大约占几行。"""
    return max(1, math.ceil(text_width(text) / limit)) if text else 1


def wrap_content(entry: dict[str, str], limit: float = LINE_LIMIT) -> list[str]:
    """履历内容行超过约 ``limit`` 个字宽时断行：机构名后、逗号后、括注前，贪心装满一行。

    返回各行文字。单个片段本身超长时不再硬拆，交给排版自然换行。
    """
    content = entry["content"]
    if text_width(content) <= limit:
        return [content]
    atoms: list[str] = []
    text = entry.get("text", content)
    organization = entry.get("organization", "")
    if organization and text.startswith(organization) and len(text) > len(organization):
        atoms.append(organization)
        text = text[len(organization):]
    atoms.extend(_split_after_commas(text))
    atoms.extend(_split_after_commas(entry.get("note", "")))

    lines: list[str] = []
    current = ""
    for atom in atoms:
        if current and text_width((current + atom).strip()) > limit:
            lines.append(current.strip())
            current = atom
        else:
            current += atom
    if current.strip():
        lines.append(current.strip())
    return lines


# ---------------------------------------------------------------- HTML 小工具


def esc(text: Any) -> str:
    """HTML 转义，属性值也能用。"""
    return html.escape("" if text is None else str(text), quote=True)


def attr_style(style: str) -> str:
    """把样式串整理成 ``style="..."``，去掉多余空白。"""
    return 'style="' + esc(" ".join(style.split())) + '"'


# ---------------------------------------------------------------- 内部


def _clean_text(value: Any, key: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise RenderError(f"字段 {key} 应是字符串：{value!r}")
    return str(value).strip()


def _as_list(value: Any, key: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    raise RenderError(f"字段 {key} 应是列表：{value!r}")


def _split_after_commas(text: str) -> list[str]:
    return [piece for piece in re.split(r"(?<=，)", text) if piece]


def _is_ascii_word(ch: str) -> bool:
    return ch.isascii() and ch.isalnum()


def _is_wide(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("W", "F")


def _needs_space(left: str, right: str) -> bool:
    return (_is_ascii_word(left) and _is_wide(right) and right not in "，。、；：（）") or (
        _is_wide(left) and left not in "，。、；：（）" and _is_ascii_word(right)
    )
