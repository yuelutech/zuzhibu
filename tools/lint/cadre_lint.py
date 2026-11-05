# -*- coding: utf-8 -*-
"""干部简历写法 lint。

依据 aipolis 规则 cadre.md、party_join.md、research.md（本仓库不带副本，要点见 shared/skills/cadre-resume/SKILL.md）
（aipolis 规则的只读副本）检查干部简历文本，只用标准库。

规则：
  CR01 叙述词          只写单位＋职务，不用"工作、从事、负责"等叙述词
  CR02 期间误用        在职学习、兼职括注用"其间"，不用"期间"
  CR03 日期格式        1985年－1993年；2001年1月－2003年1月；1993年4月至今；年月不补零
  CR04 中英文空格      英文和汉字之间空一格
  CR05 顿号合并        同一单位多个职务、现任多个职务用顿号连写
  CR06 未知待补字样    图上不出现"未知""待补""资料不详"等缺口词和占位符
  CR07 译名一致        机构、职务、人名对照 shared/glossary 三张表的简历写法列
                       待核写法只作提示：同一写法表里有一行已核就不提示；--country rw 时看英文键带卢旺达括注的行
  CR08 入党句格式      ××××年×月加入美国××党；无党派人士；放在国籍之后
  CR09 来源与推算痕迹  来源、推算、锚点、链接只进来源记录，不上图（附加规则）
  CR10 五项必写        姓名、性别、××籍、出生年月、参加工作时间齐全，后两项写到月
  CR11 日期粒度统一    缺月份的条目不超过 2 条或全篇四分之一就全篇写到月，否则全篇只写到年
  CR12 参加工作年龄    参加工作或入伍时不满 16 岁（多半借用了别人的数据）
  CR13 履历排序与衔接  按开始时间排，开始相同的先结束的在前；第一条工作与首段参加工作一致
  CR14 曾任与履历      首段“曾任××”要在履历里找得到
  CR15 国别附则        美国族裔只用五个词；卢旺达不写族裔、党职在前（--country 选国别）

用法：
  python -m tools.lint.cadre_lint 文件或目录 [更多路径] [选项]

退出码：0 无违规；1 有违规；2 参数错误或文件读不出来。
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

__all__ = [
    "RULES",
    "Violation",
    "Glossary",
    "load_glossary",
    "lint_text",
    "lint_file",
    "iter_files",
    "main",
]

__version__ = "0.1.0"

# 本文件在 tools/lint/ 下，仓库根目录在两级之上。
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_GLOSSARY_DIR = REPO_ROOT / "shared" / "glossary"
DEFAULT_EXTS = (".md", ".txt")

RULES: dict[str, str] = {
    "CR01": "叙述词",
    "CR02": "期间误用",
    "CR03": "日期格式",
    "CR04": "中英文空格",
    "CR05": "顿号合并",
    "CR06": "未知待补字样",
    "CR07": "译名一致",
    "CR08": "入党句格式",
    "CR09": "来源与推算痕迹",
    "CR10": "五项必写",
    "CR11": "日期粒度统一",
    "CR12": "参加工作年龄",
    "CR13": "履历排序与衔接",
    "CR14": "曾任与履历",
    "CR15": "国别附则",
}
COUNTRIES = ("us", "rw", "other")

# 规则文字里举例用的占位符（××）和项目名，不算违规。
BUILTIN_ALLOW: tuple[str, ...] = ("AI政见",)

MASK = "\x00"  # 遮蔽已核对或已报告的片段，长度不变，位置不动

CJK = "一-鿿㐀-䶿"
LATIN = "A-Za-zÀ-ÖØ-öø-ɏ"
RE_CJK_CHAR = re.compile(f"[{CJK}]")
RE_LATIN_CHAR = re.compile(f"[{LATIN}]")
RE_BOUNDARY = re.compile(f"(?<=[{CJK}])(?=[{LATIN}0-9])|(?<=[{LATIN}0-9])(?=[{CJK}])")
RE_MIDDLE_DOT = re.compile("[·•・‧∙]")

DATE = r"\d{4}年(?:\d{1,2}月)?"
RE_RANGE_OK = re.compile(rf"^\s*{DATE}(?:－{DATE}|至今)\s*$")
RE_SINGLE_DATE = re.compile(rf"^\s*{DATE}\s*$")
# 只含数字、年月日、连接号一类字符的行：当作条目的日期行（含写错的）。
RE_DATEISH = re.compile(r"^[\s\d０-９年月日至今现在迄起到间\-–—~～－.．/／、—]+$")
RE_HAS_YEAR = re.compile(r"[\d０-９]{4}")
RE_HEADING = re.compile(r"^\s*(#{1,6}\s|>)")

# ---- CR01 叙述词 ---------------------------------------------------------
# cadre.md 第 2.6 条列出的词是核心清单；人事三局 RULES.md 第七条的传记腔词作补充。
NARRATIVE_CORE = (
    "工作", "后任", "先后", "从事", "参与", "负责", "曾为", "服务于", "活跃于",
    "职业生涯", "帮助", "协助", "支持",
)
NARRATIVE_EXTRA = (
    "毕业后", "退役后", "随后", "此后", "后来", "开始从政", "转入政界", "投身于",
    "致力于", "担任过", "曾在", "长期从事", "主要负责", "拥有丰富经验", "推动了",
    "见证了", "曾任", "历任", "期间任", "任职于", "就职于", "效力于", "加盟",
)
# 叙述词恰好是固定句式或职务名一部分的，不算。
NARRATIVE_EXCEPTIONS = (
    "参加工作", "负责人", "工作人员", "工作组", "工作委员会", "社会工作", "工作室",
    "工作部", "工作会议", "工作站", "支持中心", "参与者",
)
RE_NARRATIVE = re.compile(
    "|".join(re.escape(w) for w in sorted(NARRATIVE_CORE + NARRATIVE_EXTRA, key=len, reverse=True))
)
# "加入"后面不是政党的，是叙述（"2005年加入谷歌公司"）。
RE_JOIN_NON_PARTY = re.compile(r"加入(?![^，。；：、（）\s]{0,20}(?:党|联盟|阵线|同盟|无党派))")

# ---- CR02 期间 -----------------------------------------------------------
RE_QIJIAN_WRONG = re.compile(r"期间")
RE_QIJIAN_NO_COLON = re.compile(r"其间(?!：)")
RE_QIJIAN_NO_BRACKET = re.compile(r"(?<![（(])其间：")

# ---- CR06 未知待补 -------------------------------------------------------
PLACEHOLDER_WORDS = (
    "未知", "待补", "待定", "待核", "待查", "待确认", "待复核", "待考", "待填", "待补充",
    "不详", "未查到", "查无", "不明", "暂缺", "暂无", "空缺", "无记录", "缺失",
    "资料不完整", "资料显示不完整", "占位", "尚不清楚", "不清楚",
)
FUZZY_WORDS = ("大概", "左右", "可能", "疑似", "据说", "据称", "或为", "约为", "约在")
RE_PLACEHOLDER_WORDS = re.compile(
    "|".join(re.escape(w) for w in sorted(PLACEHOLDER_WORDS, key=len, reverse=True))
)
RE_FUZZY_WORDS = re.compile("|".join(re.escape(w) for w in sorted(FUZZY_WORDS, key=len, reverse=True)))
RE_FUZZY_ABOUT = re.compile(r"约\s*[0-9０-９一二三四五六七八九十]")
RE_PLACEHOLDER_SYMBOLS = (
    (re.compile(r"×{2,}"), "模板占位符「××」没有填"),
    (re.compile(r"[XＸ]{2,}"), "占位符「XX」没有填"),
    (re.compile(r"□+|＿{2,}|_{2,}"), "空格占位符没有填"),
    (re.compile(r"\b(?:N/A|TBD|TODO|FIXME)\b", re.IGNORECASE), "英文占位符不上图"),
    (re.compile(r"[？?]"), "问号表示事实未定，缺项直接略去"),
    (re.compile(r"\[\s*\]|【\s*】|（\s*）|\(\s*\)"), "空括号是没填的缺项"),
)

# ---- CR09 来源与推算痕迹 -------------------------------------------------
RE_SOURCE_PATTERNS = (
    (re.compile(r"https?://\S+|www\.\S+"), "链接只进来源记录，不上图"),
    (re.compile(r"来源|出处|引自|参见|参考"), "来源说明只进来源记录，不上图"),
    (re.compile(r"推算|推断|推定|估算|倒推"), "推算标记只进来源记录，图上直接写结果"),
    (re.compile(r"锚点"), "入党锚点和级别只进来源和核对记录"),
    (re.compile(r"核查|核对|核实|复核"), "核对过程不上图"),
    (re.compile(r"维基|百科|Wikipedia", re.IGNORECASE), "百科只用来找线索，不出现在简历里"),
    (re.compile(r"\[\d+\]|［\d+］|[①-⑳]|\^\d+"), "不加来源编号和脚注"),
    (re.compile(r"据[^，。；]{0,15}(?:报道|披露|显示|记载|介绍|称)"), "不写转述来源的句子"),
)

# ---- CR08 入党句 ---------------------------------------------------------
RE_JOIN_PARTY = re.compile(
    r"加入(?P<party>[^，。；：、（）\s]{2,20}?(?:党|联盟|阵线|同盟))(?P<tail>党员|党籍|人|成员)?(?=[，。；：、（）\s]|$)"
)
RE_PARTY_MEMBER_WORDS = re.compile(r"(?:共和党|民主党)(?:人|籍|党员|员|议员)|党员|党籍|入党(?!句)")
RE_PARTY_ENGLISH = re.compile(r"\b(?:Republican|Democrat(?:ic|s)?|Independent|GOP)\b")
RE_NONPARTISAN_WRONG = re.compile(r"无党籍|独立人士|独立派人士|独立派|无党派者|无党派(?!人士)")
RE_NATIONALITY = re.compile(rf"[{CJK}]{{1,8}}(?:籍|人)，")  # 现行写“××籍”，旧稿写“××人”
RE_WORK_START = re.compile(r"(?P<date>\d{4}年(?:\d{1,2}月)?)(?:参加工作|入伍)")
RE_FIRST_PARA_HINT = re.compile(r"，[男女]，|出生")

# ---- CR03 日期 -----------------------------------------------------------
RE_FULLWIDTH_DIGITS = re.compile(r"[０-９]+")
RE_CN_NUMERAL_YEAR = re.compile(r"[一二三四五六七八九零〇○]{2,4}年")
RE_RANGE_NO_NIAN = re.compile(r"(?<![\d.．/／])(\d{4})\s*[-–—~～－至到]\s*(\d{4})(?![\d年])")
RE_NUMERIC_DATE = re.compile(r"(?<!\d)\d{4}[-./．／]\d{1,2}(?:[-./．／]\d{1,2})?(?!\d)")
RE_WRONG_DASH = re.compile(rf"{DATE}\s*(?:[-–—~～至到]+|－\s+|\s+－)\s*(?=\d{{4}})")
RE_END_NO_NIAN = re.compile(rf"{DATE}\s*[-–—~～－至到]+\s*\d{{4}}(?![\d年])")
RE_START_NO_NIAN = re.compile(r"(?<![\d.．/／])\d{4}\s*[-–—~～－至到]+\s*\d{4}年")
RE_DASH_BEFORE_NOW = re.compile(r"[-–—~～－]\s*至今")
RE_NOW_WRONG = re.compile(r"至现在|到现在|迄今|至目前|到目前|至现任|到今|－今|至今日")
RE_PADDED_MONTH = re.compile(r"(?<!\d)0\d月")
RE_DAY = re.compile(r"\d{1,2}月\d{1,2}日")
RE_MONTH_RANGE = re.compile(r"(?<!\d)(?:1[3-9]|[2-9]\d|00?)月")
RE_SPACE_IN_DATE = re.compile(r"\d[ \t　]+[年月]")
RE_FUZZY_PERIOD = re.compile(r"月份|年代|年初|年中|年末|年底|上半年|下半年|月初|月中|月底|月末")
RE_RANGE_FULL = re.compile(r"(\d{4})年(?:(\d{1,2})月)?－(\d{4})年(?:(\d{1,2})月)?")

# ---- CR05 顿号 -----------------------------------------------------------
RE_DUNHAO_DOUBLE = re.compile(r"、{2,}")
RE_DUNHAO_MISPLACED = re.compile(r"(?:^|[（(，。；：])\s*、|、\s*(?:[，。；：）)]|$)", re.MULTILINE)
RE_ASCII_COMMA_CJK = re.compile(rf"[{CJK}）]\s*,\s*[{CJK}（]")
RE_SLASH_CJK = re.compile(rf"[{CJK}][／/][{CJK}]")
ORG_SUFFIX = (
    "公司", "集团", "大学", "学院", "学校", "研究院", "研究所", "实验室", "委员会", "银行",
    "基金会", "事务所", "中心", "办公室", "部", "局", "署", "厅", "院", "会", "社", "报",
    "台", "馆", "厂", "所", "队", "处", "司", "室",
)
RE_ORG_PREFIX = re.compile(
    "^(.{1,30}?(?:" + "|".join(sorted(ORG_SUFFIX, key=len, reverse=True)) + "))"
)


# =============================================================================
# 结果与文档
# =============================================================================


@dataclass
class Violation:
    """一处违规（或 level 为 note 的提示）。"""

    code: str
    message: str
    line: int
    col: int
    text: str
    path: str = ""
    level: str = "error"

    @property
    def rule(self) -> str:
        return RULES.get(self.code, "")

    def format(self) -> str:
        where = f"{self.path}:{self.line}:{self.col}" if self.path else f"{self.line}:{self.col}"
        tag = f"{self.code}" if self.level == "error" else f"{self.code}(提示)"
        snippet = self.text.replace("\n", "\\n")
        return f"{where}: {tag} {self.rule}：{self.message}：「{snippet}」"

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "line": self.line,
            "col": self.col,
            "code": self.code,
            "rule": self.rule,
            "level": self.level,
            "message": self.message,
            "text": self.text,
        }


class Doc:
    """待检查文本，负责偏移量到行列的换算。"""

    def __init__(self, text: str, path: str = ""):
        self.text = text
        self.path = path
        self.lines = text.split("\n")
        self.line_starts = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                self.line_starts.append(i + 1)

    def pos(self, offset: int) -> tuple[int, int]:
        line = bisect.bisect_right(self.line_starts, offset)
        col = offset - self.line_starts[line - 1] + 1
        return line, col

    def violation(self, code: str, message: str, start: int, end: int, level: str = "error") -> Violation:
        line, col = self.pos(start)
        snippet = self.text[start:end]
        if len(snippet) > 60:
            snippet = snippet[:57] + "…"
        return Violation(code, message, line, col, snippet, self.path, level)

    def line_span(self, index: int) -> tuple[int, int]:
        """第 index 行（从 0 起）在全文中的起止偏移。"""
        start = self.line_starts[index]
        end = start + len(self.lines[index])
        return start, end


def apply_mask(text: str, spans: Iterable[tuple[int, int]]) -> str:
    buf = list(text)
    for start, end in spans:
        for i in range(start, min(end, len(buf))):
            buf[i] = MASK
    return "".join(buf)


def find_spans(pattern: Optional[re.Pattern], text: str) -> list[tuple[int, int]]:
    if pattern is None:
        return []
    return [(m.start(), m.end()) for m in pattern.finditer(text)]


def literal_pattern(phrases: Iterable[str]) -> Optional[re.Pattern]:
    phrases = [p for p in phrases if p]
    if not phrases:
        return None
    return re.compile("|".join(re.escape(p) for p in sorted(set(phrases), key=len, reverse=True)))


def split_outside_brackets(text: str, sep: str) -> list[str]:
    """按 sep 切分，括号里的 sep 不算。"""
    parts, depth, buf = [], 0, []
    for ch in text:
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
        if ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return parts


def positions_outside_brackets(text: str, chars: str) -> list[int]:
    out, depth = [], 0
    for i, ch in enumerate(text):
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
        elif depth == 0 and ch in chars:
            out.append(i)
    return out


def strip_parenthetical(text: str) -> str:
    out, depth = [], 0
    for ch in text:
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out).strip()


def is_dateish_line(line: str) -> bool:
    s = line.strip()
    return bool(s) and bool(RE_DATEISH.match(s)) and bool(RE_HAS_YEAR.search(s))


def is_heading_line(line: str) -> bool:
    return bool(RE_HEADING.match(line))


# =============================================================================
# 译名表
# =============================================================================


def _tolerant_pattern(term: str) -> str:
    """把译名表写法变成正则：中英文之间可有可无一个空格，人名间隔号允许写错。"""
    parts = RE_BOUNDARY.split(term)
    escaped = []
    for part in parts:
        esc = re.escape(part)
        esc = RE_MIDDLE_DOT.sub(lambda _m: "[·•・‧∙]", esc)
        escaped.append(esc)
    return " ?".join(escaped)


def _split_standard(value: str) -> list[str]:
    """职务表的"首席法务官／总法律顾问"是两种写法；含 ×× 的是模板，不用。"""
    out = []
    for part in re.split(r"[／/]", value or ""):
        part = part.strip()
        if part and "×" not in part:
            out.append(part)
    return out


def _parse_variants(value: str) -> list[str]:
    """历史写法列：'奥特曼（第57页，只写姓）；山姆·奥特曼（CEO雷达）' → ['奥特曼', '山姆·奥特曼']"""
    out = []
    for part in re.split(r"[；;]", value or ""):
        v = strip_parenthetical(part).strip("“”\"'「」 ")
        if v:
            out.append(v)
    return out


def _parse_buxie(usage: str) -> list[str]:
    """职务表用法说明里"不写CEO、行政总裁、执行长"一类说明 → ['CEO', '行政总裁', '执行长']"""
    out = []
    for m in re.finditer(r"不写(?:成|作|为)?([^。；;]+)", usage or ""):
        for seg in re.split(r"[、，,]", m.group(1)):
            seg = strip_parenthetical(seg).strip("“”\"'「」 ")
            if not seg or len(seg) > 8:
                continue
            if re.search(r"避免|混淆|用语|误|不|宜|可", seg):
                continue
            out.append(seg)
    return out


def _clean_key(key: str) -> list[str]:
    """英文查找键：去括注，按 / 拆成几个，太短或含占位符的不要。"""
    key = re.sub(r"[（(][^）)]*[）)]", "", key or "")
    out = []
    for part in re.split(r"\s*[/／;]\s*", key):
        part = part.strip()
        if "×" in part or len(re.findall(f"[{LATIN}]", part)) < 3:
            continue
        out.append(part)
    return out


# 只差一个国名、州名、"白宫""联邦"一类前缀的写法，不算译名不一致（国名加不加按 译名表使用说明 国名规则）。
RE_QUALIFIER = re.compile(
    r"^(?:美国|英国|德国|法国|日本|韩国|印度|加拿大|澳大利亚|卢旺达|以色列|新加坡|中国|中华人民共和国|欧盟|欧洲"
    r"|白宫|联邦|总统|国会|国家|[一-鿿]{1,5}州)"
)


def _core(term: str) -> str:
    prev = None
    while prev != term:
        prev = term
        term = RE_QUALIFIER.sub("", term, count=1)
    return term


RE_URL = re.compile(r"https?://\S+|www\.\S+|\S+@\S+\.\S+")
RE_LATIN_RUN = re.compile(f"[{LATIN}0-9][{LATIN}0-9 &.'’\\-/]*[{LATIN}0-9]|[{LATIN}0-9]")


@dataclass
class Glossary:
    """三张译名表的查找索引。"""

    standards: dict[str, str] = field(default_factory=dict)  # 写法 -> 表名
    pending: set[str] = field(default_factory=set)  # 待核写法：有待核行、同一写法没有一行已核（不分国别）
    full_forms: dict[str, str] = field(default_factory=dict)  # 全称 -> 简历写法（两者不同时）
    variants: dict[str, set[str]] = field(default_factory=dict)  # 旧写法 -> 标准写法集合
    english: dict[str, set[tuple[str, str]]] = field(default_factory=dict)  # 英文键 -> {(表中原键, 标准写法)}
    counts: dict[str, int] = field(default_factory=dict)
    source_dir: str = ""
    _variant_pairs: list[tuple[str, str, str]] = field(default_factory=list, repr=False)  # (表, 旧写法, 标准写法)
    _status_rows: dict[str, list[tuple[str, str]]] = field(default_factory=dict, repr=False)  # 写法 -> [(英文键, status)]

    _re_std: Optional[re.Pattern] = field(default=None, repr=False)
    _re_full: Optional[re.Pattern] = field(default=None, repr=False)
    _re_variant: Optional[re.Pattern] = field(default=None, repr=False)
    _re_en_cs: Optional[re.Pattern] = field(default=None, repr=False)
    _re_en_ci: Optional[re.Pattern] = field(default=None, repr=False)
    _lookup_std: dict[str, str] = field(default_factory=dict, repr=False)
    _lookup_full: dict[str, str] = field(default_factory=dict, repr=False)
    _lookup_variant: dict[str, str] = field(default_factory=dict, repr=False)

    # ---- 建索引 ----
    def finalize(self) -> "Glossary":
        all_std = set(self.standards)
        self.pending = {t for t in self._status_rows if self.is_pending(t)}

        variants: dict[str, set[str]] = {}
        for table, v, std in self._variant_pairs:
            if len(v) < 2 or v in all_std or not std:
                continue
            if any(v in s for s in all_std):  # 是某个标准写法的一部分，太泛，不报
                continue
            if table in ("org", "title") and (v.endswith(std) or std.endswith(v) or _core(v) == _core(std)):
                continue  # 只差国名、州名一类前缀，加不加按译名表使用说明的国名规则，不算译名不一致
            variants.setdefault(v, set()).add(std)
        self.variants = variants
        self.full_forms = {f: s for f, s in self.full_forms.items() if f and f != s and f not in all_std}
        self.english = {k: v for k, v in self.english.items() if k not in all_std}

        def build(terms: Iterable[str], flags: int = 0) -> tuple[Optional[re.Pattern], dict[str, str]]:
            terms = sorted(set(terms), key=len, reverse=True)
            if not terms:
                return None, {}
            lookup = {_normalize(t): t for t in terms}
            pat = re.compile("|".join(_tolerant_pattern(t) for t in terms), flags)
            return pat, lookup

        self._re_std, self._lookup_std = build(self.standards)
        self._re_full, self._lookup_full = build(self.full_forms)
        self._re_variant, self._lookup_variant = build(self.variants)
        short = [k for k in self.english if len(k) <= 4]
        long_ = [k for k in self.english if len(k) > 4]
        self._re_en_cs = re.compile(r"(?<![A-Za-z])(?:" + "|".join(re.escape(k) for k in sorted(short, key=len, reverse=True)) + r")(?![A-Za-z])") if short else None
        self._re_en_ci = re.compile(
            r"(?<![A-Za-z])(?:" + "|".join(re.escape(k) for k in sorted(long_, key=len, reverse=True)) + r")(?![A-Za-z])",
            re.IGNORECASE,
        ) if long_ else None
        return self

    # ---- 查找 ----
    def is_pending(self, std: str, country: Optional[str] = None) -> bool:
        """写法是否待核：同一写法表里只要有一行已核就不算待核。
        --country rw 时，同一写法有英文键带卢旺达括注的行（如 Permanent Secretary (Rwanda)），只看这几行。"""
        rows = self._status_rows.get(std, [])
        if country in COUNTRY_KEY_TAGS:
            rows = [r for r in rows if _country_tagged(r[0], country)] or rows
        statuses = {s for _k, s in rows}
        return "已核" not in statuses and "待核" in statuses

    def _resolve(self, lookup: dict[str, str], matched: str) -> str:
        return lookup.get(_normalize(matched), matched)

    def standard_spans(self, text: str) -> list[tuple[int, int, str]]:
        return [(m.start(), m.end(), self._resolve(self._lookup_std, m.group())) for m in (self._re_std.finditer(text) if self._re_std else [])]

    def full_form_spans(self, text: str) -> list[tuple[int, int, str]]:
        return [(m.start(), m.end(), self._resolve(self._lookup_full, m.group())) for m in (self._re_full.finditer(text) if self._re_full else [])]

    def variant_spans(self, text: str) -> list[tuple[int, int, str]]:
        return [(m.start(), m.end(), self._resolve(self._lookup_variant, m.group())) for m in (self._re_variant.finditer(text) if self._re_variant else [])]

    def english_spans(self, text: str) -> list[tuple[int, int, str]]:
        """文中的英文键。嵌在更长英文串里的不算（King & Spalding 里的 King 不是国王）。"""
        runs = [(m.start(), m.end()) for m in RE_LATIN_RUN.finditer(text)]
        out = []
        for pat in (self._re_en_cs, self._re_en_ci):
            if pat is None:
                continue
            for m in pat.finditer(text):
                run = next(((s, e) for s, e in runs if s <= m.start() and m.end() <= e), None)
                if run and text[run[0]:run[1]].strip().lower() != m.group().strip().lower():
                    continue
                key = m.group()
                if key not in self.english:
                    for k in self.english:
                        if k.lower() == key.lower():
                            key = k
                            break
                out.append((m.start(), m.end(), key))
        out.sort()
        return out


def _normalize(term: str) -> str:
    t = RE_MIDDLE_DOT.sub("·", term)
    return re.sub(r"\s+", "", t)


# 国别开关对应的英文键括注：--country rw 时优先用英文键括注里有 Rwanda 的那几行
COUNTRY_KEY_TAGS = {"rw": "rwanda"}


def _country_tagged(key: str, country: Optional[str]) -> bool:
    tag = COUNTRY_KEY_TAGS.get(country or "")
    return bool(tag) and any(tag in p.lower() for p in re.findall(r"\(([^)]*)\)", key or ""))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = []
        for row in csv.DictReader(f):
            rows.append({(k or "").strip(): (v or "").strip() for k, v in row.items()})
        return rows


def _find_table(directory: Path, suffix: str) -> Optional[Path]:
    cands = sorted(p for p in directory.glob(f"*{suffix}") if p.is_file())
    return cands[-1] if cands else None  # 版本号最大的排在最后


def load_glossary(directory: os.PathLike | str = DEFAULT_GLOSSARY_DIR) -> Glossary:
    """读 shared/glossary 的机构、职务、人名三张表。

    列的取法按 译名表使用说明.md 第三节：简历用 机构.简历写法、职务.干部简历写法，
    人名用"本人汉名或通行汉名"（有值且备注未标待核、未核），否则用新华社译名。
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"译名表目录不存在：{directory}")
    g = Glossary(source_dir=str(directory))

    def add_std(term: str, table: str, status: str, key: str = "") -> None:
        for t in _split_standard(term):
            g.standards.setdefault(t, table)
            g._status_rows.setdefault(t, []).append((key.strip(), status))

    def add_variant(table: str, v: str, std: str) -> None:
        if v and std and v != std:
            g._variant_pairs.append((table, v, std))

    def add_english(key: str, std: str) -> None:
        for k in _clean_key(key):
            if std and k != std:
                g.english.setdefault(k, set()).add((key.strip(), std))

    found = 0
    org_path = _find_table(directory, "_org.csv")
    if org_path:
        found += 1
        rows = _read_csv(org_path)
        g.counts["org"] = len(rows)
        for r in rows:
            stds = _split_standard(r.get("简历写法", ""))
            std = stds[0] if stds else ""
            add_std(r.get("简历写法", ""), "org", r.get("status", ""), r.get("english_name", ""))
            full = r.get("全称", "")
            if full and std and full != std:
                g.full_forms.setdefault(full, std)
            for v in _parse_variants(r.get("历史写法", "")):
                add_variant("org", v, std)
            add_english(r.get("english_name", ""), std)

    title_path = _find_table(directory, "_title.csv")
    if title_path:
        found += 1
        rows = _read_csv(title_path)
        g.counts["title"] = len(rows)
        for r in rows:
            stds = _split_standard(r.get("干部简历写法", ""))
            std = stds[0] if stds else ""
            add_std(r.get("干部简历写法", ""), "title", r.get("status", ""), r.get("english_title", ""))
            for v in _parse_variants(r.get("历史写法", "")):
                add_variant("title", v, std)
            for v in _parse_buxie(r.get("用法说明", "")):
                add_variant("title", v, std)
            add_english(r.get("english_title", ""), std)

    person_path = _find_table(directory, "_person.csv")
    if person_path:
        found += 1
        rows = _read_csv(person_path)
        g.counts["person"] = len(rows)
        for r in rows:
            xinhua = r.get("新华社译名", "")
            hanming = r.get("本人汉名或通行汉名", "")
            note = r.get("备注", "")
            if hanming and not any(mark in note for mark in ("待核", "未核")):
                std, other = hanming, xinhua
            else:
                std, other = xinhua, hanming
            if not std:
                continue
            add_std(std, "person", r.get("status", ""), r.get("english_name", ""))
            add_variant("person", other, std)
            for v in _parse_variants(r.get("历史写法", "")):
                add_variant("person", v, std)
            add_english(r.get("english_name", ""), std)

    if found == 0:
        raise FileNotFoundError(f"目录里没有 *_org.csv、*_title.csv、*_person.csv：{directory}")
    return g.finalize()


