"""glossary：译名表（shared/glossary 三张 v1.4 CSV）的加载、生成前确定性匹配、生成后一致性检查。

对应《译名表使用说明》第三节“渲染时取哪一列”里新闻稿、讲话稿那一行：

- 机构：首次提及用“全称”列，再提用“简称”列（简称为空时仍用全称）。
- 职务：用“干部简历写法”列。
- 人名：首次用“新华社译名”全名，再提可只写姓（有间隔号取最后一段，华人名整个名字）。

匹配是确定性的字符串查找，不做分词、不做模糊匹配：

- 机构名、人名区分大小写，按英文单词边界整词匹配；职务不区分大小写。
- 长键优先，命中后把那段原文遮住，短键不会再在里面二次命中（“Google”不会在“Google DeepMind”里重复命中）。
- 英文键尾部的括注分两种：全大写缩写（``National Science Foundation (NSF)``）作为别名也参与匹配；
  其他括注（``Mayor (City of Kigali)``）只用来区分同名条目，匹配时去掉。
  机构“全称”列里全角括号内的英文缩写（``美国亚马逊网络服务公司（AWS）``）同样作为别名。
- 同一个键对应多条时标为 ambiguous，把所有候选都给模型看，由模型按语境选。
- 单个词的机构名、人名嵌在更长的专名里不算命中：“Finance”不在“Development Finance Corporation”里命中；
  后面跟 Inc.、Corp. 一类公司后缀的照常命中。
- 职务词命中后，把表里以同一个词开头的带语境条目（“Vice President of the United States”→副总统）
  作为相关条目一起给模型，由模型按语境选；职务词本身也可能不作职务用（partner countries），提示词里交代。
- 人名英文键首段是 JD、JB 一类大写缩写的，同时认 J.D.、J. D. 写法。

只用标准库。
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

__all__ = ["GLOSSARY_DIR", "FILES", "Entry", "Match", "Glossary", "render_prompt_table"]

GLOSSARY_DIR = Path(__file__).resolve().parents[1] / "glossary"
FILES = {
    "org": "译名表_v1.4_org.csv",
    "title": "译名表_v1.4_title.csv",
    "person": "译名表_v1.4_person.csv",
}
KIND_ZH = {"org": "机构", "title": "职务", "person": "人名"}

_REQUIRED_COLUMNS = {
    "org": ("english_name", "全称", "简历写法", "简称", "置信度", "备注", "status", "decided_by"),
    "title": ("english_title", "干部简历写法", "用法说明", "置信度", "备注", "status", "decided_by"),
    "person": ("english_name", "新华社译名", "本人汉名或通行汉名", "置信度", "备注", "status", "decided_by"),
}

_QUALIFIER_RE = re.compile(r"\s*\(([^()]*)\)\s*$")
_FULLWIDTH_ACRONYM_RE = re.compile(r"（([A-Za-z][A-Za-z0-9&.\-]{1,29})）")
_ACRONYM_RE = re.compile(r"^[A-Z][A-Z0-9&.\-]{1,11}$")
_SPACE_RE = re.compile(r"\s+")
_INITIALS_RE = re.compile(r"^([A-Z])([A-Z])([A-Z]?)\s+(?=\S)")
_WORD_BEFORE_RE = re.compile(r"([A-Za-z][A-Za-z.&'’\-]*)\s$")
_WORD_AFTER_RE = re.compile(r"^\s([A-Za-z][A-Za-z.&'’\-]*)")
#: 单词专名后面跟这些词时仍算命中（Intel Corp.、Meta Platforms）。
_NAME_SUFFIXES = frozenset({
    "Inc", "Inc.", "Corp", "Corp.", "Corporation", "Co", "Co.", "Ltd", "Ltd.", "LLC", "PLC", "plc", "AG", "SA",
    "Group", "Holdings", "Platforms", "Technologies", "Company",
})
#: 句首或冠词之类的大写词出现在单词专名前面不算“嵌在更长专名里”。
_LEADING_OK = frozenset({"The", "A", "An", "And", "Or", "But", "For", "With", "From", "At", "In", "On", "By", "Of", "To", "As"})
RELATED_TITLE_LIMIT = 8


def _split_key(english: str) -> tuple[str, str, list[str]]:
    """拆英文键：返回 (匹配键, 括注, 别名列表)。"""
    text = _SPACE_RE.sub(" ", english).strip()
    qualifier = ""
    aliases: list[str] = []
    m = _QUALIFIER_RE.search(text)
    if m and m.start() > 0:
        inner = m.group(1).strip()
        text = text[: m.start()].strip()
        if _ACRONYM_RE.match(inner):
            aliases.append(inner)
        else:
            qualifier = inner
    return text, qualifier, aliases


def _surname(full_name: str) -> str:
    """新华社译名的“姓”：有间隔号取最后一段，否则整个名字（华人名、单名）。"""
    name = full_name.strip()
    if "·" in name:
        return name.split("·")[-1].strip() or name
    return name


@dataclass(frozen=True)
class Entry:
    kind: str
    english: str
    key: str
    qualifier: str
    aliases: tuple[str, ...]
    first: str
    later: str
    resume: str
    usage: str
    status: str
    confidence: str
    note: str
    decided_by: str

    @property
    def kind_zh(self) -> str:
        return KIND_ZH.get(self.kind, self.kind)

    @property
    def needs_review(self) -> bool:
        return self.status.strip() != "已核"

    @property
    def forms(self) -> tuple[str, ...]:
        """成稿里可以接受的全部中文写法（去重、去空）。"""
        seen: list[str] = []
        for f in (self.first, self.later, self.resume):
            f = f.strip()
            if f and f not in seen:
                seen.append(f)
        return tuple(seen)

    @property
    def ident(self) -> tuple[str, str]:
        return (self.kind, self.english)


@dataclass
class Match:
    entry: Entry
    count: int
    first_pos: int
    matched_texts: list[str] = field(default_factory=list)
    ambiguous: bool = False
    alternatives: list[Entry] = field(default_factory=list)
    related: list[Entry] = field(default_factory=list)
    """职务词的相关条目：表里以同一个词开头、写明语境的条目（如 Vice President of the United States）。"""

    @property
    def english(self) -> str:
        return self.entry.english

    @property
    def matched_text(self) -> str:
        return self.matched_texts[0] if self.matched_texts else self.entry.key

    def to_dict(self) -> dict:
        return {
            "english": self.entry.english,
            "kind": self.entry.kind,
            "kind_zh": self.entry.kind_zh,
            "first": self.entry.first,
            "later": self.entry.later,
            "status": self.entry.status,
            "confidence": self.entry.confidence,
            "decided_by": self.entry.decided_by,
            "count": self.count,
            "first_pos": self.first_pos,
            "matched_texts": list(self.matched_texts),
            "ambiguous": self.ambiguous,
            "alternatives": [
                {"english": alt.english, "first": alt.first, "later": alt.later, "usage": alt.usage}
                for alt in self.alternatives
            ],
            "related": [{"english": rel.english, "first": rel.first, "status": rel.status} for rel in self.related],
            "needs_review": self.entry.needs_review,
        }


def _get(row: dict, column: str) -> str:
    value = row.get(column)
    return (value or "").strip()


def entry_from_row(kind: str, row: dict) -> Entry | None:
    """把一行 CSV 变成 Entry；查找键或写法为空的行丢掉。"""
    extra_aliases: list[str] = []
    if kind == "org":
        english = _get(row, "english_name")
        first = _get(row, "全称") or _get(row, "简历写法")
        later = _get(row, "简称") or first
        resume = _get(row, "简历写法")
        usage = _get(row, "简历写法示例")
        extra_aliases = [m.group(1) for m in _FULLWIDTH_ACRONYM_RE.finditer(first)]
    elif kind == "title":
        english = _get(row, "english_title")
        first = _get(row, "干部简历写法")
        later = first
        resume = first
        usage = _get(row, "用法说明")
    elif kind == "person":
        english = _get(row, "english_name")
        first = _get(row, "新华社译名") or _get(row, "本人汉名或通行汉名")
        later = _surname(first) if first else ""
        resume = _get(row, "本人汉名或通行汉名")
        usage = "；".join(x for x in (_get(row, "所属机构"), _get(row, "现任职务")) if x)
    else:
        raise ValueError(f"未知类别 {kind!r}")
    if not english or not first:
        return None
    key, qualifier, aliases = _split_key(english)
    if not key:
        return None
    if kind == "person":
        initials = _INITIALS_RE.match(key)
        if initials:
            letters = [x for x in initials.groups() if x]
            rest = key[initials.end():]
            extra_aliases += [".".join(letters) + ". " + rest, ". ".join(letters) + ". " + rest]
    merged: list[str] = []
    for alias in aliases + extra_aliases:
        alias = alias.strip()
        if alias and alias != key and len(alias) >= 2 and alias not in merged:
            merged.append(alias)
    return Entry(
        kind=kind,
        english=english,
        key=key,
        qualifier=qualifier,
        aliases=tuple(merged),
        first=first,
        later=later,
        resume=resume,
        usage=usage,
        status=_get(row, "status"),
        confidence=_get(row, "置信度"),
        note=_get(row, "备注"),
        decided_by=_get(row, "decided_by"),
    )


class Glossary:
    """三张表合在一起的查找结构。"""

    def __init__(self, entries: Iterable[Entry]):
        self.entries: list[Entry] = list(entries)
        # (kind, 查找键) -> 条目列表；职务的查找键小写。
        self._by_key: dict[tuple[str, str], list[Entry]] = {}
        for entry in self.entries:
            for key in (entry.key, *entry.aliases):
                lookup = key.lower() if entry.kind == "title" else key
                bucket = self._by_key.setdefault((entry.kind, lookup), [])
                if entry not in bucket:
                    bucket.append(entry)
        # 长键优先；同长度按类别和键稳定排序，保证结果可复现。
        self._patterns: list[tuple[str, str, re.Pattern[str]]] = []
        for (kind, lookup) in sorted(self._by_key, key=lambda k: (-len(k[1]), k[0], k[1])):
            flags = re.IGNORECASE if kind == "title" else 0
            pattern = re.compile(r"(?<![A-Za-z0-9])" + re.escape(lookup) + r"(?![A-Za-z0-9])", flags)
            self._patterns.append((kind, lookup, pattern))

    # ------------------------------------------------------------------ 加载

    @classmethod
    def load(cls, directory: Path | str = GLOSSARY_DIR) -> "Glossary":
        directory = Path(directory)
        entries: list[Entry] = []
        for kind, filename in FILES.items():
            path = directory / filename
            if not path.is_file():
                raise FileNotFoundError(f"译名表缺文件：{path}")
            entries.extend(cls.read_csv(kind, path))
        return cls(entries)

    @staticmethod
    def read_csv(kind: str, path: Path | str) -> list[Entry]:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = reader.fieldnames or []
            missing = [c for c in _REQUIRED_COLUMNS[kind] if c not in columns]
            if missing:
                raise ValueError(f"{path} 缺列 {missing}，列名应沿用 v1.2 起的定稿列")
            entries = []
            for row in reader:
                entry = entry_from_row(kind, row)
                if entry is not None:
                    entries.append(entry)
        return entries

    # ------------------------------------------------------------------ 统计

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[Entry]:
        return iter(self.entries)

    def count(self, kind: str) -> int:
        return sum(1 for e in self.entries if e.kind == kind)

    def lookup(self, kind: str, english: str) -> list[Entry]:
        key, _, _ = _split_key(english)
        if kind == "title":
            key = key.lower()
        return list(self._by_key.get((kind, key), []))

    # ------------------------------------------------------------------ 匹配

    def match(self, text: str) -> list[Match]:
        """在英文原文里找译名表条目，返回按首次出现位置排序的命中（每个条目一条）。"""
        masked = list(text)
        current = text
        hits: dict[tuple[str, str], Match] = {}
        for kind, lookup, pattern in self._patterns:
            spans = [m.span() for m in pattern.finditer(current)]
            if spans and kind in ("org", "person") and " " not in lookup:
                spans = [(s, e) for s, e in spans if not _embedded(text, s, e)]
            if not spans:
                continue
            candidates = self._by_key[(kind, lookup)]
            primary, others = candidates[0], candidates[1:]
            start, end = spans[0]
            matched = current[start:end]
            hit = hits.get(primary.ident)
            if hit is None:
                hit = Match(entry=primary, count=0, first_pos=start, ambiguous=bool(others), alternatives=list(others))
                hits[primary.ident] = hit
            hit.count += len(spans)
            hit.first_pos = min(hit.first_pos, start)
            if matched not in hit.matched_texts:
                hit.matched_texts.append(matched)
            if others:
                hit.ambiguous = True
                for other in others:
                    if other not in hit.alternatives:
                        hit.alternatives.append(other)
            for s, e in spans:
                for i in range(s, e):
                    masked[i] = "\0"
            current = "".join(masked)
        for hit in hits.values():
            if hit.entry.kind == "title":
                hit.related = self._related_titles(hit)
        return sorted(hits.values(), key=lambda m: (m.first_pos, m.entry.kind, m.entry.english))

    def _related_titles(self, hit: Match) -> list[Entry]:
        """以命中词开头、写明语境的职务条目（Vice President → Vice President of the United States）。已核的排前。"""
        heads = {hit.entry.key.lower(), *(t.lower() for t in hit.matched_texts)}
        shown = {hit.entry.ident, *(alt.ident for alt in hit.alternatives)}
        related = [
            e for e in self.entries
            if e.kind == "title" and e.ident not in shown
            and any(e.key.lower().startswith(h + " ") or e.key.lower().startswith(h + ",") for h in heads)
        ]
        related.sort(key=lambda e: (e.needs_review, len(e.key)))
        return related[:RELATED_TITLE_LIMIT]

    # ------------------------------------------------------------------ 生成后检查

    def check_output(self, text: str, matches: Iterable[Match]) -> list[dict]:
        """对照命中条目检查成稿：首次提及是否用了全称，再提是否用了简称，待核项提示复核。

        返回 issue 列表，每项 {level, rule, message, english}。级别只到 P1；
        要不要重写由 tools/lint 的 P0 决定，这里不越权。
        """
        issues: list[dict] = []
        matches = list(matches)
        all_forms = sorted(
            {f for m in matches for e in (m.entry, *m.alternatives) for f in e.forms if f}, key=len, reverse=True
        )

        def masked_for(entry: Entry) -> str:
            # 别的条目写法里含本条写法的先遮住：“白宫”不在“美国白宫科技政策办公室”里算一次出现。
            out = text
            for form in all_forms:
                if form not in entry.forms and any(own in form for own in entry.forms):
                    out = out.replace(form, "\0" * len(form))
            return out

        for match in matches:
            if match.entry.kind == "title":
                continue
            candidates = [match.entry, *match.alternatives]
            masked = {e.ident: masked_for(e) for e in candidates}
            present = [e for e in candidates if any(form in masked[e.ident] for form in e.forms)]
            if not present:
                issues.append(
                    {
                        "level": "P1",
                        "rule": "glossary-missing",
                        "english": match.entry.english,
                        "message": f"译名表条目“{match.entry.english}”在成稿里没有出现任何表内写法，"
                        f"应写“{match.entry.first}”（再提“{match.entry.later}”）",
                    }
                )
                continue
            entry = present[0]
            text_e = masked[entry.ident]
            first_pos = text_e.find(entry.first) if entry.first else -1
            has_distinct_later = bool(entry.later) and entry.later != entry.first
            later_positions = _find_all(text_e, entry.later) if has_distinct_later else []
            if first_pos < 0:
                issues.append(
                    {
                        "level": "P1",
                        "rule": "glossary-first-mention",
                        "english": entry.english,
                        "message": f"“{entry.english}”首次提及应用“{entry.first}”，成稿只用了“{entry.later}”",
                    }
                )
            elif later_positions and min(later_positions) < first_pos and not entry.first.startswith(entry.later):
                issues.append(
                    {
                        "level": "P1",
                        "rule": "glossary-first-mention",
                        "english": entry.english,
                        "message": f"“{entry.english}”在首次写全称“{entry.first}”之前先出现了简称“{entry.later}”",
                    }
                )
            full_count = text_e.count(entry.first)
            limit = 1 if entry.kind == "org" else 2
            if has_distinct_later and full_count > limit:
                issues.append(
                    {
                        "level": "P2",
                        "rule": "glossary-repeat-full",
                        "english": entry.english,
                        "message": f"“{entry.first}”出现 {full_count} 次，再提应用“{entry.later}”",
                    }
                )
            if entry.needs_review:
                issues.append(
                    {
                        "level": "P2",
                        "rule": "glossary-pending",
                        "english": entry.english,
                        "message": f"“{entry.english}”在译名表里是“{entry.status or '待核'}”"
                        f"（{entry.decided_by or '无依据'}），列入人工复核",
                    }
                )
        return issues


def _embedded(text: str, start: int, end: int) -> bool:
    """单词专名是否嵌在更长的大写专名里：前面紧挨一个非句首的大写词，或后面紧挨一个不是公司后缀的大写词。"""
    before = _WORD_BEFORE_RE.search(text[max(0, start - 40):start])
    if before:
        word = before.group(1)
        word_start = start - len(before.group(0))
        prefix = text[:word_start].rstrip()
        sentence_start = not prefix or prefix[-1] in ".!?:;—\n\"“(（"
        if word[:1].isupper() and word not in _LEADING_OK and not sentence_start:
            return True
    after = _WORD_AFTER_RE.match(text[end:end + 40])
    if after:
        word = after.group(1)
        if word[:1].isupper() and word not in _NAME_SUFFIXES and word.rstrip(".") not in _NAME_SUFFIXES:
            return True
    return False


def _find_all(text: str, needle: str) -> list[int]:
    if not needle:
        return []
    out = []
    start = 0
    while True:
        idx = text.find(needle, start)
        if idx < 0:
            return out
        out.append(idx)
        start = idx + len(needle)


def render_prompt_table(matches: Iterable[Match]) -> str:
    """把命中条目排成给模型看的 Markdown 表。"""
    rows = ["| 原文 | 类别 | 首次提及 | 再次提及 | 状态 | 说明 |", "|---|---|---|---|---|---|"]
    count = 0
    for m in matches:
        count += 1
        notes = []
        if m.ambiguous:
            alts = "；".join(f"{alt.english}→{alt.first}" for alt in m.alternatives)
            notes.append(f"同名多义，按语境选：{m.entry.english}→{m.entry.first}；{alts}")
        if m.entry.needs_review:
            notes.append("待核，照表写并列入复核清单")
        if m.entry.kind == "title" and m.entry.usage:
            notes.append(m.entry.usage[:60])
        if m.related:
            notes.append("相关条目，按语境选：" + "；".join(f"{rel.english}→{rel.first}" for rel in m.related))
        shown = m.entry.english if m.entry.kind == "title" else "、".join(m.matched_texts) or m.entry.english
        rows.append(
            "| {en} | {kind} | {first} | {later} | {status} | {note} |".format(
                en=_cell(shown),
                kind=m.entry.kind_zh,
                first=_cell(m.entry.first),
                later=_cell(m.entry.later),
                status=_cell(m.entry.status or "待核"),
                note=_cell("；".join(notes)),
            )
        )
    if count == 0:
        return "（原文没有命中译名表条目。表外专名按规范处理并列入 glossary_misses。）"
    return "\n".join(rows)


def _cell(text: str) -> str:
    return text.replace("|", "｜").replace("\n", " ")
