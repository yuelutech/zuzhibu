# -*- coding: utf-8 -*-
"""讲话稿、新闻稿 lint 共用的部分：稿件拆段、分句、数字抽取与比对、英文夹杂、问题条目。

speech_lint 和 newswire_lint 照各自 SKILL.md 参数卡逐条实现，这里只放两边都要用的工具。只用标准库。

问题条目统一成 dict：{level, rule, message, where}，level 只取 P0/P1/P2，
正好是 shared/backends/lint_hook 认的形态。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]

CJK_RE = re.compile(r"[一-鿿㐀-䶿]")
HAN_NUM = "一二三四五六七八九十"
HEADING1_RE = re.compile(rf"^[{HAN_NUM}]+、")
HEADING2_RE = re.compile(rf"^（[{HAN_NUM}]+）")
FULL_DATE_RE = re.compile(r"\d{4}年\d{1,2}月\d{1,2}日")
SENT_END_RE = re.compile(r"(?<=[。！？；])")
ENGLISH_RUN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’&.\-]*(?:[ \t]+[A-Za-z][A-Za-z0-9'’&.\-]*)*")
LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'’&.+\-]*")
QUOTE_RE = re.compile(r"“([^”]*)”")
MARKDOWN_LINE_RE = re.compile(r"^\s*(?:#{1,6}\s|[*\-+]\s|>\s?|\d+[.、]\s|`)")
INLINE_FORMAT_RE = re.compile(r"`|\*\*|__|\|\s*-{3,}|<\/?[A-Za-z][^>]*>|https?://|www\.")

#: 单个英文词默认放行：产品型号、通用缩写（SKILL 第 6.6 条、第 7.7 条点名的几类）。
BASE_ALLOWED_LATIN = frozenset({
    "AI", "GPU", "CPU", "Token", "AGI", "API", "IT", "5G", "6G", "App", "CEO", "CTO",
})


# =============================================================================
# 问题条目
# =============================================================================


def issue(level: str, rule: str, message: str, where: str = "") -> dict[str, str]:
    return {"level": level, "rule": rule, "message": message, "where": where}


def clip(text: str, limit: int = 30) -> str:
    text = text.replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


# =============================================================================
# 稿件
# =============================================================================


@dataclass
class Draft:
    """lint 看到的稿件：标题、副题、顺序段落；source 是英文原文（没有时为空）。"""

    title: str
    subtitle: str
    paragraphs: list[str]
    source: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def body_text(self) -> str:
        return "\n".join(self.paragraphs)

    @property
    def all_text(self) -> str:
        return "\n".join([self.title, self.subtitle, *self.paragraphs])


def draft_from(
    text: str,
    *,
    document: dict[str, Any] | None,
    source: str | None,
    extra: dict[str, str] | None,
    subtitle_test: Callable[[str], bool],
) -> Draft:
    """优先用流水线传来的 document（标题、副题、段落分开）；只有纯文本时按空行拆，第二块由 subtitle_test 判断是不是副题。"""
    if document:
        paragraphs = [str(p).strip() for p in document.get("paragraphs") or [] if str(p).strip()]
        return Draft(
            title=str(document.get("title") or "").strip(),
            subtitle=str(document.get("subtitle") or "").strip(),
            paragraphs=paragraphs,
            source=source or "",
            extra=dict(extra or {}),
        )
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    title = blocks[0] if blocks else ""
    rest = blocks[1:]
    subtitle = ""
    if rest and subtitle_test(rest[0]):
        subtitle, rest = rest[0], rest[1:]
    return Draft(title=title, subtitle=subtitle, paragraphs=rest, source=source or "", extra=dict(extra or {}))


def cjk_len(text: str) -> int:
    """字数：汉字、全角标点算 1，英文词算 1，数字串算 1。"""
    han = len(re.findall(r"[一-鿿㐀-䶿，。、；：？！“”‘’（）《》——…·]", text))
    latin = len(re.findall(r"[A-Za-z]+|\d+(?:\.\d+)?", text))
    return han + latin


def sentences(text: str) -> list[str]:
    """按句号、问号、叹号、分号切句，去掉空句。"""
    return [s.strip() for s in SENT_END_RE.split(text) if s.strip()]


def strip_quotes(text: str) -> str:
    return QUOTE_RE.sub("", text)


# =============================================================================
# 数字
# =============================================================================

_ZH_MULT = {"万亿": 1e12, "亿": 1e8, "万": 1e4, "千": 1e3}
#: 成稿里的阿拉伯数字（可带千分位、小数、万/亿）。前面紧挨英文字母或数字的不算（型号 H100、GPT-4 一类）。
_ZH_NUM_RE = re.compile(r"(?<![A-Za-z0-9.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?\s*(万亿|亿|万|千)?")
#: 月、日、号前面的数字是日期，电头、日期行、星期换算都会产生，不比对。
_DATE_PART_RE = re.compile(r"^\s*(?:月|日|号)")

_EN_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_EN_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_EN_SCALES = {"hundred": 100, "thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
_EN_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
    "ninth": 9, "tenth": 10,
}
_EN_MULTIPLIERS = {"double": 2, "twice": 2, "triple": 3, "tripled": 3, "triples": 3, "quadruple": 4, "half": 0.5, "dozen": 12}
_EN_SUFFIX_SCALE = {"trillion": 1e12, "billion": 1e9, "bn": 1e9, "million": 1e6, "mn": 1e6, "thousand": 1e3, "k": 1e3, "m": 1e6, "b": 1e9}
_EN_DIGIT_RE = re.compile(
    r"(?<![A-Za-z0-9.])['’]?(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?:st|nd|rd|th)?"
    r"(?:\s*-?\s*(trillion|billion|million|thousand|bn|mn|k|m|b)\b)?",
    re.I,
)
_EN_WORD_RE = re.compile(r"[A-Za-z]+")
#: 原文里只是日期的行（页头的发布日期）不参加“漏数字”检查。
_EN_DATE_LINE_RE = re.compile(
    r"^\s*(?:updated\s+|published\s+)?(?:[A-Z][a-z]{2,8}\.?\s+\d{1,2},\s+\d{4})\s*$", re.I
)


@dataclass(frozen=True)
class Number:
    value: float
    text: str
    start: int = -1
    end: int = -1


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))


def zh_numbers(text: str) -> list[Number]:
    """成稿里要比对的数字：阿拉伯数字按万、亿换算成数值；日期（月、日）和全日期不算。"""
    masked = FULL_DATE_RE.sub(lambda m: " " * len(m.group()), text)
    out: list[Number] = []
    for m in _ZH_NUM_RE.finditer(masked):
        if _DATE_PART_RE.match(masked[m.end():]) and not m.group(3):
            continue
        whole = m.group(1).replace(",", "")
        value = float(whole + ("." + m.group(2) if m.group(2) else ""))
        if m.group(3):
            value *= _ZH_MULT[m.group(3)]
        out.append(Number(value, m.group().strip(), m.start(), m.end()))
    return out


_HAN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_HAN_UNITS = {"十": 10, "百": 100, "千": 1000}
_HAN_BIG = {"万": 1e4, "亿": 1e8}
_HAN_NUM_RE = re.compile(r"[零〇一二两三四五六七八九十百千万亿]+")


def han_numbers(text: str) -> list[float]:
    """汉字数字（两、三、十二、三千、两万……）的数值。只用来认“原文数字在成稿里有对应”，不参与编造检查。"""
    out: list[float] = []
    for m in _HAN_NUM_RE.finditer(text):
        token = m.group()
        if set(token) <= set(_HAN_BIG):
            continue
        total, section, current = 0.0, 0.0, 0.0
        for ch in token:
            if ch in _HAN_DIGITS:
                current = _HAN_DIGITS[ch]
            elif ch in _HAN_UNITS:
                section += (current or 1) * _HAN_UNITS[ch]
                current = 0
            else:
                total += (section + current) * _HAN_BIG[ch]
                section = current = 0
        out.append(total + section + current)
    return out


_EN_APPROX_RE = re.compile(
    r"\b(about|roughly|nearly|approximately|almost|around|more than|over|at least|up to|close to|less than|"
    r"fewer than|under|some|as many as|as much as)\s+(\$?[\d,.]+(?:\s*-?\s*(?:trillion|billion|million|thousand|percent))?"
    r"|(?:a|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|forty|fifty|sixty|seventy|"
    r"eighty|ninety|hundred)(?:[\s-]+(?:hundred|thousand|million|billion|trillion|and|one|two|three|four|five|six|"
    r"seven|eight|nine))*)(?:\s+(?:quarters?|decades?|percent))?",
    re.I,
)
_ZH_APPROX_BEFORE = ("约", "近", "超过", "逾", "至少", "最多", "多达", "最高", "不到", "不足", "将近", "接近", "大约", "上限", "高达", "少于", "低于")
_ZH_APPROX_AFTER = ("左右", "多", "余", "以上", "以下", "上下")


def approx_issues(paragraphs: Sequence[str], source: str, *, rule: str, level: str = "P1") -> list[dict[str, str]]:
    """原文带 about、nearly、up to 等限定词的数字，成稿里对应数字前后要有“约、近、最多、超过”一类字（不改精度）。"""
    if not source.strip():
        return []
    issues: list[dict[str, str]] = []
    drafts = [(p, zh_numbers(p)) for p in paragraphs]
    for m in _EN_APPROX_RE.finditer(source):
        values = [n.value for n in en_numbers(m.group(0)[len(m.group(1)):])[0]]
        if not values:
            continue
        hits = [(p, n) for p, nums in drafts for n in nums if any(_close(n.value, v) for v in values)]
        if not hits:
            continue  # 数字本身没写进成稿，由数字保真那条管
        if any(_has_approx(p, n) for p, n in hits):
            continue
        p, n = hits[0]
        issues.append(issue(level, rule, f"原文「{m.group(0)}」带限定词，成稿「{n.text}」前后没有约、近、超过、最多一类字", clip(p)))
    return issues


def _has_approx(paragraph: str, number: Number) -> bool:
    before = paragraph[max(0, number.start - 4):number.start]
    after = paragraph[number.end:number.end + 4]
    return any(w in before for w in _ZH_APPROX_BEFORE) or any(w in after for w in _ZH_APPROX_AFTER)


def _word_numbers(words: list[str]) -> list[tuple[float, int, bool]]:
    """英文数词：three hundred million、a billion、two decades、three quarters 一类。

    返回 (数值, 起始词序号, 是否算“要求保留的数字”)；one、a、序数只用于反查，不要求成稿保留。
    """
    out: list[tuple[float, int, bool]] = []
    i, n = 0, len(words)
    while i < n:
        w = words[i].lower()
        if w in _EN_ORDINALS:
            out.append((_EN_ORDINALS[w], i, False))
            i += 1
            continue
        if w in _EN_MULTIPLIERS:
            out.append((_EN_MULTIPLIERS[w], i, w not in ("half",)))
            i += 1
            continue
        if w in _EN_UNITS or w in _EN_TENS or (w == "a" and i + 1 < n and words[i + 1].lower() in _EN_SCALES):
            start, total, current, j, used_word = i, 0.0, 0.0, i, []
            while j < n:
                t = words[j].lower()
                if t in _EN_UNITS:
                    current += _EN_UNITS[t]
                elif t in _EN_TENS:
                    current += _EN_TENS[t]
                elif t == "a" and j == start:
                    current += 1
                elif t == "and" and j > start and j + 1 < n and words[j + 1].lower() in (*_EN_UNITS, *_EN_TENS):
                    pass
                elif t in _EN_SCALES:
                    if current == 0:
                        current = 1
                    if t == "hundred":
                        current *= 100
                    else:
                        total += current * _EN_SCALES[t]
                        current = 0
                else:
                    break
                used_word.append(t)
                j += 1
            value = total + current
            required = not (used_word in (["one"], ["a"]) or value < 2 and len(used_word) == 1)
            out.append((value, start, required))
            nxt = words[j].lower() if j < n else ""
            if nxt in ("decade", "decades"):
                out.append((value * 10, start, required))
            elif nxt in ("quarter", "quarters"):
                out.append((value / 4, start, False))
                out.append((value * 25, start, False))
            i = max(j, i + 1)
            continue
        if w in ("decade", "decades") and i > 0 and words[i - 1].lower() in ("a", "the", "one"):
            out.append((10.0, i, False))
        if w in _EN_SCALES and w != "hundred":
            # 前面没有数词的 billion（the billion people）：按 1 个计数单位算，只用于反查
            out.append((_EN_SCALES[w], i, False))
        i += 1
    return out


def en_numbers(text: str) -> tuple[list[Number], list[Number]]:
    """原文里的数字。返回 (全部数值, 要求成稿保留的数值)。

    全部数值：阿拉伯数字（含千分位、小数、$、%、'27 这样的年份缩写）、带 billion/million 的换算值、英文数词、
    two decades 这样的十年换算、three quarters 这样的分数。要求保留的：排除页头日期行、one/a 和序数。
    """
    all_numbers: list[Number] = []
    required: list[Number] = []
    for line in text.splitlines():
        is_date_line = bool(_EN_DATE_LINE_RE.match(line))
        for m in _EN_DIGIT_RE.finditer(line):
            whole = m.group(1).replace(",", "")
            raw = float(whole + ("." + m.group(2) if m.group(2) else ""))
            values = [raw]
            if m.group(3):
                values.insert(0, raw * _EN_SUFFIX_SCALE[m.group(3).lower()])
            token = m.group(0)
            if token[:1] in "'’" and len(whole) == 2:
                values.insert(0, 2000 + raw)  # FY '27：要求成稿保留的是 2027
            nxt = line[m.end():m.end() + 12].lower()
            if re.match(r"\s*-?\s*decades?\b", nxt):
                values.append(raw * 10)
            for v in values:
                all_numbers.append(Number(v, token.strip()))
            if not is_date_line:
                required.append(Number(values[0], token.strip()))
        # 数词另算：先抹掉阿拉伯数字（连同后面的 billion），免得“a $1.5 trillion budget”里剩下的“a trillion”被当成一万亿
        words = _EN_WORD_RE.findall(_EN_DIGIT_RE.sub(" ", line))
        for value, _start, req in _word_numbers(words):
            all_numbers.append(Number(value, "(英文数词)"))
            if req and not is_date_line:
                required.append(Number(value, _word_text(words, _start)))
    return all_numbers, required


def _word_text(words: list[str], start: int) -> str:
    return " ".join(words[start:start + 3])


def number_issues(
    draft_paragraphs: Sequence[str],
    source: str,
    *,
    rule: str,
    skip_paragraph: Callable[[int, str], bool] = lambda i, p: False,
    missing_level: str = "P1",
    title: str = "",
) -> list[dict[str, str]]:
    """数字保真：成稿里原文没有的数字报 P0（编造）；原文数字在成稿里找不到对应的合并成一条 missing_level。"""
    if not source.strip():
        return []
    src_all, src_required = en_numbers(source)
    issues: list[dict[str, str]] = []
    seen_bad: set[str] = set()
    draft_values: list[float] = []
    texts = ([title] if title else []) + list(draft_paragraphs)
    han_values: list[float] = []
    for index, paragraph in enumerate(texts):
        if skip_paragraph(index - (1 if title else 0), paragraph):
            continue
        han_values += han_numbers(paragraph)
        for num in zh_numbers(paragraph):
            draft_values.append(num.value)
            if any(_close(num.value, s.value) for s in src_all):
                continue
            if num.text in seen_bad:
                continue
            seen_bad.add(num.text)
            issues.append(issue("P0", rule, f"成稿数字「{num.text}」在原文里找不到对应（不得编造、推算或自行换算数字）", clip(paragraph)))
    missing: list[str] = []
    for num in src_required:
        if any(_close(num.value, v) for v in draft_values) or any(_close(num.value, v) for v in han_values):
            continue
        label = num.text
        if label not in missing:
            missing.append(label)
    if missing:
        shown = "、".join(missing[:12]) + ("……" if len(missing) > 12 else "")
        issues.append(issue(missing_level, rule, f"原文数字在成稿里没有对应：{shown}（共 {len(missing)} 处，删去的要在复核清单说明）"))
    return issues


# =============================================================================
# 英文夹杂
# =============================================================================


@lru_cache(maxsize=4)
def glossary_latin(glossary_dir: str | None = None) -> tuple[frozenset[str], tuple[str, ...]]:
    """译名表写法里出现的英文词（如“Anthropic公司”里的 Anthropic）和含英文的整条写法，用来放行英文夹杂。"""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        from shared.backends.glossary import Glossary

        glossary = Glossary.load(glossary_dir) if glossary_dir else Glossary.load()
    except (ImportError, OSError, ValueError):
        return frozenset(), ()
    tokens: set[str] = set()
    forms: set[str] = set()
    for entry in glossary:
        for form in entry.forms:
            found = LATIN_TOKEN_RE.findall(form)
            if found:
                forms.add(form)
                tokens.update(found)
    return frozenset(tokens), tuple(sorted(forms, key=len, reverse=True))


def english_issues(
    paragraphs: Sequence[str],
    source: str,
    *,
    rule: str,
    glossary_dir: str | None = None,
) -> list[dict[str, str]]:
    """连续 4 个以上英文词 P0；单个英文词不在译名表、不在放行表的，原文里有的 P2（表外保留英文，进复核清单），没有的 P1。"""
    tokens, forms = glossary_latin(glossary_dir)
    allowed = set(tokens) | BASE_ALLOWED_LATIN
    # 原文词去掉所有格和句末标点再比（Paramount’s、Press.、Paramount+ 都算 Paramount、Press）
    source_tokens = {_bare(t) for t in LATIN_TOKEN_RE.findall(source)} if source else set()
    issues: list[dict[str, str]] = []
    reported: set[str] = set()
    for paragraph in paragraphs:
        masked = paragraph
        for form in forms:
            if form in masked:
                masked = masked.replace(form, "\0" * len(form))
        for m in ENGLISH_RUN_RE.finditer(masked):
            run = m.group().strip(" .-")
            words = run.split()
            if len(words) >= 4:
                if run not in reported:
                    reported.add(run)
                    issues.append(issue("P0", rule, f"连续 {len(words)} 个英文词「{clip(run, 40)}」：不保留英文短语，译成中文或按译名表写", clip(paragraph)))
                continue
            tokens = [t.strip(".-'’") for t in words]
            unknown = [t for t in tokens if t and t not in allowed and t.upper() not in allowed]
            if not unknown:
                continue
            label = run if len(tokens) > 1 else unknown[0]
            if label in reported:
                continue
            reported.add(label)
            lower_source = {t.lower() for t in source_tokens}
            if all(_bare(t).lower() in lower_source for t in unknown):
                issues.append(issue("P2", rule, f"英文「{label}」不在译名表：表外无通行中文名才保留英文，并列入复核清单", clip(paragraph)))
            else:
                issues.append(issue("P1", rule, f"英文「{label}」原文和译名表里都没有", clip(paragraph)))
    return issues


def _bare(token: str) -> str:
    token = re.sub(r"['’]s$", "", token)
    return token.strip(".,;:!?+'’-")


_PARALLEL_LEADS = ("让我们", "我们要", "我们", "要", "必须", "坚持", "更要", "既要", "又要")


def _heads(unit: str) -> set[str]:
    heads = {unit[:1]}
    for lead in _PARALLEL_LEADS:
        if unit.startswith(lead) and len(unit) > len(lead):
            heads.add(unit[len(lead):len(lead) + 1])
    return heads


def parallel_runs(paragraphs: Sequence[str]) -> int:
    """排比处数：一段里有“一是……二是……三是”，或连续 3 个分句（按逗号、分号或句号切）起头相同（“让我们以……，以……，以……”也算）。每段最多记 1 处。"""
    runs = 0
    for p in paragraphs:
        if re.search("一是.*二是.*三是", p):
            runs += 1
            continue
        for sep in (r"[，；。！]", r"[；。！]"):
            units = [u.strip() for u in re.split(sep, p) if u.strip()]
            if any(
                any(re.match(r"[一-鿿]", h) for h in _heads(units[i]) & _heads(units[i + 1]) & _heads(units[i + 2]))
                for i in range(len(units) - 2)
            ):
                runs += 1
                break
    return runs


# =============================================================================
# 词表计数
# =============================================================================


def find_words(text: str, words: Iterable[str]) -> list[str]:
    hits = []
    for word in words:
        if word in text:
            hits.append(word)
    return hits


def count_pattern(text: str, pattern: str) -> int:
    return len(re.findall(pattern, text))


# =============================================================================
# 命令行
# =============================================================================


def load_input(path: Path) -> tuple[str, dict[str, Any] | None, dict[str, str]]:
    """读待查稿件：流水线的 <原文名>.json（取 document），或纯文本 .txt/.md。返回 (纯文本, document, extra)。"""
    raw = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        data = json.loads(raw)
        document = data.get("document") or data
        paragraphs = [str(p) for p in document.get("paragraphs") or []]
        head = [x for x in (document.get("title"), document.get("subtitle")) if x]
        extra = dict((data.get("config") or {}).get("extra") or {})
        return "\n\n".join([*head, *paragraphs]) + "\n", document, extra
    if raw.startswith("---\n"):
        # 流水线的 .md：去掉 YAML 头和复核清单。
        parts = raw.split("\n---\n", 2)
        body = parts[1] if len(parts) > 1 else raw
        body = re.sub(r"^#+\s*", "", body, flags=re.M)
        return body, None, {}
    return raw, None, {}


def cli(
    argv: Sequence[str] | None,
    *,
    prog: str,
    description: str,
    lint_text: Callable[..., list[dict[str, str]]],
    extra_args: Callable[[argparse.ArgumentParser], None] | None = None,
    extra_from: Callable[[argparse.Namespace], dict[str, str]] | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("draft", help="待查稿件：流水线输出的 <原文名>.json，或纯文本")
    parser.add_argument("--source", help="英文原文（数字保真、篇幅比、英文夹杂要用）")
    parser.add_argument("--glossary-dir", help="译名表目录，默认 shared/glossary")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    if extra_args:
        extra_args(parser)
    args = parser.parse_args(argv)
    try:
        text, document, extra = load_input(Path(args.draft))
        source = Path(args.source).read_text(encoding="utf-8-sig") if args.source else None
    except (OSError, ValueError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    if extra_from:
        extra.update({k: v for k, v in extra_from(args).items() if v})
    issues = lint_text(text, source=source, document=document, extra=extra, glossary_dir=args.glossary_dir)
    counts = {lvl: sum(1 for i in issues if i["level"] == lvl) for lvl in ("P0", "P1", "P2")}
    if args.format == "json":
        print(json.dumps({"counts": counts, "issues": issues}, ensure_ascii=False, indent=2))
    else:
        for i in sorted(issues, key=lambda x: (x["level"], x["rule"])):
            where = f"　〔{i['where']}〕" if i.get("where") else ""
            print(f"[{i['level']}] {i['rule']} {i['message']}{where}")
        print(f"P0 {counts['P0']}，P1 {counts['P1']}，P2 {counts['P2']}", file=sys.stderr)
    return 1 if counts["P0"] else 0