# =============================================================================
# 各条规则
# =============================================================================


def _flag_all(doc: Doc, text: str, pattern: re.Pattern, code: str, message: str, out: list[Violation]) -> None:
    for m in pattern.finditer(text):
        out.append(doc.violation(code, message, m.start(), m.end()))


def check_narrative(doc: Doc, text: str, raw: Optional[str] = None) -> list[Violation]:
    """text 遮过译名表标准写法；raw 没遮（默认同 text）。"""
    out: list[Violation] = []
    text = apply_mask(text, find_spans(literal_pattern(NARRATIVE_EXCEPTIONS), text))
    for m in RE_NARRATIVE.finditer(text):
        word = m.group()
        kind = "叙述词" if word in NARRATIVE_CORE else "传记腔词"
        out.append(doc.violation("CR01", f"只写单位＋职务，不用{kind}「{word}」", m.start(), m.end()))
    # “加入”后面是不是政党要看原文：政党全称（如“卢旺达爱国阵线”）本身是译名表标准写法，遮住后前瞻看不到“阵线”“党”。
    raw = text if raw is None else raw
    for m in re.finditer("加入", text):
        if RE_JOIN_NON_PARTY.match(raw, m.start()):
            out.append(doc.violation("CR01", "「加入」只用于入党句，任职写成单位＋职务", m.start(), m.end()))
    return out


