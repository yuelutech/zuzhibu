#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""import_ceo_profiles.py — 人事三局：把 Care 历史项目 ceo-profiles 的 538 份底稿转成事实包。

用法（在仓库根目录）：

    python3 bureaus/personnel-congress/import_ceo_profiles.py
    python3 bureaus/personnel-congress/import_ceo_profiles.py --date 2026-10-16 --ids A000055 N000147

输入（--source 给原料目录，只读，不改原料；原料 2026-10-21 公开前已移出仓库，默认目录不在了）：
    profiles/*.json            底稿（60 份逐人取证的 T1，478 份 Codex 初稿 T2）
    roster-538.csv             名册（类别、州、选区、译名来源）
    progress.csv               进度台账（批次）
    batches/TIERING.md         T1 名单，与底稿结构对照，不一致写进报告
    batches/T1-review.md       T1 第二批名单

输出（默认 bureaus/personnel-congress/data/）：
    bundles/<id>.json          每人一份事实包，结构按 shared/schema/bundle.schema.json
    index.csv                  名册索引：分级、校验结果、关键字段
    report.md / report.json    校验通过率、字段转换统计、留给本机复核的清单

转换规则见 bureaus/personnel-congress/README.md「导入脚本」一节。只用标准库，不联网。
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import re
import sys
from collections import Counter, OrderedDict
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
from zuzhibu.sources import tier_of  # noqa: E402  来源等级按链接判定（rules/cadre.md 第 4 节）
DEFAULT_SOURCE = HERE / "import" / "ceo-profiles"
DEFAULT_SCHEMA = REPO / "shared" / "schema" / "bundle.schema.json"
DEFAULT_OUT = HERE / "data"

SCHEMA_VERSION = "1.0"

# ────────────────────────────────────────────────────────────────────────────
# 常量
# ────────────────────────────────────────────────────────────────────────────

# 州名写法以底稿和 shared/glossary 的用法为准（爱荷华州、佐治亚州、得克萨斯州）。
STATE_ZH = {
    "AL": "阿拉巴马州", "AK": "阿拉斯加州", "AZ": "亚利桑那州", "AR": "阿肯色州",
    "CA": "加利福尼亚州", "CO": "科罗拉多州", "CT": "康涅狄格州", "DE": "特拉华州",
    "FL": "佛罗里达州", "GA": "佐治亚州", "HI": "夏威夷州", "ID": "爱达荷州",
    "IL": "伊利诺伊州", "IN": "印第安纳州", "IA": "爱荷华州", "KS": "堪萨斯州",
    "KY": "肯塔基州", "LA": "路易斯安那州", "ME": "缅因州", "MD": "马里兰州",
    "MA": "马萨诸塞州", "MI": "密歇根州", "MN": "明尼苏达州", "MS": "密西西比州",
    "MO": "密苏里州", "MT": "蒙大拿州", "NE": "内布拉斯加州", "NV": "内华达州",
    "NH": "新罕布什尔州", "NJ": "新泽西州", "NM": "新墨西哥州", "NY": "纽约州",
    "NC": "北卡罗来纳州", "ND": "北达科他州", "OH": "俄亥俄州", "OK": "俄克拉何马州",
    "OR": "俄勒冈州", "PA": "宾夕法尼亚州", "RI": "罗得岛州", "SC": "南卡罗来纳州",
    "SD": "南达科他州", "TN": "田纳西州", "TX": "得克萨斯州", "UT": "犹他州",
    "VT": "佛蒙特州", "VA": "弗吉尼亚州", "WA": "华盛顿州", "WV": "西弗吉尼亚州",
    "WI": "威斯康星州", "WY": "怀俄明州",
}
# 无表决权代表所属地区
TERRITORY_ZH = {
    "DC": "哥伦比亚特区", "PR": "波多黎各", "GU": "关岛",
    "VI": "美属维尔京群岛", "AS": "美属萨摩亚", "MP": "北马里亚纳群岛",
}
PARTY_ZH = {"R": "美国共和党", "D": "美国民主党", "I": "无党派人士"}
SENATE_CATEGORIES = {"senate", "senator", "senator_appointed"}
CHAMBER_TAG = {"senate": "senate", "house": "house", "whitehouse": "whitehouse"}
COMPANY_TAG = {"senate": "美国参议院", "house": "美国众议院", "whitehouse": "美国白宫"}
CHAMBER_PREFIX = {"senate": "参议院", "house": "众议院"}

DATASET_URL = "https://github.com/unitedstates/congress-legislators"
DATASET_TITLE = "unitedstates/congress-legislators（legislators-current.json）"

# 入党锚点级别 → schema 里的推断规则名（rules/party_join.md 第一、三节）
ANCHOR_RULES = {
    1: "party_anchor_registration",
    2: "party_anchor_party_office",
    3: "party_anchor_candidacy_filing",
    4: "party_anchor_partisan_ballot",
    5: "party_anchor_partisan_office",
}
# 判级关键词：先判第 3 级（FEC/竞选委员会登记），再判第 1 级（选民登记），避免"登记"二字混淆。
ANCHOR_PATTERNS = [
    (3, re.compile(r"FEC|F2|参选声明|竞选委员会|宣布参选|宣布竞选|启动[^，；]*竞选|登记参选|参选登记")),
    (1, re.compile(r"登记记录|选民登记|改登记|登记为|党籍登记|登记时间|最近一次登记")),
    (2, re.compile(r"党内职务|执行主任|全国委员会[^，；]*委员|州委员会主席|党主席")),
    (5, re.compile(r"任命为|被任命|首次就任|就任[^，；（）]*(?:公职|议员|法官|州长|市长)")),
    (4, re.compile(r"初选|普选|当选|参选|选举|决选|选票")),
]

ROLE_RE = re.compile(
    r"(共同主席|联合主席|副主席|主席|资深少数党成员|首席少数党成员|民主党首席成员|"
    r"少数党首席成员|资深成员|首席成员|当然委员|委员|成员)$"
)
ROLE_NORMALIZE = {
    "资深少数党成员": "资深成员", "首席少数党成员": "资深成员", "民主党首席成员": "资深成员",
    "少数党首席成员": "资深成员", "首席成员": "资深成员", "成员": "委员",
}
OFFICE_RE = re.compile(r"联邦参议员|联邦众议员|众议院[^，、]*代表|美国总统$|美国副总统$|^第\d+任美国总统$")
COMMITTEE_RE = re.compile(r"委员会|小组")
LEADERSHIP_RE = re.compile(r"会议|党团|党鞭|领袖|议长")
NO_PREFIX_RE = re.compile(r"^(美国|参议院|众议院|国会|全国|联合|白宫|民主党全国|共和党全国)")
# 两党的竞选委员会是全国性党组织，不加参议院/众议院前缀
PARTY_ORG_RE = re.compile(r"竞选委员会|全国委员会")
NOTE_PAREN_RE = re.compile(r"（(?:领导职务|按惯例[^）]*)）")
PLACEHOLDER_RE = re.compile(r"待核|待定|待补|未知")
# 现任段按顿号拆开后，不以这些词结尾的片段要和下一片段并回去（委员会名里有顿号）
TERMINAL_RE = re.compile(
    r"(委员会|小组|议员|代表|领袖|党鞭|主席|议长|秘书|顾问|成员|委员|党团|总统|联盟|主任|会长|理事|书记|经理)"
    r"(（[^）]*）)?$"
)

# 时间线行："1992年-1996年 X" / "1997年-     X" / "2011年 X"
LINE_RE = re.compile(r"^(\d{4})年(?:\s*(?P<dash>[-－—])\s*(?:(\d{4})年?)?)?\s*(?P<body>.*)$")
QIJIAN_RE = re.compile(r"^(?P<main>.*?)[（(]其间[：:](?P<inner>.*)[）)]\s*$")
# 底稿括注："约1998-2001任…" / "2005.01-2009.01任…" / "2006年派驻…" / "2025- …" / "2024年11月当选…"
ANN_RE = re.compile(
    r"^(?P<approx>约)?(?P<y1>\d{4})"
    r"(?:[.．](?P<m1>\d{1,2})|年(?P<m1b>\d{1,2})月(?:(?P<d1>\d{1,2})日)?)?年?\s*"
    r"(?P<dash>[-－—~～]\s*(?:(?P<y2>\d{4})(?:[.．](?P<m2>\d{1,2})|年(?P<m2b>\d{1,2})月)?年?)?)?\s*"
    r"(?P<text>.+)$"
)
ORG_SUFFIX = r"(?:大学|学院|学校|分校|中心|研究院|研究生院|研究所|学部|军校|神学院|学园|书院)"
STUDY_RE = re.compile(
    r"^(?P<org>.+" + ORG_SUFFIX + r"(?:（[^）]*）)?)"
    r"(?:(?P<prog>[^，（）]+?)专业)?"
    r"(?P<lvl>(?:在职)?(?:硕士|博士)?研究生|博士|硕士)?(?:课程)?学习"
    r"(?:（(?P<note>[^）]*)）)?"
    r"(?:，获(?P<deg>.+?)学位)?"
    r"(?:（(?P<note2>[^）]*)）)?$"
)
DEGREE_RE = re.compile(r"获(.+?)学位")
LATIN_PAREN_RE = re.compile(r"（([^（）]*)）")
URL_IN_TEXT_RE = re.compile(r"https?://\S+")
BARE_URL_RE = re.compile(r"^[\w.-]+\.(?:org|gov|com|edu|net|int|io)(?:/\S*)?$")
PARTY_SENTENCE_RE = re.compile(r"\d{4}年(?:\d{1,2}月)?加入美国(?:共和党|民主党)")
WORKSTART_SENTENCE_RE = re.compile(r"\d{4}年\d{1,2}月(?:参加工作|入伍)")
MILITARY_RE = re.compile(r"海军|陆军|空军|海军陆战队|国民警卫队|军官|军人|服役|士兵|军士|预备役")
ENLIST_RE = re.compile(r"入伍|参军|受衔|^入美国(?:陆军|海军|空军|海军陆战队|海岸警卫队)")
STUDY_TITLE_RE = re.compile(r"^[^，]*?学习(?:（[^）]*）)?(?:，获.*学位)?(?:（[^）]*）)?$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ────────────────────────────────────────────────────────────────────────────
# 极简 JSON Schema 校验器（只覆盖 bundle.schema.json 用到的关键字）
# ────────────────────────────────────────────────────────────────────────────

def _type_name(x) -> str:
    if x is None:
        return "null"
    if isinstance(x, bool):
        return "boolean"
    if isinstance(x, int):
        return "integer"
    if isinstance(x, float):
        return "number"
    if isinstance(x, str):
        return "string"
    if isinstance(x, list):
        return "array"
    if isinstance(x, dict):
        return "object"
    return type(x).__name__


def _type_ok(expected, x) -> bool:
    kinds = expected if isinstance(expected, list) else [expected]
    actual = _type_name(x)
    for k in kinds:
        if k == actual:
            return True
        if k == "number" and actual == "integer":
            return True
    return False


class MiniValidator:
    """支持 $ref(#/…)、type、properties、required、additionalProperties、items、enum、pattern、anyOf。"""

    def __init__(self, schema: dict):
        self.schema = schema

    def validate(self, instance) -> list[str]:
        errors: list[str] = []
        self._check(self.schema, instance, "$", errors)
        return errors

    def _resolve(self, ref: str) -> dict:
        if not ref.startswith("#/"):
            raise ValueError(f"只支持文档内引用：{ref}")
        node = self.schema
        for part in ref[2:].split("/"):
            node = node[part.replace("~1", "/").replace("~0", "~")]
        return node

    def _check(self, schema: dict, inst, path: str, errors: list[str]) -> None:
        if "$ref" in schema:
            self._check(self._resolve(schema["$ref"]), inst, path, errors)
            return
        if "anyOf" in schema:
            branch_errors: list[list[str]] = []
            for sub in schema["anyOf"]:
                errs: list[str] = []
                self._check(sub, inst, path, errs)
                if not errs:
                    break
                branch_errors.append(errs)
            else:
                # 没有一个分支通过：报最接近（错误最少）那个分支的全部错误
                errors.extend(min(branch_errors, key=len))
            return
        expected = schema.get("type")
        if expected is not None and not _type_ok(expected, inst):
            errors.append(f"{path}: 类型应为 {expected}，实为 {_type_name(inst)}")
            return
        if "enum" in schema and inst not in schema["enum"]:
            errors.append(f"{path}: 值 {inst!r} 不在枚举 {schema['enum']} 内")
        if "pattern" in schema and isinstance(inst, str) and not re.search(schema["pattern"], inst):
            errors.append(f"{path}: 值 {inst!r} 不匹配 {schema['pattern']}")
        if isinstance(inst, dict):
            props = schema.get("properties", {})
            for key in schema.get("required", []):
                if key not in inst:
                    errors.append(f"{path}: 缺少必填字段 {key}")
            if schema.get("additionalProperties") is False:
                for key in inst:
                    if key not in props:
                        errors.append(f"{path}: 不允许的字段 {key}")
            for key, sub in props.items():
                if key in inst:
                    self._check(sub, inst[key], f"{path}.{key}", errors)
        if isinstance(inst, list) and "items" in schema:
            for i, x in enumerate(inst):
                self._check(schema["items"], x, f"{path}[{i}]", errors)


# ────────────────────────────────────────────────────────────────────────────
# 小工具
# ────────────────────────────────────────────────────────────────────────────

def date_value(year, month=None, day=None) -> str | None:
    """年月日拼成 YYYY[-MM[-DD]]；来源写到哪一级就写到哪一级。"""
    if not year:
        return None
    try:
        y = int(year)
    except (TypeError, ValueError):
        return None
    value = f"{y:04d}"
    if month:
        value += f"-{int(month):02d}"
        if day:
            value += f"-{int(day):02d}"
    return value


def fact(value, source_ids, inferred=False, basis=None, inference=None) -> dict:
    """cadreFact / cadreDateFact / cadreWorkStartFact 结构相同。"""
    return OrderedDict([
        ("value", value),
        ("source_ids", list(source_ids)),
        ("inferred", bool(inferred)),
        ("basis", basis),
        ("inference", inference),
    ])


def inference(rule: str, from_date: str, duration_years=None) -> dict:
    return OrderedDict([("rule", rule), ("from_date", from_date), ("duration_years", duration_years)])


def clean_name(text) -> str:
    """去掉英文原名、英文学位名一类的括注（University of Arkansas、J.D.、Antioch College，俄亥俄州黄泉镇），
    保留通行缩写（MIT、ACLU）和纯中文的括注（第三巡回区，费城）。"""
    if not text:
        return ""
    def _repl(m):
        inner = m.group(1).strip()
        if not inner:
            return ""
        if re.search(r"[A-Za-z]", inner) and re.search(r"[a-z]|\s|\.|，|,", inner):
            return ""
        return m.group(0)
    return LATIN_PAREN_RE.sub(_repl, str(text)).strip()


def strip_leading_verb(text: str) -> str:
    return re.sub(r"^(?:兼任|选派为|任|在)", "", text.strip())


def study_level(degree: str | None, lvl: str | None = None) -> str | None:
    deg = degree or ""
    if "博士" in deg and "博士后" not in deg:
        return "doctor"
    if "硕士" in deg:
        return "master"
    if "学士" in deg and deg.count("学士") > deg.count("副学士"):
        return "bachelor"
    if lvl:
        if "博士" in lvl:
            return "doctor"
        if "硕士" in lvl:
            return "master"
    return None


LEVEL_RANK = {"bachelor": 1, "master": 2, "doctor": 3}
LEVEL_XUELI = {"bachelor": "大学学历", "master": "研究生学历", "doctor": "研究生学历"}


def compose_degree(org: str, prog: str | None, deg: str) -> str:
    """"学校＋专业＋学位"。联合学位（法学博士、伦理学硕士）只取最高的一个，学校、专业按同一序号对应；
    专业已含在学位名里就不重复（法律＋法学博士→法学博士）。"""
    degs = [d for d in split_top_level(deg, "、,，") if d]
    progs = [p for p in split_top_level(prog or "", "、,，") if p]
    orgs = [o for o in split_top_level(org or "", "、") if o]
    pick = 0
    if len(degs) > 1:
        ranks = [LEVEL_RANK.get(study_level(d) or "", 0) for d in degs]
        pick = ranks.index(max(ranks))
    deg_one = clean_name(degs[pick]) if degs else clean_name(deg)
    prog_one = None
    if progs:
        if len(progs) == len(degs):
            prog_one = progs[pick]
        elif len(progs) == 1:
            prog_one = progs[0]
    org_one = orgs[pick] if len(orgs) == len(degs) and len(orgs) > 1 else org
    parts = [clean_name(org_one)]
    if prog_one and prog_one[0] not in deg_one and prog_one not in deg_one:
        parts.append(prog_one)
    parts.append(deg_one + "学位")
    return "".join(parts)


def parse_study(text: str) -> dict | None:
    """解析"某校某专业学习，获某学位"。返回 org/prog/deg/level/note；不像学习经历返回 None。"""
    if "学习" not in text:
        return None
    m = STUDY_RE.match(text)
    if not m:
        deg_m = DEGREE_RE.search(text)
        deg = deg_m.group(1).replace("学位、", "、") if deg_m else None
        return {"org": text, "prog": None, "deg": deg, "level": study_level(deg), "parsed": False}
    deg = m.group("deg")
    if deg:
        deg = deg.replace("学位、", "、")
    prog = m.group("prog")
    return {
        "org": m.group("org"),
        "prog": prog or None,
        "deg": deg or None,
        "level": study_level(deg, m.group("lvl")),
        "parsed": True,
    }


def split_top_level(text: str, seps: str = "、，,") -> list[str]:
    """按顿号/逗号拆分，括号里的不拆。"""
    out, buf, depth = [], [], 0
    for ch in text:
        if ch in "（(":
            depth += 1
        elif ch in "）)":
            depth = max(0, depth - 1)
        if depth == 0 and ch in seps:
            if "".join(buf).strip():
                out.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        out.append("".join(buf).strip())
    return out


def merge_fragments(parts: list[str]) -> list[str]:
    """委员会名含顿号（农业、营养和林业委员会）：不以职务词结尾的片段并回下一片段。"""
    merged: list[str] = []
    carry = ""
    for p in parts:
        cur = (carry + "、" + p) if carry else p
        if TERMINAL_RE.search(cur):
            merged.append(cur)
            carry = ""
        else:
            carry = cur
    if carry:
        merged.append(carry)
    return merged


def split_role(text: str) -> tuple[str, str]:
    m = ROLE_RE.search(text)
    if m and m.start() > 0:
        return text[: m.start()], ROLE_NORMALIZE.get(m.group(1), m.group(1))
    return text, ""


def committee_org(org: str, chamber: str | None) -> str:
    org = re.sub(r"^美国(?=参议院|众议院|国会)", "", org.strip())
    if chamber and not NO_PREFIX_RE.match(org) and not PARTY_ORG_RE.search(org):
        return CHAMBER_PREFIX[chamber] + org
    return org


def leadership_org(text: str, chamber: str | None) -> str:
    text = text.strip()
    if chamber and LEADERSHIP_RE.search(text) and not NO_PREFIX_RE.match(text):
        return CHAMBER_PREFIX[chamber] + text
    return text


def split_role_text(text: str) -> list[str]:
    """现任段、委员会字段的一段文字 → 若干职务片段：去掉"现任""（领导职务）"，按顶层顿号拆，再把委员会名里的顿号并回去。"""
    text = re.sub(r"^现任", "", (text or "").strip().rstrip("。"))
    text = NOTE_PAREN_RE.sub("", text)
    return merge_fragments(split_top_level(text))


def parse_role_segment(seg: str, chamber: str | None) -> list[dict]:
    """把现任段里的一段文字拆成 sector/organization/role；公职本身另行生成，这里跳过。"""
    seg = seg.strip().strip("。；;")
    seg = re.sub(r"^(?:现任|兼任)", "", seg)
    seg = NOTE_PAREN_RE.sub("", seg).strip()
    if not seg or OFFICE_RE.search(seg) or PLACEHOLDER_RE.search(seg):
        return []
    m = re.match(r"^(.*?)（([^（）]*)）$", seg)
    base, paren = (m.group(1), m.group(2)) if m else (seg, None)
    out: list[dict] = []
    if COMMITTEE_RE.search(base):
        org, role = split_role(base)
        org = committee_org(org, chamber)
        out.append({"sector": "committee", "organization": org, "role": role or "委员"})
        if paren:
            if "小组" in paren or "委员会" in paren:
                sub_org, sub_role = split_role(paren)
                out.append({"sector": "committee", "organization": org + sub_org, "role": sub_role or "委员"})
            else:
                r = ROLE_RE.search(paren)
                if r:
                    out[-1]["role"] = ROLE_NORMALIZE.get(r.group(1), r.group(1))
    else:
        text = base if (paren is not None and ROLE_RE.fullmatch(paren)) else seg
        out.append({"sector": "politics", "organization": leadership_org(text, chamber), "role": ""})
    return out


def classify_anchor(anchor: str) -> int | None:
    """按 party_join.md 第一节判锚点级别：1 选民登记、2 党内职务、3 参选登记、4 党派选举、5 以该党身份就任。"""
    for level, rx in ANCHOR_PATTERNS:
        if rx.search(anchor or ""):
            return level
    return None


def split_sources(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        items: list[str] = []
        for x in raw:
            items.extend(split_sources(x))
        return items
    text = str(raw).strip()
    if not text:
        return []
    return [p.strip() for p in re.split(r"[;；]", text) if p.strip()]


def normalize_source(piece: str) -> tuple[str, str]:
    """返回 (url, title)。数据集名、裸域名、含链接的说明文字分别处理；查不出链接的 url 留空。"""
    if piece.startswith("unitedstates/congress-legislators"):
        return DATASET_URL, DATASET_TITLE
    if re.match(r"^https?://", piece):
        parts = urlsplit(piece)
        if parts.netloc.endswith("wikipedia.org"):
            return piece, "维基百科：" + unquote(parts.path.rsplit("/", 1)[-1])
        return piece, parts.netloc
    if BARE_URL_RE.match(piece):
        url = "https://" + piece
        parts = urlsplit(url)
        if parts.netloc.endswith("wikipedia.org"):
            return url, "维基百科：" + unquote(parts.path.rsplit("/", 1)[-1])
        return url, parts.netloc
    m = URL_IN_TEXT_RE.search(piece)
    if m:
        return m.group(0).rstrip("，。）)"), piece
    return "", piece


class SourceBook:
    """一个事实包内的来源登记簿：同一链接、同一摘录只记一次，编号 s1、s2……"""

    def __init__(self, checked_at: str):
        self.checked_at = checked_at
        self.entries: list[dict] = []
        self.index: dict[tuple[str, str], str] = {}

    def add(self, raw, quote: str | None = None) -> list[str]:
        ids: list[str] = []
        excerpt = (quote or "").strip()
        for piece in split_sources(raw):
            url, title = normalize_source(piece)
            key = (url or title, excerpt)
            sid = self.index.get(key)
            if sid is None:
                sid = f"s{len(self.entries) + 1}"
                self.entries.append(OrderedDict([
                    ("id", sid), ("url", url), ("title", title), ("published_at", None),
                    ("checked_at", self.checked_at), ("excerpt", excerpt), ("tier", tier_of(url)),
                ]))
                self.index[key] = sid
            if sid not in ids:
                ids.append(sid)
        return ids

    def wiki_only(self, ids: list[str]) -> bool:
        urls = [e["url"] for e in self.entries if e["id"] in ids]
        return bool(urls) and all("wikipedia.org" in u for u in urls)


# ────────────────────────────────────────────────────────────────────────────
# 读入
# ────────────────────────────────────────────────────────────────────────────

def read_csv_by_id(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {row["id"]: row for row in csv.DictReader(f)}


def load_t1_ids(source: Path) -> set[str]:
    """从 TIERING.md 的 T1 节和 T1-review.md 的"本批人员"行取出明确写了编号的 T1 名单。"""
    ids: set[str] = set()
    tiering = source / "batches" / "TIERING.md"
    if tiering.exists():
        text = tiering.read_text(encoding="utf-8")
        m = re.search(r"## T1.*?(?=\n## T2|\Z)", text, re.S)
        section = m.group(0) if m else text
        ids |= set(re.findall(r"\b[A-Z]\d{6}\b", section))
        for word in ("president", "vice-president"):
            if re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(word), section):
                ids.add(word)
    review = source / "batches" / "T1-review.md"
    if review.exists():
        m = re.search(r"本批人员：(.*)", review.read_text(encoding="utf-8"))
        if m:
            ids |= set(re.findall(r"\b[A-Z]\d{6}\b", m.group(1)))
    return ids


def is_researched(profile: dict) -> bool:
    """T1：逐人取证的底稿（有 career/party_join 结构，没有 provenance=codex_normalized）。"""
    return profile.get("provenance") != "codex_normalized" and isinstance(profile.get("career"), list)


# ────────────────────────────────────────────────────────────────────────────
# 转换
# ────────────────────────────────────────────────────────────────────────────

class Converter:
    def __init__(self, source: Path, date: str):
        self.source = source
        self.date = date
        self.roster = read_csv_by_id(source / "roster-538.csv")
        self.progress = read_csv_by_id(source / "progress.csv")
        self.t1_ids = load_t1_ids(source)
        self.stats: Counter = Counter()
        self.review: dict[str, list] = {
            "party_joined_at_kept": [],
            "party_anchor_unclassified": [],
            "tiering_mismatch": [],
            "work_start_military": [],
            "generated_names": [],
        }

    # ── 入口 ────────────────────────────────────────────────────────────────
    def convert_file(self, path: Path) -> dict:
        profile = json.loads(path.read_text(encoding="utf-8"))
        return self.convert(profile)

    def convert(self, profile: dict) -> dict:
        """一份底稿 → 一份事实包（schema_version/date/items）。"""
        pid = str(profile["id"])
        roster = self.roster.get(pid, {})
        tier = "gold" if is_researched(profile) else "draft"
        chamber_tag = CHAMBER_TAG.get(roster.get("category") or "", None) or self._chamber_from_category(profile)
        chamber = chamber_tag if chamber_tag in CHAMBER_PREFIX else None
        checked_at = profile.get("updated") if DATE_RE.match(str(profile.get("updated") or "")) else self.date
        book = SourceBook(checked_at)
        unknowns: list[str] = []
        excluded: list[str] = []
        ctx = {"pid": pid, "tier": tier, "chamber": chamber, "book": book,
               "unknowns": unknowns, "excluded": excluded}

        self.stats["items"] += 1
        self.stats[f"items_{tier}"] += 1
        if roster.get("name_zh_source") == "generated":
            self.review["generated_names"].append({"id": pid, "name_zh": profile.get("name_zh")})

        office_src = book.add(profile.get("current_position", {}).get("source"),
                              profile.get("current_position", {}).get("quote"))
        person = self._person(profile, ctx, office_src)
        current_roles = self._current_roles(profile, ctx, office_src)
        careers = self._careers_t1(profile, ctx) if tier == "gold" else self._careers_t2(profile, ctx)
        if tier == "gold":
            self._person_education_t1(profile, person, ctx)
            self._work_start_t1(profile, person, ctx)
            conflicts = self._conflicts_t1(profile)
            for miss in profile.get("missing") or []:
                unknowns.append(f"底稿缺项：{miss}")
            if profile.get("military"):
                excluded.append("军旅记录（military）未单独转换，服役经历以履历条目为准")
            if (profile.get("disclosure") or {}).get("filings"):
                excluded.append("股票交易公开披露（disclosure.filings）不进事实包")
        else:
            self._person_education_t2(careers, person, ctx)
            self._work_start_t2(profile, careers, person, ctx)
            conflicts = []
            unknowns.append("履历：T2 Codex 初稿时间线，逐条无来源，待重新取证")
            fp = profile.get("first_paragraph") or ""
            bp = re.search(r"生，(.+?)人，", fp)
            if bp:
                unknowns.append(f"出生地：无来源（底稿首段作「{bp.group(1)}人」）")
            else:
                unknowns.append("出生地：无来源")
            for sentence in WORKSTART_SENTENCE_RE.findall(fp):
                excluded.append(f"发布稿「{sentence}」：月份为旧规则推定，只写到年")
            for flag in profile.get("flags") or []:
                unknowns.append(f"底稿标记：{flag}")
        self._party(profile, person, ctx)

        self.stats["careers_total"] += len(careers)
        for c in careers:
            self.stats[f"careers_{c['kind']}"] += 1
            self.stats["careers_with_sources" if c["source_ids"] else "careers_without_sources"] += 1
            if c["kind"] == "work" and c["role"] is None:
                self.stats["careers_role_unsplit"] += 1
            self.stats["annotations_total"] += len(c["annotations"])
        self.stats["current_roles_politics"] += sum(1 for r in current_roles if r["sector"] == "politics")
        self.stats["current_roles_committee"] += sum(1 for r in current_roles if r["sector"] == "committee")
        if any(r["sector"] == "committee" for r in current_roles):
            self.stats["items_with_committee"] += 1
        if conflicts:
            self.stats["items_with_conflicts"] += 1
        self.stats["unknowns_total"] += len(unknowns)
        self.stats["excluded_total"] += len(excluded)

        subject_id = re.sub(r"[^a-z0-9_-]", "-", pid.lower())
        state = profile.get("state") or roster.get("state") or ""
        tags = [tier, chamber_tag]
        if state:
            tags.append(f"state:{state}")
        item = OrderedDict([
            ("id", f"cadre-{subject_id}"),
            ("line", "cadre"),
            ("subject_id", subject_id),
            ("title", f"{profile.get('name_zh') or pid}简历"),
            ("sources", book.entries),
            ("tags", tags),
            ("person", person),
            ("current_roles", current_roles),
            ("careers", careers),
            ("appointments", []),
            ("event_date", None),
            ("paragraphs", []),
            ("company_tag", COMPANY_TAG.get(chamber_tag, "美国国会")),
            ("conflicts", conflicts),
            ("unknowns", unknowns),
            ("excluded", excluded),
            ("country_profile", "us"),
            ("card_type", None),
            ("date_granularity", None),
            ("disambiguation", None),
            ("review_items", []),
        ])
        return OrderedDict([("schema_version", SCHEMA_VERSION), ("date", self.date), ("items", [item])])

    @staticmethod
    def _chamber_from_category(profile: dict) -> str:
        cat = profile.get("category") or ""
        if cat in SENATE_CATEGORIES:
            return "senate"
        if cat in ("president", "vice_president"):
            return "whitehouse"
        return "house"

    # ── person ──────────────────────────────────────────────────────────────
    def _person(self, profile: dict, ctx: dict, office_src: list[str]) -> dict:
        book: SourceBook = ctx["book"]
        gender = profile.get("gender") or {}
        gender_src = book.add(gender.get("source"), gender.get("quote"))
        birth = profile.get("birth") or {}
        birth_src = book.add(birth.get("source"), birth.get("quote"))
        if birth.get("corroboration"):
            birth_src += book.add(birth["corroboration"])
        birth_value = date_value(birth.get("year"), birth.get("month"), birth.get("day"))
        person = OrderedDict([
            ("name", fact(profile.get("name_zh") or profile.get("name_en") or ctx["pid"], office_src or gender_src)),
            ("gender", fact(gender["value"], gender_src) if gender.get("value") in ("男", "女") else None),
            ("nationality", fact("美国", gender_src or office_src)),
            ("birth_date", fact(birth_value, birth_src) if birth_value else None),
            ("birth_place", None),
            ("work_start", None),
            ("education", None),
            ("degree", None),
            ("honorary_degree", None),
            ("party", None),
            ("party_joined_at", None),
            ("work_start_action", "参加工作"),
            ("ethnicity", None),
            ("academician", []),
            ("military_rank", None),
            ("dropout", None),
            ("death_date", None),
            ("status", "在任"),
        ])
        bp = profile.get("birthplace") or {}
        if bp.get("city_zh") or bp.get("state_zh") or bp.get("en"):
            value = f"{bp.get('state_zh') or ''}{bp.get('city_zh') or ''}" or bp.get("en")
            person["birth_place"] = fact(value, book.add(bp.get("source"), bp.get("quote")))
        for key in ("gender", "birth_date", "birth_place"):
            self.stats[f"person.{key}_" + ("filled" if person[key] else "empty")] += 1
        if person["gender"] is None:
            ctx["unknowns"].append("性别：底稿无值")
        if person["birth_date"] is None:
            ctx["unknowns"].append("出生年月：底稿无值")
        return person

    # ── 党派 ────────────────────────────────────────────────────────────────
    def _party(self, profile: dict, person: dict, ctx: dict) -> None:
        book: SourceBook = ctx["book"]
        code = str(profile.get("party") or "").strip()
        party_zh = PARTY_ZH.get(code)
        office = profile.get("current_position") or {}
        party_src = book.add(office.get("source"), office.get("quote"))
        if ctx["tier"] == "draft":
            party_src = book.add("unitedstates/congress-legislators") or party_src
        if party_zh:
            person["party"] = fact(party_zh, party_src)
            self.stats["person.party_filled"] += 1
        else:
            self.stats["person.party_empty"] += 1
            ctx["unknowns"].append(f"党派：底稿党派代码「{code}」无法识别")
            return
        fp = profile.get("first_paragraph") or ""
        if code == "I":
            self.stats["party_joined_at_independent"] += 1
            for s in PARTY_SENTENCE_RE.findall(fp):
                ctx["excluded"].append(f"发布稿入党句「{s}」：官方党籍为无党派，不写入党时间")
            return
        pj = profile.get("party_join") or {}
        anchor = (pj.get("anchor") or "").strip()
        if not anchor and pj.get("history") and pj.get("note"):
            anchor = pj["note"].strip()
        src_ids = book.add(pj.get("source"), pj.get("quote"))
        if pj.get("corroboration"):
            src_ids += book.add(pj["corroboration"])
        if not pj or not pj.get("year") or not anchor or not src_ids:
            self.stats["party_joined_at_empty_no_anchor"] += 1
            sentences = PARTY_SENTENCE_RE.findall(fp)
            for s in sentences:
                ctx["excluded"].append(f"发布稿入党句「{s}」：底稿无锚点、无来源，不采用")
            reason = "底稿无锚点" if not anchor else ("底稿无来源" if not src_ids else "底稿无年份")
            ctx["unknowns"].append(f"入党时间：{reason}，按 party_join.md 重新取锚点")
            return
        year = int(pj["year"])
        month = pj.get("month")
        level = classify_anchor(anchor)
        # 月份：只取锚点事件本身的日期。底稿按旧规则推定的月份（month_inferred）不采用，
        # 除非锚点文字里明写了同年同月。
        anchor_month, anchor_date = self._anchor_date(anchor, year)
        keep_month = bool(month) and (not pj.get("month_inferred") or anchor_month == int(month))
        value = date_value(year, month if keep_month else None)
        if month and not keep_month:
            self.stats["party_joined_at_month_dropped"] += 1
            note = pj.get("note") or "按旧规则推定"
            ctx["excluded"].append(f"入党月份「{year}年{int(month)}月」：底稿推定（{note}），不采用，只写到年")
        from_date = anchor_date or value
        urls = "、".join(e["url"] or e["title"] for e in book.entries if e["id"] in src_ids)
        if level:
            basis = f"第{level}级锚点：{anchor}；来源：{urls}"
            inf = inference(ANCHOR_RULES[level], from_date, None)
        else:
            basis = f"锚点级别未能自动判定：{anchor}；来源：{urls}"
            inf = None
            self.review["party_anchor_unclassified"].append({"id": ctx["pid"], "anchor": anchor})
        if pj.get("history"):
            hist = "；".join(f"{h.get('year')}年{h.get('month')}月 {h.get('party')}" for h in pj["history"])
            basis += f"；党籍沿革：{hist}"
        if pj.get("note") and pj["note"].strip() != anchor:
            basis += f"；底稿备注：{pj['note'].strip()}"
        wiki_only = book.wiki_only(src_ids)
        if wiki_only:
            basis += "；来源仅维基百科，待补原始来源（party_join.md 四.8）"
        person["party_joined_at"] = fact(value, src_ids, True, basis, inf)
        self.stats["party_joined_at_kept"] += 1
        self.review["party_joined_at_kept"].append({
            "id": ctx["pid"], "name_zh": profile.get("name_zh"), "value": value,
            "level": level, "month_dropped": bool(month and not keep_month), "wiki_only": wiki_only,
        })

    @staticmethod
    def _anchor_date(anchor: str, year: int) -> tuple[int | None, str | None]:
        """从锚点文字里找同一年的日期：YYYY-MM-DD、YYYY年M月D日、YYYY年M月、YYYY-MM。"""
        for m in re.finditer(r"(\d{4})-(\d{1,2})-(\d{1,2})", anchor):
            if int(m.group(1)) == year:
                return int(m.group(2)), f"{year:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        for m in re.finditer(r"(\d{4})年(\d{1,2})月(\d{1,2})日", anchor):
            if int(m.group(1)) == year:
                return int(m.group(2)), f"{year:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        for m in re.finditer(r"(\d{4})年(\d{1,2})月", anchor):
            if int(m.group(1)) == year:
                return int(m.group(2)), f"{year:04d}-{int(m.group(2)):02d}"
        for m in re.finditer(r"(\d{4})-(\d{2})(?!\d)", anchor):
            if int(m.group(1)) == year:
                return int(m.group(2)), f"{year:04d}-{int(m.group(2)):02d}"
        return None, None

    # ── 现任 ────────────────────────────────────────────────────────────────
    def _office_organization(self, profile: dict) -> str:
        cat = profile.get("category") or ""
        state = (profile.get("state") or "").strip()
        district = str(profile.get("district") or "").strip()
        if cat == "president":
            return "美国总统"
        if cat == "vice_president":
            return "美国副总统"
        if state in TERRITORY_ZH:
            area = TERRITORY_ZH[state]
            if state == "PR":
                return f"美国国会众议院{area}常驻代表（无表决权）"
            return f"美国国会众议院{area}代表（无表决权）"
        st = STATE_ZH.get(state, state)
        if cat in SENATE_CATEGORIES:
            return f"美国联邦参议员（{st}）"
        if district and district != "0":
            return f"美国联邦众议员（{st}第{district}选区）"
        return f"美国联邦众议员（{st}）"

    def _current_roles(self, profile: dict, ctx: dict, office_src: list[str]) -> list[dict]:
        book: SourceBook = ctx["book"]
        chamber = ctx["chamber"]
        cp = profile.get("current_position") or {}
        start_value = date_value(cp.get("start_year"), cp.get("start_month"), cp.get("start_day"))
        roles: list[dict] = []

        def add(sector: str, organization: str, role: str, start, source_ids: list[str]) -> None:
            key = (sector, re.sub(r"^(美国|参议院|众议院|国会)+", "", organization))
            for r in roles:
                if (r["sector"], re.sub(r"^(美国|参议院|众议院|国会)+", "", r["organization"])) == key:
                    if role and not r["role"]:
                        r["role"] = role
                        r["roles"] = [role]
                    return
            roles.append(OrderedDict([
                ("organization", organization),
                ("role", role),
                ("start", start),
                ("concurrent", sector == "committee"),
                ("source_ids", list(source_ids)),
                ("roles", [role] if role else []),
                ("sector", sector),
                ("appointment_state", "正式"),
            ]))

        add("politics", self._office_organization(profile), "",
            fact(start_value, office_src) if start_value else None, office_src)

        if ctx["tier"] == "gold":
            committees = profile.get("committees_119") or {}
            cm_src = book.add(committees.get("source"), committees.get("quote"))
            for entry in split_sources(committees.get("value")):
                for seg in split_role_text(entry):
                    for parsed in parse_role_segment(seg, chamber):
                        add(parsed["sector"], parsed["organization"], parsed["role"], None, cm_src)
            extra_segments = split_role_text(cp.get("title_zh") or "")
        else:
            extra_segments = split_role_text(profile.get("current_position_text") or "")
        for seg in extra_segments:
            for parsed in parse_role_segment(seg, chamber):
                add(parsed["sector"], parsed["organization"], parsed["role"], None,
                    office_src if ctx["tier"] == "gold" else [])
        roles.sort(key=lambda r: 0 if r["sector"] == "politics" else 1)
        return roles

    # ── 履历：T1 ────────────────────────────────────────────────────────────
    def _careers_t1(self, profile: dict, ctx: dict) -> list[dict]:
        book: SourceBook = ctx["book"]
        careers: list[dict] = []
        for e in profile.get("education") or []:
            degree = (e.get("degree") or "").strip()
            inst = clean_name(e.get("institution"))
            if degree == "高中":
                ctx["excluded"].append(f"高中学习经历：{inst}（高中不入履历）")
                self.stats["careers_study_highschool_excluded"] += 1
                continue
            if not e.get("start_year"):
                ctx["unknowns"].append(f"履历：{inst}学习无入学年份，未收录")
                self.stats["careers_study_dropped_no_start"] += 1
                continue
            main_src = book.add(e.get("source"), e.get("quote"))
            level = study_level(degree)
            end_year = e.get("end_year")
            if e.get("start_source"):
                start = fact(date_value(e["start_year"]), book.add(e["start_source"], e.get("start_quote")))
            elif e.get("start_inferred"):
                basis = e.get("start_basis") or e.get("note") or "底稿按学制倒推"
                if level == "bachelor" and end_year and int(end_year) - int(e["start_year"]) == 4:
                    inf = inference("graduation_minus_four", str(end_year), 4)
                    basis = f"推算：毕业年份减4；底稿依据：{basis}"
                    self.stats["careers_study_start_inferred_rule"] += 1
                elif end_year:
                    inf = inference("degree_duration", str(end_year), int(end_year) - int(e["start_year"]))
                    basis = f"推算：按学制从毕业年倒推（现行规则未列明此推算，待复核）；底稿依据：{basis}"
                    self.stats["careers_study_start_inferred_duration"] += 1
                else:
                    inf = None
                    basis = f"推算：{basis}"
                    self.stats["careers_study_start_inferred_other"] += 1
                start = fact(date_value(e["start_year"]), main_src, True, basis, inf)
            else:
                start = fact(date_value(e["start_year"]), main_src)
            degree_awarded = None
            if degree and not degree.startswith(("（", "(")):
                degree_awarded = fact(clean_name(degree), main_src)
            careers.append(self._career(
                "study", start,
                fact(date_value(end_year), main_src) if end_year else None,
                inst, None, (e.get("field") or "").strip() or None, degree_awarded, main_src,
                ongoing=False, study_level=level,
            ))
        for c in profile.get("career") or []:
            title = (c.get("title_zh") or "").strip()
            if STUDY_TITLE_RE.match(title):
                continue  # 学习经历以 education 为准
            src = book.add(c.get("source"), c.get("quote"))
            if c.get("month_source"):
                src += book.add(c["month_source"])
            start_value = date_value(c.get("start_year"), c.get("start_month"), c.get("start_day"))
            if not start_value:
                ctx["unknowns"].append(f"履历：{clean_name(c.get('org_zh'))}{title}无起始年份，未收录")
                self.stats["careers_work_dropped_no_start"] += 1
                continue
            start = fact(start_value, src, bool(c.get("start_inferred")),
                         (f"推算：{c.get('note')}" if c.get("start_inferred") and c.get("note") else
                          ("推算：底稿标为推定" if c.get("start_inferred") else None)), None)
            end = None
            if c.get("end_year"):
                end_inferred = bool(c.get("derived_end") or c.get("end_inferred"))
                end = fact(date_value(c["end_year"], c.get("end_month"), c.get("end_day")), src, end_inferred,
                           (f"推算：{c.get('derived_note') or c.get('note') or '底稿标为推定'}" if end_inferred else None),
                           None)
            role = clean_name(title) or None
            annotations = []
            ann_src = src + book.add(c.get("concurrent_sources"))
            for text in c.get("concurrent") or []:
                ann = self._annotation_from_text(text, ann_src, ctx, clean_name(c.get("org_zh")))
                if ann:
                    annotations.append(ann)
            careers.append(self._career(
                "work", start, end, clean_name(c.get("org_zh")), role, None, None, src,
                ongoing=c.get("end_year") is None, annotations=annotations,
            ))
        careers.sort(key=lambda x: x["start"]["value"])
        return careers

    def _annotation_from_text(self, text: str, source_ids: list[str], ctx: dict, parent_org: str) -> dict | None:
        """底稿的"其间"字符串 → annotation；没有年份的记入 unknowns。"""
        m = ANN_RE.match(text.strip())
        if not m:
            ctx["unknowns"].append(f"履历括注无起止年份，未结构化（{parent_org}）：{text}")
            self.stats["annotations_unparsed"] += 1
            return None
        y1 = int(m.group("y1"))
        m1 = m.group("m1") or m.group("m1b")
        body = strip_leading_verb(m.group("text"))
        open_ended = False
        if body.startswith("起"):
            body = strip_leading_verb(body[1:])
            open_ended = True
        start = fact(date_value(y1, m1, m.group("d1")), source_ids, bool(m.group("approx")),
                     "推算：底稿作“约”" if m.group("approx") else None, None)
        ongoing = False
        if m.group("dash"):
            if m.group("y2"):
                end = fact(date_value(m.group("y2"), m.group("m2") or m.group("m2b")), source_ids)
            else:
                end, ongoing = None, True
        elif open_ended:
            end = None
        else:
            end = fact(date_value(y1, m1), source_ids)
        self.stats["annotations_parsed"] += 1
        return self._annotation(body, start, end, ongoing, source_ids)

    def _annotation(self, body: str, start: dict, end: dict | None, ongoing: bool, source_ids: list[str]) -> dict:
        study = parse_study(body)
        if study:
            kind, org, prog = "study", clean_name(study["org"]), study["prog"]
            degree_awarded = fact(clean_name(study["deg"]), source_ids) if study["deg"] else None
        else:
            kind, org, prog, degree_awarded = "part_time", clean_name(body), None, None
        return OrderedDict([
            ("kind", kind), ("start", start), ("end", end), ("ongoing", ongoing),
            ("organization", org), ("role", None), ("roles", []), ("program", prog),
            ("degree_awarded", degree_awarded), ("concurrent", True), ("source_ids", list(source_ids)),
        ])

    @staticmethod
    def _career(kind, start, end, organization, role, program, degree_awarded, source_ids,
                ongoing=False, study_level=None, annotations=None) -> dict:
        roles = [r for r in split_top_level(role, "、") if r] if role else []
        return OrderedDict([
            ("kind", kind), ("start", start), ("end", end), ("organization", organization),
            ("role", role), ("program", program), ("degree_awarded", degree_awarded),
            ("concurrent", False), ("source_ids", list(source_ids)), ("ongoing", bool(ongoing)),
            ("roles", roles), ("role_category", None), ("role_basis", None),
            ("study_level", study_level), ("full_time", None), ("annotations", annotations or []),
            ("appointment_state", None), ("stable_identity", False),
        ])

    # ── 履历：T2（career_text 字符串） ───────────────────────────────────────
    @staticmethod
    def parse_timeline_line(line: str) -> dict | None:
        """"1992年-1996年 X" / "1997年-     X" / "2011年 X" → start/end/ongoing/body。"""
        m = LINE_RE.match(line.strip())
        if not m or not m.group("body").strip():
            return None
        y1, y2, dash = int(m.group(1)), m.group(3), m.group("dash")
        if dash and y2:
            return {"start": y1, "end": int(y2), "ongoing": False, "body": m.group("body").strip()}
        if dash:
            return {"start": y1, "end": None, "ongoing": True, "body": m.group("body").strip()}
        return {"start": y1, "end": y1, "ongoing": False, "body": m.group("body").strip()}

    def _careers_t2(self, profile: dict, ctx: dict) -> list[dict]:
        careers: list[dict] = []
        for line in profile.get("career_text") or []:
            parsed = self.parse_timeline_line(line)
            if not parsed:
                ctx["unknowns"].append(f"履历行无法解析：{line}")
                self.stats["careers_t2_line_unparsed"] += 1
                continue
            q = QIJIAN_RE.match(parsed["body"])
            main, inner = (q.group("main").strip(), q.group("inner")) if q else (parsed["body"], "")
            annotations = []
            for seg in re.split(r"[；;]", inner):
                seg = seg.strip()
                if not seg:
                    continue
                sub = self.parse_timeline_line(seg)
                if not sub:
                    ctx["unknowns"].append(f"履历括注无法解析（{main}）：{seg}")
                    self.stats["annotations_unparsed"] += 1
                    continue
                self.stats["annotations_parsed"] += 1
                annotations.append(self._annotation(
                    sub["body"], fact(date_value(sub["start"]), []),
                    fact(date_value(sub["end"]), []) if sub["end"] else None, sub["ongoing"], []))
            start = fact(date_value(parsed["start"]), [])
            end = fact(date_value(parsed["end"]), []) if parsed["end"] else None
            study = parse_study(main)
            if study:
                if study["parsed"]:
                    self.stats["careers_study_parsed"] += 1
                else:
                    self.stats["careers_study_unparsed"] += 1
                careers.append(self._career(
                    "study", start, end, clean_name(study["org"]), None, study["prog"],
                    fact(clean_name(study["deg"]), []) if study["deg"] else None, [],
                    ongoing=parsed["ongoing"], study_level=study["level"], annotations=annotations))
            else:
                careers.append(self._career(
                    "work", start, end, clean_name(main), None, None, None, [],
                    ongoing=parsed["ongoing"], annotations=annotations))
        return careers

    # ── 学历、学位、参加工作 ───────────────────────────────────────────────
    def _highest_degree(self, entries: list[dict]) -> dict | None:
        """entries: {org, prog, deg, level, end}；取最高学位，同级取毕业最晚的。"""
        best = None
        for e in entries:
            if not e.get("deg") or not e.get("level"):
                continue
            key = (LEVEL_RANK[e["level"]], e.get("end") or "")
            if best is None or key > best[0]:
                best = (key, e)
        return best[1] if best else None

    def _set_education(self, person: dict, best: dict | None, source_ids: list[str], ctx: dict, header: dict | None) -> None:
        if best:
            person["degree"] = fact(compose_degree(best["org"], best.get("prog"), best["deg"]), source_ids)
            xueli = (header or {}).get("xueli") or LEVEL_XUELI[best["level"]]
            person["education"] = fact(xueli, source_ids)
            self.stats["person.degree_filled"] += 1
            self.stats["person.education_filled"] += 1
        else:
            self.stats["person.degree_empty"] += 1
            if header and header.get("xueli"):
                person["education"] = fact(header["xueli"], source_ids)
                self.stats["person.education_filled"] += 1
            else:
                self.stats["person.education_empty"] += 1
            ctx["unknowns"].append("学历学位：履历无带学位的学习条目")

    def _person_education_t1(self, profile: dict, person: dict, ctx: dict) -> None:
        book: SourceBook = ctx["book"]
        entries = []
        src_ids: list[str] = []
        for e in profile.get("education") or []:
            degree = (e.get("degree") or "").strip()
            if not degree or degree == "高中" or degree.startswith(("（", "(")):
                continue
            level = study_level(degree)
            if not level:
                continue
            entries.append({"org": e.get("institution") or "", "prog": (e.get("field") or "").strip() or None,
                            "deg": degree, "level": level, "end": str(e.get("end_year") or "")})
        best = self._highest_degree(entries)
        if best:
            for e in profile.get("education") or []:
                if (e.get("institution") or "") == best["org"] and (e.get("degree") or "").strip() == best["deg"]:
                    src_ids = book.add(e.get("source"), e.get("quote"))
                    break
        header = profile.get("education_header") or {}
        if not src_ids and header.get("source"):
            src_ids = book.add(header["source"])
        self._set_education(person, best, src_ids, ctx, header)

    def _person_education_t2(self, careers: list[dict], person: dict, ctx: dict) -> None:
        entries = []
        for c in careers:
            if c["kind"] == "study" and c["degree_awarded"] and c["study_level"]:
                entries.append({"org": c["organization"], "prog": c["program"], "deg": c["degree_awarded"]["value"],
                                "level": c["study_level"], "end": (c["end"] or {}).get("value", "")})
        self._set_education(person, self._highest_degree(entries), [], ctx, None)

    def _work_start_t1(self, profile: dict, person: dict, ctx: dict) -> None:
        book: SourceBook = ctx["book"]
        ws = profile.get("work_start") or {}
        if not ws.get("year"):
            self.stats["person.work_start_empty"] += 1
            ctx["unknowns"].append("参加工作时间：底稿无值")
            return
        src = book.add(ws.get("source"), ws.get("quote"))
        if ws.get("corroboration"):
            src += book.add(ws["corroboration"])
        month = ws.get("month") if ws.get("month") and ws.get("month_inferred") is False else None
        if ws.get("month") and not month:
            ctx["excluded"].append(
                f"参加工作月份「{ws['year']}年{int(ws['month'])}月」：底稿推定（{ws.get('note') or '按旧规则推定'}），不采用，只写到年")
            self.stats["work_start_month_dropped"] += 1
        value = date_value(ws["year"], month)
        desc = (ws.get("description") or "").strip()
        note = (ws.get("note") or "").strip()
        basis = f"按第一份全职工作的开始时间推算：{desc}" if desc else "按第一份全职工作的开始时间推算"
        if note:
            basis += f"；底稿备注：{note}"
        person["work_start"] = fact(value, src, True, basis, inference("first_full_time_job", value, None))
        if "参加工作" not in desc and (ENLIST_RE.search(desc) or "入伍" in note):
            person["work_start_action"] = "入伍"
        self.stats["person.work_start_filled"] += 1

    def _work_start_t2(self, profile: dict, careers: list[dict], person: dict, ctx: dict) -> None:
        first = next((c for c in careers if c["kind"] == "work"), None)
        if not first:
            self.stats["person.work_start_empty"] += 1
            ctx["unknowns"].append("参加工作时间：履历无非学习条目")
            return
        year = first["start"]["value"][:4]
        published = ""
        for inf in profile.get("inferences") or []:
            if inf.get("field") == "work_start":
                published = inf.get("published") or ""
        basis = f"按履历首个非学习条目的开始年份推算：{first['organization']}（T2 Codex 初稿时间线，无来源）"
        if published:
            basis += f"；发布稿作「{published}」，月份为旧规则推定，不采用"
        person["work_start"] = fact(year, [], True, basis, inference("first_full_time_job", year, None))
        self.stats["person.work_start_filled"] += 1
        if MILITARY_RE.search(first["organization"]):
            ctx["unknowns"].append(f"参加工作/入伍写法待定：首个履历条目为军职（{first['organization']}）")
            self.review["work_start_military"].append({"id": ctx["pid"], "organization": first["organization"]})

    # ── 冲突 ────────────────────────────────────────────────────────────────
    @staticmethod
    def _conflicts_t1(profile: dict) -> list[dict]:
        out = []
        for c in profile.get("conflicts") or []:
            alternatives = [fact(str(c.get(k)), []) for k in ("a", "b") if c.get(k)]
            out.append(OrderedDict([
                ("field", str(c.get("item") or "")),
                ("alternatives", alternatives),
                ("selected_value", str(c["adopted"]) if c.get("adopted") else None),
                ("reason", "底稿（Care 2026-06）记录的取舍，官方来源优先；待本机复核"),
            ]))
        return out


# ────────────────────────────────────────────────────────────────────────────
# 报告
# ────────────────────────────────────────────────────────────────────────────

def pct(n: int, d: int) -> str:
    return f"{(100.0 * n / d):.1f}%" if d else "-"


def build_report(conv: Converter, results: list[dict]) -> dict:
    s = conv.stats
    total = len(results)
    valid = sum(1 for r in results if r["valid"])
    by_tier = {}
    for tier in ("gold", "draft"):
        rows = [r for r in results if r["tier"] == tier]
        by_tier[tier] = {"total": len(rows), "valid": sum(1 for r in rows if r["valid"])}
    gold_ids = {r["id"] for r in results if r["tier"] == "gold"}
    listed_not_gold = sorted(conv.t1_ids - gold_ids)
    gold_not_listed = sorted(gold_ids - conv.t1_ids)
    conv.review["tiering_mismatch"] = [
        {"id": i, "note": "TIERING.md 的 T1 名单列出，但底稿仍是 Codex 初稿（未精做），按底稿标 draft"}
        for i in listed_not_gold if i in {r["id"] for r in results}
    ]
    fields = OrderedDict([
        ("person.gender", {"filled": s["person.gender_filled"], "empty": s["person.gender_empty"]}),
        ("person.birth_date", {"filled": s["person.birth_date_filled"], "empty": s["person.birth_date_empty"]}),
        ("person.birth_place", {"filled": s["person.birth_place_filled"], "empty": s["person.birth_place_empty"]}),
        ("person.work_start", {"filled": s["person.work_start_filled"], "empty": s["person.work_start_empty"],
                               "month_dropped": s["work_start_month_dropped"]}),
        ("person.education", {"filled": s["person.education_filled"], "empty": s["person.education_empty"]}),
        ("person.degree", {"filled": s["person.degree_filled"], "empty": s["person.degree_empty"]}),
        ("person.party", {"filled": s["person.party_filled"], "empty": s["person.party_empty"]}),
        ("person.party_joined_at", {
            "kept": s["party_joined_at_kept"], "month_dropped": s["party_joined_at_month_dropped"],
            "empty_no_anchor": s["party_joined_at_empty_no_anchor"],
            "empty_independent": s["party_joined_at_independent"],
        }),
        ("current_roles", {"politics_entries": s["current_roles_politics"],
                           "committee_entries": s["current_roles_committee"],
                           "items_with_committee": s["items_with_committee"]}),
        ("careers", {"total": s["careers_total"], "work": s["careers_work"], "study": s["careers_study"],
                     "with_sources": s["careers_with_sources"], "without_sources": s["careers_without_sources"],
                     "work_role_unsplit": s["careers_role_unsplit"],
                     "study_parsed_t2": s["careers_study_parsed"], "study_unparsed_t2": s["careers_study_unparsed"],
                     "study_start_inferred_rule": s["careers_study_start_inferred_rule"],
                     "study_start_inferred_duration": s["careers_study_start_inferred_duration"],
                     "study_start_inferred_other": s["careers_study_start_inferred_other"],
                     "study_dropped_no_start": s["careers_study_dropped_no_start"],
                     "study_highschool_excluded": s["careers_study_highschool_excluded"],
                     "work_dropped_no_start": s["careers_work_dropped_no_start"],
                     "t2_line_unparsed": s["careers_t2_line_unparsed"]}),
        ("annotations", {"parsed": s["annotations_parsed"], "unparsed": s["annotations_unparsed"]}),
        ("conflicts", {"items_with_conflicts": s["items_with_conflicts"]}),
        ("unknowns", {"total": s["unknowns_total"]}),
        ("excluded", {"total": s["excluded_total"]}),
    ])
    return OrderedDict([
        ("generated_at", conv.date),
        ("summary", OrderedDict([
            ("total", total), ("valid", valid), ("invalid", total - valid), ("pass_rate", pct(valid, total)),
            ("by_tier", by_tier),
        ])),
        ("fields", fields),
        ("review", OrderedDict([
            ("invalid_items", [{"id": r["id"], "errors": r["errors"]} for r in results if not r["valid"]]),
            ("tiering_mismatch", conv.review["tiering_mismatch"]),
            ("tiering_listed_count", len(conv.t1_ids)),
            ("gold_not_listed_by_id", gold_not_listed),
            ("party_joined_at_kept", conv.review["party_joined_at_kept"]),
            ("party_anchor_unclassified", conv.review["party_anchor_unclassified"]),
            ("work_start_military", conv.review["work_start_military"]),
            ("generated_names", conv.review["generated_names"]),
        ])),
    ])


def render_report_md(report: dict) -> str:
    s = report["summary"]
    f = report["fields"]
    r = report["review"]
    lines = [
        "# 人事三局导入报告（import_ceo_profiles.py）",
        "",
        f"生成日期：{report['generated_at']}。校验依据 shared/schema/bundle.schema.json，脚本内置校验器，不联网。",
        "",
        "## 校验通过率",
        "",
        "| 分级 | 份数 | 通过 | 通过率 |",
        "|---|---|---|---|",
        f"| 全部 | {s['total']} | {s['valid']} | {s['pass_rate']} |",
    ]
    for tier, label in (("gold", "gold（T1 精做稿）"), ("draft", "draft（T2 Codex 初稿）")):
        t = s["by_tier"].get(tier, {"total": 0, "valid": 0})
        lines.append(f"| {label} | {t['total']} | {t['valid']} | {pct(t['valid'], t['total'])} |")
    lines += ["", "## 字段转换统计", "", "| 字段 | 统计 |", "|---|---|"]
    for key, val in f.items():
        lines.append(f"| {key} | " + "，".join(f"{k} {v}" for k, v in val.items()) + " |")
    lines += [
        "",
        "说明：",
        "",
        "- party_joined_at：kept 为底稿有锚点且有来源、按 party_join.md 保留的；month_dropped 为底稿按旧规则推定的月份被去掉、只写到年的；"
        "empty_no_anchor 为置空并记入 unknowns 的；empty_independent 为无党派人士，不写入党时间。",
        "- careers.work_role_unsplit：T2 时间线整句存入 organization、role 为 null 的条目，待本机拆分单位与职务。",
        "- careers.without_sources：T2 时间线来自 Codex 初稿，无逐条来源，事实包里 source_ids 为空。",
        "- careers.study_start_inferred_duration：底稿按学制倒推的入学年（硕士、法学院等），现行 cadre.md 只明确允许本科减 4 年，保留为推算并待复核。",
        "- 不转换的内容：股票交易披露（disclosure）、military 军旅记录、Codex 修改记录（codex_changes/codex_diff）、照片路径。",
        "",
        "## 留给本机复核",
        "",
        f"1. 入党时间保留 {len(r['party_joined_at_kept'])} 份（全部为 gold）。月份按 party_join.md 第二节重核；"
        f"来源仅维基百科的 {sum(1 for x in r['party_joined_at_kept'] if x['wiki_only'])} 份要补原始来源（四.8）；"
        f"锚点级别未能自动判定的 {len(r['party_anchor_unclassified'])} 份见下表。",
        f"2. T1 名单与底稿不一致 {len(r['tiering_mismatch'])} 份：" + (
            "；".join(f"{x['id']}（{x['note']}）" for x in r["tiering_mismatch"]) or "无"),
        f"3. 底稿为精做稿但 TIERING.md 只以姓氏列出、未写编号的 {len(r['gold_not_listed_by_id'])} 份：" + (
            "、".join(r["gold_not_listed_by_id"]) or "无") + "。按底稿结构标 gold。",
        f"4. 译名来源为 generated 的 {len(r['generated_names'])} 份，对照 shared/glossary 人名表复核：" + (
            "、".join(x["id"] for x in r["generated_names"]) or "无"),
        f"5. T2 首个履历条目为军职、参加工作/入伍写法待定的 {len(r['work_start_military'])} 份：" + (
            "、".join(x["id"] for x in r["work_start_military"]) or "无"),
        "6. 全部 draft 条目不发布：履历、学历、出生地无来源，入党时间置空，待逐人重新取证。",
        f"7. 校验未通过 {len(r['invalid_items'])} 份：" + (
            "；".join(f"{x['id']}：{x['errors'][0]}" for x in r["invalid_items"]) or "无"),
        "",
        "## 入党时间保留清单",
        "",
        "| 编号 | 姓名 | 值 | 锚点级别 | 月份被去掉 | 来源仅维基 |",
        "|---|---|---|---|---|---|",
    ]
    for x in r["party_joined_at_kept"]:
        lines.append(f"| {x['id']} | {x['name_zh']} | {x['value']} | {x['level'] or '未判定'} | "
                     f"{'是' if x['month_dropped'] else ''} | {'是' if x['wiki_only'] else ''} |")
    if r["party_anchor_unclassified"]:
        lines += ["", "### 锚点级别未能自动判定", ""]
        for x in r["party_anchor_unclassified"]:
            lines.append(f"- {x['id']}：{x['anchor']}")
    lines.append("")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────────────────────

def run(source: Path, schema_path: Path, out: Path, date: str, ids: list[str] | None = None,
        quiet: bool = False) -> dict:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = MiniValidator(schema)
    conv = Converter(source, date)
    files = sorted((source / "profiles").glob("*.json"))
    if ids:
        wanted = set(ids)
        files = [p for p in files if p.stem in wanted]
        missing = wanted - {p.stem for p in files}
        if missing:
            raise SystemExit(f"找不到底稿：{'、'.join(sorted(missing))}")
    if not files:
        raise SystemExit(f"目录里没有底稿：{source / 'profiles'}")
    bundles_dir = out / "bundles"
    bundles_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for path in files:
        bundle = conv.convert_file(path)
        errors = validator.validate(bundle)
        item = bundle["items"][0]
        pid = path.stem
        (bundles_dir / f"{pid}.json").write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        person = item["person"]
        results.append({
            "id": pid, "subject_id": item["subject_id"], "tier": item["tags"][0], "chamber": item["tags"][1],
            "valid": not errors, "errors": errors, "item": item,
            "name_zh": person["name"]["value"],
            "party": (person["party"] or {}).get("value", ""),
            "party_joined_at": (person["party_joined_at"] or {}).get("value", ""),
            "work_start": (person["work_start"] or {}).get("value", ""),
            "degree": (person["degree"] or {}).get("value", ""),
        })
        if errors and not quiet:
            print(f"[FAIL] {pid}: {errors[0]}", file=sys.stderr)
    report = build_report(conv, results)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "report.md").write_text(render_report_md(report), encoding="utf-8")
    with open(out / "index.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["id", "subject_id", "name_zh", "name_en", "tier", "chamber", "state", "district", "party",
                    "batch", "valid", "error_count", "unknown_count", "party_joined_at", "work_start", "degree",
                    "careers", "current_roles", "file"])
        for r in results:
            roster = conv.roster.get(r["id"], {})
            progress = conv.progress.get(r["id"], {})
            w.writerow([
                r["id"], r["subject_id"], r["name_zh"], roster.get("name", ""), r["tier"], r["chamber"],
                roster.get("state", ""), roster.get("district", ""), r["party"], progress.get("batch", ""),
                "yes" if r["valid"] else "no", len(r["errors"]), len(r["item"]["unknowns"]),
                r["party_joined_at"], r["work_start"], r["degree"], len(r["item"]["careers"]),
                len(r["item"]["current_roles"]), f"bundles/{r['id']}.json",
            ])
    if not quiet:
        s = report["summary"]
        print(f"转换 {s['total']} 份，校验通过 {s['valid']} 份（{s['pass_rate']}），"
              f"gold {s['by_tier']['gold']['total']}，draft {s['by_tier']['draft']['total']}；"
              f"输出 {out}")
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把 Care ceo-profiles 底稿转成事实包并按 schema 校验。")
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="原料目录（只读）")
    ap.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="bundle.schema.json")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出目录")
    ap.add_argument("--date", default=_dt.date.today().isoformat(), help="事实包日期 YYYY-MM-DD，默认今天")
    ap.add_argument("--ids", nargs="*", help="只转换这些编号")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if not DATE_RE.match(args.date):
        ap.error("--date 须为 YYYY-MM-DD")
    report = run(args.source, args.schema, args.out, args.date, args.ids, args.quiet)
    return 0 if report["summary"]["invalid"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
