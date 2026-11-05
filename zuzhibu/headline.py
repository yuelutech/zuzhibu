"""一局动态的标题：程序按结构化字段生成一句人民网人事新闻式的标题（2026-10-21 定），不开 AI 会话。

- 机构和职务用库里的中文写法，不带英文、编号和取证说明；日期单独一栏，不写进标题。
- 姓名先用人物记录的中文名，没有的用补充译名表（shared/glossary/补充译名_person.csv）的待核译名，两处都没有才用英文原名。
- 研究、核查会话写的 headline_zh、summary_zh 只作内部记录，界面和输出一律用这里生成的标题。

只用标准库。
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from typing import Any

from . import bureaus
from .resume import mixed_spacing
from .store import REPO, Store

SUPPLEMENT = REPO / "shared" / "glossary" / "补充译名_person.csv"
_STATE_WORDS = re.compile(r"^(?:候任|拟任)|(?:提名人选|当选人)$")


@lru_cache(maxsize=1)
def supplement_names() -> dict[str, str]:
    """补充译名表的待核译名：人物编号、英文名 → 中文名。"""
    out: dict[str, str] = {}
    if SUPPLEMENT.exists():
        with SUPPLEMENT.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                name = (row.get("中文名") or "").strip()
                for key in ((row.get("person_id") or "").strip(), (row.get("english_name") or "").strip()):
                    if name and key:
                        out.setdefault(key, name)
    return out


def person_name(event: dict[str, Any], store: Store, supplement: dict[str, str] | None = None) -> tuple[str, str]:
    """(姓名, 出处)：出处是 record（人物记录）、supplement（补充译名表）或 english（英文原名）。"""
    pid = event.get("person_id") or ""
    rec = store.person(pid) if pid else None
    if rec and rec.get("name_zh"):
        return rec["name_zh"], "record"
    name_en = (rec or {}).get("name_en") or (event.get("person") or {}).get("name_en") or ""
    names = supplement_names() if supplement is None else supplement
    name = names.get(pid) or names.get(name_en)
    if name:
        return name, "supplement"
    return name_en, "english"


def _role_text(role: dict[str, Any]) -> str:
    return _STATE_WORDS.sub("", ("、".join(role.get("roles") or []) or role.get("role") or "").strip())


def post(role: dict[str, Any], strip_acting: bool = False) -> str:
    """机构加职务：大使馆的大使写“美国驻某国大使”；机构名以职务结尾的不重复（美国国家情报总监办公室 + 国家情报总监
    → 美国国家情报总监）。strip_acting 时去掉职务前的“代理”“临时”。"""
    org = (role.get("organization") or "").strip()
    text = _role_text(role)
    core = text[2:] if text.startswith(("代理", "临时")) else text
    if strip_acting:
        text = core
    if org == "美国政府":
        return "美国" + text  # 回溯动态有把驻外大使的机构写成“美国政府”的
    if org.endswith("大使馆") and text.endswith("大使"):
        m = re.match(r"驻(.+?)((?:特命全权)?大使)$", text)
        if m and ("驻" + m.group(1)) in org:
            text = m.group(2)  # 美国驻赤道几内亚大使馆 + 驻赤道几内亚大使 → 美国驻赤道几内亚大使
        return org[:-3] + text
    for tail in (core, core + "办公室"):
        if core and org.endswith(tail):
            return org[:-len(tail)] + text
    org_core = re.sub(r"^美国(?:联邦)?", "", org)
    if org_core and org_core != org and core.startswith(org_core):
        return org[:-len(org_core)] + text  # 美国司法部 + 司法部长 → 美国司法部长
    return org + text


def _main_roles(roles: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """有主职的时候，委员会委员一类不进标题（亚马逊董事兼审计委员会委员 → 亚马逊公司董事）。"""
    roles = [r for r in roles or [] if isinstance(r, dict) and ((r.get("organization") or "").strip() or _role_text(r))]
    main = [r for r in roles if r.get("sector") != "committee"]
    return main or roles


def posts(roles: list[dict[str, Any]] | None, strip_acting: bool = False, skip_org: str | None = None) -> str:
    """几项职务连写：同一机构的只写一次机构名（skip_org 给的机构一开始就不写）；超过两项的写“××等 n 个职务”。"""
    roles = _main_roles(roles)
    if not roles:
        return ""
    if len(roles) > 2:
        return f"{post(roles[0], strip_acting)}等{len(roles)}个职务"
    parts, last_org = [], skip_org
    for role in roles:
        org = (role.get("organization") or "").strip()
        parts.append(_role_text(role) if org == last_org and _role_text(role) else post(role, strip_acting))
        last_org = org
    return "、".join(parts)


def president_surname(store: Store) -> str:
    """在任美国总统的姓（人物记录现任职务的机构写“美国总统”）；找不到返回空字符串。"""
    for rec in store.people():
        for role in (rec.get("resume") or {}).get("current_roles") or []:
            if role.get("organization") == "美国总统" and not _role_text(role) and role.get("appointment_state") in (None, "正式"):
                return (rec.get("name_zh") or "").split("·")[-1]
    return ""


def event_headline(event: dict[str, Any], store: Store, supplement: dict[str, str] | None = None) -> str:
    """一条动态的标题，例如“基思·桑德林就任美国劳工部部长”“美国参议院确认某某出任某职”“特朗普提名高雄为美国海军部部长人选”
    “白宫宣布某某将出任某职”“某某卸任某职”。"""
    name, _ = person_name(event, store, supplement)
    kind = event.get("kind")
    new_roles, ended_roles = event.get("new_roles") or [], event.get("ended_roles") or []
    new, old = posts(new_roles), posts(ended_roles)
    main = (_main_roles(new_roles) or _main_roles(ended_roles) or [{}])[0]
    gov = main.get("sector") in ("politics", "military", "party") or         bureaus.classify_role(main)[0] in (bureaus.WHITEHOUSE, bureaus.CONGRESS, bureaus.MILITARY, bureaus.JUSTICE)
    if kind == "appoint":
        text = f"{name}{'就任' if gov else '出任'}{new}" if new else f"{name}就任新职"
    elif kind == "acting":
        text = f"{name}代理{posts(new_roles, strip_acting=True)}" if gov and new else f"{name}出任{new}"
    elif kind == "transfer":
        same_org = (_main_roles(ended_roles)[0].get("organization") or "").strip() if ended_roles and len(_main_roles(ended_roles)) == 1 else None
        later = posts(new_roles, skip_org=same_org)  # 由苹果公司首席执行官转任执行董事长
        text = f"{name}由{old}转任{later}" if old and new else (f"{name}转任{new}" if new else f"{name}卸任{old}")
    elif kind == "leave":
        text = f"{name}卸任{old}" if old else f"{name}离任"
    elif kind == "death":
        text = f"{posts(ended_roles[:1])}{name}逝世" if old else f"{name}逝世"  # 只写主要职务
    elif kind == "nominate":
        president = president_surname(store) if gov else ""
        text = f"{president}提名{name}为{new}人选" if president else f"{name}获提名为{new}人选"
    elif kind == "confirm":
        text = f"美国参议院确认{name}出任{new}" if gov else f"{name}获确认出任{new}"
    elif kind == "announce":
        what = f"将出任{new}" if new else f"将卸任{old}"
        text = f"白宫宣布{name}{what}" if gov else f"{name}{what}"
    else:
        text = f"{name}{new or old}"
    return mixed_spacing(text)