def check_qijian(doc: Doc, text: str) -> list[Violation]:
    out: list[Violation] = []
    _flag_all(doc, text, RE_QIJIAN_WRONG, "CR02", "在职学习、兼职括注用「其间」，不用「期间」", out)
    _flag_all(doc, text, RE_QIJIAN_NO_COLON, "CR02", "「其间」后面用全角冒号：（其间：……）", out)
    _flag_all(doc, text, RE_QIJIAN_NO_BRACKET, "CR02", "「其间」写在同期任职条目下的括号里：（其间：……）", out)
    return out


def check_dates(doc: Doc, text: str) -> list[Violation]:
    out: list[Violation] = []
    t = text

    def flag(pattern: re.Pattern, message: str, mask: bool = False) -> None:
        nonlocal t
        spans = []
        for m in pattern.finditer(t):
            out.append(doc.violation("CR03", message, m.start(), m.end()))
            spans.append((m.start(), m.end()))
        if mask and spans:
            t = apply_mask(t, spans)

    flag(RE_FULLWIDTH_DIGITS, "数字用半角阿拉伯数字", mask=True)
    flag(RE_CN_NUMERAL_YEAR, "年份用阿拉伯数字", mask=True)
    flag(RE_RANGE_NO_NIAN, "起止年份都带「年」字，写作「1985年－1993年」", mask=True)
    flag(RE_NUMERIC_DATE, "不写数字日期，写作「2001年1月」", mask=True)
    flag(RE_WRONG_DASH, "起止之间用全角连接号「－」，前后不空格", mask=True)
    flag(RE_END_NO_NIAN, "结束年份带「年」字", mask=True)
    flag(RE_START_NO_NIAN, "起始年份带「年」字", mask=True)
    flag(RE_DASH_BEFORE_NOW, "在任写「1993年4月至今」，「至今」前不加连接号", mask=True)
    flag(RE_NOW_WRONG, "在任写「至今」", mask=True)
    flag(RE_PADDED_MONTH, "月份不补零，写「3月」不写「03月」", mask=True)
    flag(RE_DAY, "日期只写到月，不写日", mask=True)
    flag(RE_MONTH_RANGE, "月份只能是1月到12月", mask=True)
    flag(RE_SPACE_IN_DATE, "数字和「年」「月」之间不空格", mask=True)
    flag(RE_FUZZY_PERIOD, "日期只写到年或月，不写「年代」「年初」「月份」一类模糊时段")

    for m in RE_RANGE_FULL.finditer(t):
        y1, m1, y2, m2 = m.groups()
        start = (int(y1), int(m1) if m1 else 0)
        end = (int(y2), int(m2) if m2 else 0)
        if end[0] < start[0] or (end[0] == start[0] and m1 and m2 and end[1] < start[1]):
            out.append(doc.violation("CR03", "起止时间颠倒，开始时间在前", m.start(), m.end()))

    # 条目：第一行起止时间，第二行单位＋职务。
    flagged_lines = {v.line for v in out}
    n = len(doc.lines)
    for i, line in enumerate(doc.lines):
        if not is_dateish_line(line):
            continue
        start, end = doc.line_span(i)
        if (i + 1) not in flagged_lines and not RE_RANGE_OK.match(line) and not RE_SINGLE_DATE.match(line):
            out.append(doc.violation(
                "CR03", "起止时间写作「1985年－1993年」「2001年1月－2003年1月」或「1993年4月至今」", start, end
            ))
        j = i + 1
        while j < n and not doc.lines[j].strip():
            j += 1
        if j >= n or is_dateish_line(doc.lines[j]):
            out.append(doc.violation("CR03", "每条两行：日期行下面要有单位＋职务行", start, end))
    return out


def check_spacing(doc: Doc, text: str, mode: str = "require") -> list[Violation]:
    out: list[Violation] = []
    if mode == "off":
        return out
    _flag_all(doc, text, re.compile("　+"), "CR04", "不用全角空格", out)
    _flag_all(doc, text, re.compile("\t+"), "CR04", "不用制表符", out)
    _flag_all(doc, text, re.compile(rf"(?<=[{CJK}{LATIN}0-9]) +(?=[（）、，。：；])|(?<=[（、，：；]) +(?=[{CJK}{LATIN}0-9])"),
              "CR04", "全角标点前后不空格", out)
    if mode == "require":
        _flag_all(doc, text, re.compile(f"[{CJK}][{LATIN}]|[{LATIN}][{CJK}]"), "CR04", "英文和汉字之间空一格", out)
        _flag_all(doc, text, re.compile(f"[{CJK}] {{2,}}[{LATIN}]|[{LATIN}] {{2,}}[{CJK}]"), "CR04", "英文和汉字之间只空一格", out)
    elif mode == "forbid":
        _flag_all(doc, text, re.compile(f"[{CJK}] +[{LATIN}]|[{LATIN}] +[{CJK}]"), "CR04", "英文和汉字之间不空格", out)
    return out


def _entry_contents(doc: Doc) -> list[tuple[int, int, str]]:
    """履历条目的内容部分：(全文起始偏移, 行号索引, 内容)。支持两行一条和一行一条。"""
    out = []
    n = len(doc.lines)
    i = 0
    while i < n:
        line = doc.lines[i]
        start, _ = doc.line_span(i)
        if is_dateish_line(line):
            j = i + 1
            while j < n and not doc.lines[j].strip():
                j += 1
            if j < n and not is_dateish_line(doc.lines[j]) and not is_heading_line(doc.lines[j]):
                cstart, _ = doc.line_span(j)
                content = doc.lines[j]
                lead = len(content) - len(content.lstrip())
                out.append((cstart + lead, j, content.strip()))
                i = j + 1
                continue
        else:
            m = re.match(
                r"^\s*\d{4}年?(?:\d{1,2}月)?(?:\s*[-–—~～－至到]+\s*\d{4}年?(?:\d{1,2}月)?|至今)?[ \t　]+(\S.*)$",
                line,
            )
            if m and not is_heading_line(line):
                out.append((start + m.start(1), i, m.group(1).strip()))
        i += 1
    return out


def check_dunhao(doc: Doc, text: str, glossary: Optional[Glossary]) -> list[Violation]:
    """text 是只遮了放行词的文本；条目里的逗号检查另外遮住译名表标准写法（有的写法本身带逗号）。"""
    out: list[Violation] = []
    _flag_all(doc, text, RE_DUNHAO_DOUBLE, "CR05", "顿号不重复", out)
    _flag_all(doc, text, RE_DUNHAO_MISPLACED, "CR05", "顿号只放在并列的职务之间", out)
    _flag_all(doc, text, RE_ASCII_COMMA_CJK, "CR05", "中文里不用半角逗号，并列职务用顿号", out)
    _flag_all(doc, text, RE_SLASH_CJK, "CR05", "并列职务用顿号连写，不用斜线", out)

    # 现任句：一段只一个"现任"，各职务用顿号连接。
    for i, line in enumerate(text.split("\n")):
        if "现任" not in line:
            continue
        lstart, _ = doc.line_span(i)
        hits = [m.start() for m in re.finditer("现任", line)]
        if len(hits) > 1:
            out.append(doc.violation("CR05", "一段只用一个「现任」，多个职务用顿号连接", lstart + hits[1], lstart + hits[1] + 2))
        sent_start = hits[0]
        sent_end = line.find("。", sent_start)
        sentence = line[sent_start:] if sent_end < 0 else line[sent_start:sent_end]
        for p in positions_outside_brackets(sentence, "，,"):
            out.append(doc.violation("CR05", "现任各职务用顿号连接，不用逗号", lstart + sent_start + p, lstart + sent_start + p + 1))

    # 履历条目：只写单位＋职务，并列职务用顿号；顿号后不重复单位名。
    for cstart, _idx, content in _entry_contents(doc):
        masked = content
        if glossary is not None:
            masked = apply_mask(content, [(s, e) for s, e, _t in glossary.standard_spans(content)])
        # 学习条目“××专业学习，获××学位”的逗号是固定写法（rules/cadre.md 第 2 节第 3 条）
        masked = re.sub(r"(?<=学习)，(?=获)", MASK, masked)
        for p in positions_outside_brackets(masked, "，,"):
            out.append(doc.violation("CR05", "条目只写单位＋职务，并列职务用顿号连接，说明放括号里", cstart + p, cstart + p + 1))
        body = strip_parenthetical(content)
        segments = split_outside_brackets(body, "、")
        if len(segments) < 2:
            continue
        unit = ""
        if glossary is not None:
            for s, e, _t in glossary.standard_spans(segments[0]):
                if s == 0 and glossary.standards.get(_t) == "org":
                    unit = segments[0][:e]
                    break
        if not unit:
            m = RE_ORG_PREFIX.match(segments[0])
            unit = m.group(1) if m else ""
        seen = {segments[0][len(unit):] if unit else segments[0]}  # 第一段去掉单位名才是职务
        offset = len(segments[0]) + 1
        for seg in segments[1:]:
            seg_pos = content.find(seg, offset) if seg else -1
            where = cstart + (seg_pos if seg_pos >= 0 else 0)
            if unit and len(unit) >= 2 and seg.startswith(unit) and seg != segments[0]:
                out.append(doc.violation("CR05", f"顿号合并同一单位的职务时不重复单位名「{unit}」", where, where + len(seg)))
            elif seg in seen:
                out.append(doc.violation("CR05", "并列职务重复", where, where + len(seg)))
            seen.add(seg)
            offset = (seg_pos if seg_pos >= 0 else offset) + len(seg) + 1
    return out


def check_placeholders(doc: Doc, text: str) -> list[Violation]:
    out: list[Violation] = []
    for m in RE_PLACEHOLDER_WORDS.finditer(text):
        out.append(doc.violation("CR06", f"缺项直接略去，图上不出现「{m.group()}」", m.start(), m.end()))
    for m in RE_FUZZY_WORDS.finditer(text):
        out.append(doc.violation("CR06", f"不用暴露资料缺口的模糊词「{m.group()}」", m.start(), m.end()))
    _flag_all(doc, text, RE_FUZZY_ABOUT, "CR06", "不写「约」加数字，查不到就略去", out)
    for pattern, message in RE_PLACEHOLDER_SYMBOLS:
        _flag_all(doc, text, pattern, "CR06", message, out)
    return out


def check_glossary(doc: Doc, text: str, glossary: Glossary, country: Optional[str] = None) -> list[Violation]:
    out: list[Violation] = []
    t = text

    def mask(spans: Iterable[tuple[int, int]]) -> None:
        nonlocal t
        t = apply_mask(t, spans)

    mask(find_spans(RE_URL, t))  # 链接由 CR09 报，里面的英文不当译名查

    # 0) 人名间隔号：要在遮住标准写法之前查，因为匹配标准写法时允许间隔号写错。
    for m in re.finditer(f"(?<=[{CJK}])[•・‧∙](?=[{CJK}])", t):
        out.append(doc.violation("CR07", "人名间隔号用「·」", m.start(), m.end()))

    # 1) 全称和旧写法：直接报。
    full_spans = glossary.full_form_spans(t)
    for s, e, full in full_spans:
        out.append(doc.violation("CR07", f"简历用简历写法「{glossary.full_forms[full]}」，不用全称「{full}」", s, e))
    mask((s, e) for s, e, _f in full_spans)
    var_spans = glossary.variant_spans(t)
    for s, e, v in var_spans:
        stds = "」或「".join(sorted(glossary.variants[v]))
        out.append(doc.violation("CR07", f"译名表写法是「{stds}」，不写「{v}」", s, e))
    mask((s, e) for s, e, _v in var_spans)

    # 2) 标准写法：遮住；待核的给提示，每个词只提示一次。
    std_spans = glossary.standard_spans(t)
    noted: set[str] = set()
    for s, e, std in std_spans:
        if glossary.is_pending(std, country) and std not in noted:
            noted.add(std)
            out.append(doc.violation("CR07", f"「{std}」在译名表里是待核写法，成稿进人工复核清单", s, e, level="note"))
    mask((s, e) for s, e, _s in std_spans)

    # 3) 剩下的英文名：按译名表写中文。
    en_spans = glossary.english_spans(t)
    for s, e, key in en_spans:
        entries = sorted(glossary.english.get(key, set()))
        narrowed = False
        if country in COUNTRY_KEY_TAGS:  # 有带本国括注的条目，只给这几条
            local = [x for x in entries if _country_tagged(x[0], country)]
            if local:
                entries, narrowed = local, True
        stds = {std for _k, std in entries}
        if len(stds) == 1 and (narrowed or all(k == key for k, _s in entries)):
            hint = f"写作「{entries[0][1]}」"
        else:
            hint = "写中文，表中条目：" + "；".join(f"{k}→{std}" for k, std in entries[:3])
            if len(entries) > 3:
                hint += "……"
        out.append(doc.violation("CR07", f"英文「{key}」按译名表{hint}（括号英文只在译名表写法含括号时保留）", s, e))
    mask((s, e) for s, e, _k in en_spans)
    return out


def _first_paragraph(doc: Doc) -> Optional[int]:
    """首段：第一个含"，男，""，女，"或"出生"的正文行。"""
    for i, line in enumerate(doc.lines):
        if is_heading_line(line) or not line.strip():
            continue
        if RE_FIRST_PARA_HINT.search(line):
            return i
    return None


def _date_key(date: str) -> tuple[int, int]:
    m = re.match(r"(\d{4})年(?:(\d{1,2})月)?", date)
    return (int(m.group(1)), int(m.group(2)) if m.group(2) else 0) if m else (0, 0)


def check_party(doc: Doc, text: str) -> list[Violation]:
    out: list[Violation] = []
    for m in RE_JOIN_PARTY.finditer(text):
        party, tail = m.group("party"), m.group("tail")
        before = text[m.start() - 1] if m.start() > 0 else ""
        if before not in "年月日":
            out.append(doc.violation("CR08", "入党句带年或年月：「××××年×月加入美国××党」，找不到锚点就不写", m.start(), m.end()))
        if party[0] in "了过到成为":
            out.append(doc.violation("CR08", "入党句写作「××××年×月加入美国××党」，不加「了」「过」一类字", m.start(), m.end()))
            party = party.lstrip("了过到成为")
        if re.fullmatch(r"(?:共和|民主)党", party):
            out.append(doc.violation("CR08", f"美国政党写全称「美国{party}」", m.start("party"), m.end("party")))
        if tail:
            out.append(doc.violation("CR08", "写「加入美国××党」，不写党员、党籍、××党人", m.start("party"), m.end()))
    _flag_all(doc, text, RE_PARTY_MEMBER_WORDS, "CR08", "党派只写成入党句「××××年×月加入美国××党」或「无党派人士」", out)
    _flag_all(doc, text, RE_PARTY_ENGLISH, "CR08", "党派用中文：美国共和党、美国民主党、无党派人士", out)
    _flag_all(doc, text, RE_NONPARTISAN_WRONG, "CR08", "官方党籍为无党派的写「无党派人士」", out)

    idx = _first_paragraph(doc)
    if idx is None:
        return out
    line = doc.lines[idx]
    lstart, _ = doc.line_span(idx)
    nat = RE_NATIONALITY.search(line)
    joins = [m for m in RE_JOIN_PARTY.finditer(line) if m.start() > 0 and line[m.start() - 1] in "年月日"]
    work = RE_WORK_START.search(line)
    degree = line.find("学位")
    nonpartisan = line.find("无党派人士")
    for jm in joins:
        dm = re.search(r"(\d{4}年(?:\d{1,2}月)?)$", line[: jm.start()])
        jstart = dm.start() if dm else jm.start()
        if nat and jstart < nat.end():
            out.append(doc.violation("CR08", "入党句放在国籍之后", lstart + jstart, lstart + jm.end()))
        if degree >= 0 and jstart > degree:
            out.append(doc.violation("CR08", "入党句放在学位之前，与参加工作时间按先后排列", lstart + jstart, lstart + jm.end()))
        if work and dm:
            jk, wk = _date_key(dm.group(1)), _date_key(work.group("date"))
            comparable = jk[0] != wk[0] or (jk[1] and wk[1])
            if comparable and jk < wk and jstart > work.start():
                out.append(doc.violation("CR08", "入党早于参加工作，入党句排在参加工作之前", lstart + jstart, lstart + jm.end()))
            if comparable and jk > wk and jstart < work.start():
                out.append(doc.violation("CR08", "入党晚于参加工作，入党句排在参加工作之后", lstart + jstart, lstart + jm.end()))
        if nonpartisan >= 0:
            out.append(doc.violation("CR08", "「无党派人士」和入党句不能同时出现", lstart + jstart, lstart + jm.end()))
    if nonpartisan >= 0 and nat and nonpartisan < nat.end():
        out.append(doc.violation("CR08", "「无党派人士」放在国籍之后", lstart + nonpartisan, lstart + nonpartisan + 5))
    return out


# ---- CR10–CR15（2026-10-17 简历字段纪律，rules/cadre.md 第 1、2、3、8、9 节） ----
RE_BIRTH = re.compile(r"(?P<y>\d{4})年(?:(?P<m>\d{1,2})月)?出生")
RE_LIFESPAN = re.compile(r"（(?P<y>\d{4})年(?:(?P<m>\d{1,2})月)?－\d{4}年(?:\d{1,2}月)?）")
RE_WORK = re.compile(r"(?P<y>\d{4})年(?:(?P<m>\d{1,2})月)?(?:参加工作|入伍)")
RE_CITIZENSHIP = re.compile(rf"[{CJK}]{{1,8}}籍(?=[，。])")
RE_GENDER = re.compile(r"[，）][男女]，")
RE_PERIOD_LINE = re.compile(r"^(?P<s>\d{4}年(?:\d{1,2}月)?)(?:－(?P<e>\d{4}年(?:\d{1,2}月)?)|(?P<now>至今))?$")
RE_YM = re.compile(r"(?P<y>\d{4})年(?:(?P<m>\d{1,2})月)?")
RE_CENGREN = re.compile(r"曾任(?P<job>[^。；]+)")
ETHNIC_US = ("白人", "非洲裔", "拉丁裔", "亚裔", "原住民")
RE_ETHNIC_ANY = re.compile(r"白种人|黑人|非裔|华裔|西班牙裔|拉美裔|印第安人|亚洲人|高加索人|白人|非洲裔|拉丁裔|亚裔|原住民|"
                           rf"[{CJK}]{{1,3}}族(?=，)")


def _ym(text: Optional[str]) -> Optional[tuple[int, int]]:
    m = RE_YM.match(text or "")
    return (int(m.group("y")), int(m.group("m") or 0)) if m else None


def _career_entries(doc: Doc) -> list[dict]:
    """履历条目：日期行（起止或单个时间点）加下一个非空行的内容。"""
    out = []
    for i, line in enumerate(doc.lines):
        m = RE_PERIOD_LINE.match(line.strip())
        if not m:
            continue
        content, row = "", i
        for j in range(i + 1, min(i + 3, len(doc.lines))):
            if doc.lines[j].strip():
                content, row = doc.lines[j].strip(), j
                break
        out.append({"line": i, "row": row, "text": line.strip(), "start": _ym(m.group("s")),
                    "end": _ym(m.group("e")) if m.group("e") else None, "now": bool(m.group("now")), "content": content})
    return out


def _is_study(entry: dict) -> bool:
    content = re.sub(r"（.*?）", "", entry["content"])
    return bool(re.search(r"学习(?:，获|$|（肄业）)", content))


def check_required(doc: Doc, text: str) -> list[Violation]:
    """CR10 五项必写：姓名、性别、国籍、出生年月、参加工作时间；后两项写到月。"""
    idx = _first_paragraph(doc)
    if idx is None:
        return []
    line = doc.lines[idx]
    start, end = doc.line_span(idx)
    out: list[Violation] = []

    def flag(message: str, m=None) -> None:
        a, b = (start + m.start(), start + m.end()) if m else (start, min(end, start + 20))
        out.append(doc.violation("CR10", message, a, b))

    if not RE_GENDER.search(line):
        flag("首段缺性别")
    if not RE_CITIZENSHIP.search(line):
        nat = RE_NATIONALITY.search(line)
        flag("国籍写“××籍”，如“美国籍”", nat if nat and not nat.group().endswith("籍，") else None)
    birth = RE_BIRTH.search(line) or RE_LIFESPAN.search(line)
    if not birth:
        flag("首段缺出生年月")
    elif not birth.group("m"):
        flag("出生年月写到月，查不到的按推测规则补", birth)
    work = RE_WORK.search(line)
    if not work:
        flag("首段缺参加工作（入伍）时间")
    elif not work.group("m"):
        flag("参加工作时间写到月，查不到的按推测规则补", work)
    return out


def check_granularity(doc: Doc, text: str) -> list[Violation]:
    """CR11 日期粒度统一：缺月份的条目不超过 2 条或全篇四分之一（取较宽）就全篇写到月，否则全篇只写到年。"""
    entries = _career_entries(doc)
    if len(entries) < 2:
        return []
    missing = []
    for e in entries:
        dates = [d for d in (e["start"], e["end"]) if d]
        dates += [_ym(m.group()) for m in RE_YM.finditer(" ".join(re.findall(r"（[^）]*）", e["content"])))]
        if any(d[1] == 0 for d in dates if d):
            missing.append(e)
    if not missing or len(missing) == len(entries):
        return []
    allowed = max(2, len(entries) / 4)
    if len(missing) <= allowed:
        message = f"缺月份的条目 {len(missing)} 条，不超过阈值，全篇写到月，按推测规则补月份"
        targets = missing
    else:
        message = f"缺月份的条目 {len(missing)} 条，超过阈值，全篇只写到年"
        targets = [e for e in entries if e not in missing]
    out = []
    for e in targets:
        a, b = doc.line_span(e["line"])
        out.append(doc.violation("CR11", message, a, b))
    return out


def check_work_age(doc: Doc, text: str) -> list[Violation]:
    """CR12 参加工作或入伍时不满 16 岁。"""
    idx = _first_paragraph(doc)
    if idx is None:
        return []
    line = doc.lines[idx]
    birth = RE_BIRTH.search(line) or RE_LIFESPAN.search(line)
    work = RE_WORK.search(line)
    if not birth or not work:
        return []
    by, bm = int(birth.group("y")), int(birth.group("m") or 0)
    wy, wm = int(work.group("y")), int(work.group("m") or 0)
    age = wy - by - (1 if bm and wm and wm < bm else 0)
    if age >= 16:
        return []
    start, _ = doc.line_span(idx)
    return [doc.violation("CR12", f"参加工作或入伍时只有 {age} 岁，核对是否借用了别人的数据", start + work.start(), start + work.end())]


def check_order(doc: Doc, text: str) -> list[Violation]:
    """CR13 履历按开始时间排，开始相同的先结束的在前；第一条工作与首段参加工作一致。履历重叠只作提示。"""
    entries = _career_entries(doc)
    out: list[Violation] = []
    far = (9999, 99)

    def end_key(entry: dict) -> tuple[int, int]:
        return entry["end"] or (far if entry["now"] else entry["start"])

    for a, b in zip(entries, entries[1:]):
        if not a["start"] or not b["start"]:
            continue
        span = doc.line_span(b["line"])
        if b["start"] < a["start"]:
            out.append(doc.violation("CR13", "履历按开始时间排", *span))
        elif b["start"] == a["start"] and end_key(a) > end_key(b):
            out.append(doc.violation("CR13", "开始时间相同的，先结束的在前", *span))
        elif not _is_study(a) and not _is_study(b) and a["end"] and b["start"] < a["end"]:
            out.append(doc.violation("CR13", "履历重叠：确有并行主职的可以各自成条，否则把兼职写进括注", *span, level="note"))
    idx = _first_paragraph(doc)
    work = RE_WORK.search(doc.lines[idx]) if idx is not None else None
    first = next((e for e in entries if not _is_study(e) and "退出现役" not in e["content"]), None)
    if work and first and first["start"]:
        lead = (int(work.group("y")), int(work.group("m") or 0))
        if lead[0] != first["start"][0] or (lead[1] and first["start"][1] and lead[1] != first["start"][1]):
            out.append(doc.violation("CR13", f"第一条工作的起始时间要和首段参加工作时间（{work.group().replace('参加工作', '').replace('入伍', '')}）一致",
                                     *doc.line_span(first["line"])))
    return out


def check_former(doc: Doc, text: str) -> list[Violation]:
    """CR14 首段“曾任××”要在履历里找得到。"""
    idx = _first_paragraph(doc)
    if idx is None:
        return []
    line = doc.lines[idx]
    m = RE_CENGREN.search(line)
    if not m:
        return []
    squash = lambda t: re.sub(r"[\s　（）()]", "", t)
    job = squash(m.group("job"))
    contents = [squash(e["content"]) for e in _career_entries(doc)]
    if any(job in c or (c and c in job) for c in contents):
        return []
    start, _ = doc.line_span(idx)
    return [doc.violation("CR14", "首段“曾任××”的职务在履历里找不到", start + m.start(), start + m.end())]


def check_country(doc: Doc, text: str, country: str) -> list[Violation]:
    """CR15 国别附则：美国族裔只用五个词；卢旺达不写族裔和民族、党职在前。"""
    idx = _first_paragraph(doc)
    if idx is None or country not in ("us", "rw"):
        return []
    line = doc.lines[idx]
    start, _ = doc.line_span(idx)
    out: list[Violation] = []
    for m in RE_ETHNIC_ANY.finditer(line):
        if country == "rw":
            out.append(doc.violation("CR15", "卢旺达人物不写族裔和民族", start + m.start(), start + m.end()))
        elif m.group() not in ETHNIC_US:
            out.append(doc.violation("CR15", "美国人的族裔只用白人、非洲裔、拉丁裔、亚裔、原住民", start + m.start(), start + m.end()))
    if country == "rw":
        for row, text_line in enumerate(doc.lines):
            cur = re.search(r"现任(?P<roles>[^。]+)", text_line)
            if not cur:
                continue
            roles = cur.group("roles").split("、")
            party = [i for i, r in enumerate(roles) if re.search(r"阵线|党", r)]
            if party and party[0] != 0:
                a, _ = doc.line_span(row)
                out.append(doc.violation("CR15", "卢旺达人物党职在前", a + cur.start(), a + cur.end()))
    return out


def check_sources(doc: Doc, text: str) -> list[Violation]:
    out: list[Violation] = []
    for pattern, message in RE_SOURCE_PATTERNS:
        _flag_all(doc, text, pattern, "CR09", message, out)
    return out


# =============================================================================
# 入口
# =============================================================================


def lint_text(
    text: str,
    *,
    glossary: Optional[Glossary] = None,
    allow: Sequence[str] = (),
    cjk_space: str = "require",
    select: Optional[Iterable[str]] = None,
    ignore: Optional[Iterable[str]] = None,
    path: str = "",
    country: str = "us",
) -> list[Violation]:
    """检查一段文本，返回违规列表（按行列排序）。glossary 为 None 时跳过 CR07。country 选国别附则：us、rw、other。"""
    if country not in COUNTRIES:
        raise ValueError(f"未知国别：{country}；可用：{'、'.join(COUNTRIES)}")
    doc = Doc(text, path)
    allow_pat = literal_pattern(tuple(allow) + BUILTIN_ALLOW)
    base = apply_mask(text, find_spans(allow_pat, text))
    # 首段末的“曾任××”是离任人物的固定写法（rules/cadre.md 第 1 节第 4 条），不算叙述词。
    lead = _first_paragraph(doc)
    if lead is not None:
        lstart, _ = doc.line_span(lead)
        base = apply_mask(base, [(lstart + m.start(), lstart + m.start() + 2) for m in RE_CENGREN.finditer(doc.lines[lead])])
    # 叙述词、顿号、占位、来源检查先遮住译名表里的标准写法，免得机构名里的字被误报。
    std_masked = base
    if glossary is not None:
        std_masked = apply_mask(base, [(s, e) for s, e, _t in glossary.standard_spans(base)])

    wanted = set(RULES) if not select else {c.upper() for c in select}
    if ignore:
        wanted -= {c.upper() for c in ignore}
    unknown = wanted - set(RULES)
    if unknown:
        raise ValueError(f"未知规则代码：{', '.join(sorted(unknown))}")

    out: list[Violation] = []
    if "CR01" in wanted:
        out += check_narrative(doc, std_masked, base)
    if "CR02" in wanted:
        out += check_qijian(doc, base)
    if "CR03" in wanted:
        out += check_dates(doc, base)
    if "CR04" in wanted:
        out += check_spacing(doc, base, cjk_space)
    if "CR05" in wanted:
        out += check_dunhao(doc, base, glossary)
    if "CR06" in wanted:
        out += check_placeholders(doc, std_masked)
    if "CR07" in wanted and glossary is not None:
        out += check_glossary(doc, base, glossary, country)
    if "CR08" in wanted:
        out += check_party(doc, std_masked)
    if "CR09" in wanted:
        out += check_sources(doc, std_masked)
    if "CR10" in wanted:
        out += check_required(doc, text)
    if "CR11" in wanted:
        out += check_granularity(doc, text)
    if "CR12" in wanted:
        out += check_work_age(doc, text)
    if "CR13" in wanted:
        out += check_order(doc, text)
    if "CR14" in wanted:
        out += check_former(doc, text)
    if "CR15" in wanted:
        out += check_country(doc, text, country)
    out.sort(key=lambda v: (v.line, v.col, v.code))
    return out


def lint_file(path: os.PathLike | str, encoding: str = "utf-8-sig", **kwargs) -> list[Violation]:
    path = Path(path)
    text = path.read_text(encoding=encoding)
    return lint_text(text, path=str(path), **kwargs)


def iter_files(paths: Iterable[os.PathLike | str], exts: Sequence[str] = DEFAULT_EXTS) -> list[Path]:
    """展开命令行给的文件和目录；目录递归，只收指定后缀，跳过隐藏目录和缓存目录。"""
    exts = tuple(e.lower() if e.startswith(".") else "." + e.lower() for e in exts)
    out: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__")
                for name in sorted(files):
                    if name.lower().endswith(exts):
                        out.append(Path(root) / name)
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(str(p))
    return out


def read_allow_file(path: os.PathLike | str) -> list[str]:
    out = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.lint.cadre_lint",
        description="干部简历写法 lint：按 aipolis 干部简历规则（要点见 shared/skills/cadre-resume/SKILL.md）检查文本，退出码非零表示有违规。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="规则：\n" + "\n".join(f"  {c}  {n}" for c, n in RULES.items()) + "\n\n退出码：0 无违规；1 有违规；2 参数或读取错误。",
    )
    parser.add_argument("paths", nargs="*", help="文件或目录，目录递归；用 - 读标准输入")
    parser.add_argument("--glossary", default=str(DEFAULT_GLOSSARY_DIR), help="译名表目录（默认 shared/glossary）")
    parser.add_argument("--no-glossary", action="store_true", help="不查译名一致（跳过 CR07）")
    parser.add_argument("--select", default="", help="只查这些规则，逗号分隔，如 CR01,CR03")
    parser.add_argument("--ignore", default="", help="不查这些规则，逗号分隔，如 CR09")
    parser.add_argument("--allow", default="", help="放行词表文件，一行一个词，这些词不参与检查")
    parser.add_argument("--cjk-space", choices=("require", "forbid", "off"), default="require",
                        help="中英文之间的空格：require 要空一格（默认，cadre.md 第2.7条）；forbid 不空；off 不查")
    parser.add_argument("--country", choices=COUNTRIES, default="us", help="国别附则（CR15）：us 美国（默认）、rw 卢旺达、other 不查")
    parser.add_argument("--ext", default=",".join(DEFAULT_EXTS), help="扫目录时收哪些后缀，默认 .md,.txt")
    parser.add_argument("--encoding", default="utf-8-sig", help="文件编码，默认 utf-8-sig（兼容带 BOM）")
    parser.add_argument("--format", choices=("text", "json"), default="text", help="输出格式")
    parser.add_argument("--notes", action="store_true", help="同时输出提示（如用了译名表里待核的写法），提示不影响退出码")
    parser.add_argument("-q", "--quiet", action="store_true", help="不输出逐条结果，只给退出码和汇总")
    parser.add_argument("--list-rules", action="store_true", help="列出规则后退出")
    parser.add_argument("--version", action="version", version=f"cadre_lint {__version__}")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_rules:
        for code, name in RULES.items():
            print(f"{code}  {name}")
        return 0
    if not args.paths:
        parser.print_usage(sys.stderr)
        print("错误：要给文件或目录。", file=sys.stderr)
        return 2

    select = [c for c in args.select.split(",") if c.strip()] or None
    ignore = [c for c in args.ignore.split(",") if c.strip()] or None
    try:
        allow = read_allow_file(args.allow) if args.allow else []
    except OSError as exc:
        print(f"错误：放行词表读不出来：{exc}", file=sys.stderr)
        return 2

    glossary = None
    need_glossary = not args.no_glossary and (select is None or "CR07" in {c.upper() for c in select}) and not (ignore and "CR07" in {c.upper() for c in ignore})
    if need_glossary:
        try:
            glossary = load_glossary(args.glossary)
        except (OSError, csv.Error) as exc:
            print(f"错误：译名表读不出来（{exc}）。用 --glossary 指目录，或 --no-glossary 跳过译名检查。", file=sys.stderr)
            return 2

    exts = [e for e in args.ext.split(",") if e.strip()]
    try:
        files = iter_files([p for p in args.paths if p != "-"], exts)
    except FileNotFoundError as exc:
        print(f"错误：路径不存在：{exc}", file=sys.stderr)
        return 2

    results: list[Violation] = []
    checked = 0
    had_error = False
    kwargs = dict(glossary=glossary, allow=allow, cjk_space=args.cjk_space, select=select, ignore=ignore, country=args.country)
    try:
        if "-" in args.paths:
            results += lint_text(sys.stdin.read(), path="<stdin>", **kwargs)
            checked += 1
        for f in files:
            try:
                results += lint_file(f, encoding=args.encoding, **kwargs)
                checked += 1
            except UnicodeDecodeError as exc:
                had_error = True
                print(f"错误：{f} 不是 {args.encoding} 文本（{exc.reason}）", file=sys.stderr)
            except OSError as exc:
                had_error = True
                print(f"错误：{f} 读不出来（{exc}）", file=sys.stderr)
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    errors = [v for v in results if v.level == "error"]
    notes = [v for v in results if v.level != "error"]
    shown = results if args.notes else errors
    if args.format == "json":
        payload = {
            "files": checked,
            "violations": len(errors),
            "notes": len(notes),
            "items": [v.to_dict() for v in shown],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif not args.quiet:
        for v in shown:
            print(v.format())
    if args.format != "json":
        summary = f"共检查 {checked} 个文件，{len(errors)} 处违规"
        if notes and args.notes:
            summary += f"，{len(notes)} 条提示"
        print(summary, file=sys.stderr)
    if had_error:
        return 2
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
