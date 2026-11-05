"""人物记录 → 干部简历稿件对象（shared/render 的输入）和纯文本；完整简历卡的发布门槛。

写法照 aipolis rules/cadre.md（2026-10-17 版，aipolis 45a1de2；本仓库不带副本，要点见 shared/skills/cadre-resume/SKILL.md）：
- 首段：姓名，性别，〔族裔〕，××籍，×年×月出生，××人，参加工作（入伍）与入党按先后，学位或肄业，〔院士〕，〔军衔〕。
  现任……；离任、逝世的写“曾任××”；逝世的姓名后括注生卒年月（第 1 节、第 8 节第 1、2 条、第 9 节）。
- 现任状态词：××提名人选、候任××、××当选人、拟任××、代理××、临时××（第 1 节第 4 条、第 2 节第 9 条）。
- 履历：按开始时间排，开始相同的先结束的在前；全篇日期粒度统一（第 2 节、第 3 节第 2 条）。
- 门槛：五项齐全（出生年、参加工作年有来源或可推依据）且现任职务有一级来源，才是完整简历卡，才能输出（第 4 节第 6 条）。
只把已有事实排成固定句式，不补缺项、不替事实包推测。

只用标准库。
"""

from __future__ import annotations

import re
from typing import Any

from . import bureaus, sources

_DATE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")
FULL_CARD = "完整简历卡"
#: 美国附则第 1 条：族裔只用这五个词。
ETHNICITIES = ("白人", "非洲裔", "拉丁裔", "亚裔", "原住民")
_FAR = (9999, 99)


def value(fact: Any) -> str:
    return str(fact.get("value", "")) if isinstance(fact, dict) else ""


def _ym(text: str) -> tuple[int, int]:
    m = _DATE.match(text or "")
    if not m:
        raise ValueError(f"日期须为 YYYY、YYYY-MM 或 YYYY-MM-DD：{text!r}")
    return int(m.group(1)), int(m.group(2) or 0)


def chinese_month(text: str, year_only: bool = False) -> str:
    """2026-09-01 → 2026年9月；2026 → 2026年；year_only 时只写年。"""
    y, m = _ym(text)
    return f"{y}年" + (f"{m}月" if m and not year_only else "")


def mixed_spacing(text: str) -> str:
    """英文和汉字之间空一格（cadre.md 第 2 节第 7 条）。"""
    text = re.sub(r"([一-鿿])([A-Za-z])", r"\1 \2", text)
    return re.sub(r"([A-Za-z])([一-鿿])", r"\1 \2", text)


def role_text(entry: dict[str, Any]) -> str:
    return "、".join(entry.get("roles") or []) or (entry.get("role") or "")


def stated(organization: str, role: str, state: str | None) -> str:
    """职务带任职状态词。"""
    body = (organization or "") + (role or "")
    if state == "提名人选":
        return body + "提名人选"
    if state == "候任":
        return "候任" + body
    if state == "当选人":
        return body + "当选人"
    if state == "拟任":
        return "拟任" + body
    if state in ("代理", "临时") and not (role or "").startswith(state):
        return (organization or "") + state + (role or "")
    return body


def country_of(record: dict[str, Any]) -> str:
    item = record.get("resume") or {}
    if item.get("country_profile") in ("us", "rw", "other"):
        return item["country_profile"]
    nationality = value((item.get("person") or {}).get("nationality")).removesuffix("籍")
    return {"美国": "us", "卢旺达": "rw"}.get(nationality, "other")


# ──────────────────────────────────────────────────────────────── 日期粒度


def _career_dates(careers: list[dict[str, Any]]) -> list[list[str]]:
    rows = []
    for c in careers:
        dates = [value(c.get("start")), value(c.get("end"))]
        for a in c.get("annotations") or []:
            dates += [value(a.get("start")), value(a.get("end"))]
        rows.append([d for d in dates if d])
    return rows


def granularity(careers: list[dict[str, Any]]) -> str:
    """缺月份的条目不超过 2 条或全篇四分之一（取较宽）就全篇写到月，超过的全篇只写到年（第 3 节第 2 条）。"""
    rows = _career_dates(careers)
    if not rows:
        return "month"
    missing = sum(1 for dates in rows if any(len(d) == 4 for d in dates))
    return "month" if missing <= max(2, len(rows) / 4) else "year"


def span(entry: dict[str, Any], year_only: bool) -> str:
    start = value(entry["start"])
    if entry.get("kind") == "discharge":
        return chinese_month(start, year_only)
    text = chinese_month(start, year_only)
    if entry.get("end"):
        end = chinese_month(value(entry["end"]), year_only)
        if end == text and "月" not in text:
            return text  # 起止同在一年又没有月份的，只写一个年份
        text += "－" + end
    elif entry.get("ongoing"):
        text += "至今"
    return text


# ──────────────────────────────────────────────────────────────── 履历


def _program(text: str | None) -> str:
    text = text or ""
    return re.sub(r"(?:专业)?(?:在职)?(?:硕士|博士)?(?:研究生)?学习$", "", text).removesuffix("专业")


def _degree_tail(entry: dict[str, Any]) -> str:
    if not entry.get("degree_awarded"):
        return ""
    degree = value(entry["degree_awarded"])
    bare = _program(entry.get("program"))
    if bare and degree.startswith(bare):
        degree = degree[len(bare):]
    if degree and not degree.endswith("学位"):
        degree += "学位"
    return "，获" + degree if degree else ""


def study_text(entry: dict[str, Any]) -> str:
    """本科“××大学××专业学习，获××学士学位”；研究生“……硕士（博士）研究生学习，获××学位”（第 2 节第 3 条）。"""
    program = _program(entry.get("program"))
    if program and not program.endswith("专业"):
        program += "专业"
    level = {"master": "硕士研究生", "doctor": "博士研究生"}.get(entry.get("study_level") or "", "")
    return mixed_spacing(entry["organization"] + program + level + "学习" + _degree_tail(entry))


def annotation_text(entry: dict[str, Any], year_only: bool) -> str:
    """在职学习写“（其间：……）”；兼职在任写“（××起兼任……）”，已结束写“（其间：××－××兼任……）”。"""
    if entry.get("kind") == "study":
        program = _program(entry.get("program"))
        if program and not program.endswith("专业"):
            program += "专业"
        text = "（其间：" + span(entry, year_only) + "在" + entry["organization"] + program
        text += "在职研究生学习" if entry.get("concurrent") else "研究生学习"
        return mixed_spacing(text + _degree_tail(entry) + "）")
    body = entry["organization"] + role_text(entry)
    if entry.get("kind") == "review":
        return mixed_spacing("（其间：" + span(entry, year_only) + body + "）")
    if entry.get("end"):
        return mixed_spacing("（其间：" + span(entry, year_only) + "兼任" + body + "）")
    return mixed_spacing("（" + chinese_month(value(entry["start"]), year_only) + "起兼任" + body + "）")


def _sort_key(career: dict[str, Any]) -> tuple:
    """按开始时间排；同起点的，先结束的在前：单个时间点（无止期、不在任）排最前，在任的排最后（与 lint CR13 一致）。"""
    start, end = _ym(value(career["start"])), career.get("end")
    if end:
        return start, _ym(value(end))
    return start, (_FAR if career.get("ongoing") else start)


def career_entries(item: dict[str, Any]) -> tuple[list[dict[str, str]], str]:
    careers = sorted(item.get("careers") or [], key=_sort_key)
    level = granularity(careers)
    year_only = level == "year"
    entries = []
    for career in careers:
        if career["kind"] == "study":
            organization, role = "", study_text(career)
        elif career["kind"] == "discharge":
            organization, role = "", "退出现役"
        else:
            organization = ""
            role = mixed_spacing(stated(career["organization"], role_text(career), career.get("appointment_state")))
        note = "".join(annotation_text(a, year_only) for a in career.get("annotations") or [])
        entry = {"period": span(career, year_only), "organization": organization, "role": role}
        if note:
            entry["note"] = note
        entries.append(entry)
    return entries, level


# ──────────────────────────────────────────────────────────────── 首段


def birthplace_text(person: dict[str, Any], country: str) -> str:
    """出生地写“××人”：美国去掉“州”“市”，州名和城市同名的只写一次（后面跟着区名的也一样：纽约州纽约市布朗克斯区
    写“纽约布朗克斯区人”）；和国籍国同名的不写（第 1 节第 1 条、第 9 节第 3 条）。"""
    place = value(person.get("birth_place"))
    if not place:
        return ""
    nation = value(person.get("nationality")).removesuffix("籍")
    if place == nation:
        return ""
    if country == "us":
        place = place.replace("州", "").replace("市", "")
        for k in range(len(place) // 2, 1, -1):
            if place[:k] == place[k:2 * k]:
                place = place[k:]
                break
    return place.removesuffix("人") + "人"


def _last_main_role(item: dict[str, Any]) -> str:
    """离任、逝世人物首段末的“曾任××”：最后结束的主线职务（第 8 节第 1 条；任过更高职务的由人工改）。"""
    work = [c for c in item.get("careers") or [] if c.get("kind") == "work" and not c.get("concurrent") and c.get("end")]
    if not work:
        return ""
    last = max(work, key=lambda c: _ym(value(c["end"])))
    return mixed_spacing(last["organization"] + role_text(last))


def lead_paragraph(item: dict[str, Any], country: str) -> str:
    p = item["person"]
    name = value(p["name"])
    deceased = bool(p.get("death_date"))
    if deceased and p.get("birth_date"):
        name += f"（{chinese_month(value(p['birth_date']))}－{chinese_month(value(p['death_date']))}）"
    parts = [name]
    if p.get("gender"):
        parts.append(value(p["gender"]))
    if country == "us" and p.get("ethnicity") and value(p["ethnicity"]) in ETHNICITIES:
        parts.append(value(p["ethnicity"]))
    if p.get("nationality"):
        nation = value(p["nationality"])
        parts.append(nation if nation.endswith("籍") else nation + "籍")
    if p.get("birth_date") and not deceased:
        parts.append(chinese_month(value(p["birth_date"])) + "出生")
    place = birthplace_text(p, country)
    if place:
        parts.append(place)
    dated: list[tuple[tuple[int, int], int, str]] = []
    if p.get("work_start"):
        dated.append((_ym(value(p["work_start"])), 0, chinese_month(value(p["work_start"])) + (p.get("work_start_action") or "参加工作")))
    if p.get("party"):
        party = value(p["party"])
        if party == "无党派人士":
            parts.append(party)
        elif p.get("party_joined_at"):
            dated.append((_ym(value(p["party_joined_at"])), 1, chinese_month(value(p["party_joined_at"])) + "加入" + party))
    parts.extend(text for _, _, text in sorted(dated))
    if p.get("degree"):
        degree = value(p["degree"])
        parts.append(degree if degree.endswith("学位") else degree + "学位")
    elif p.get("dropout"):
        school = value(p["dropout"])
        parts.append(school if school.endswith("肄业") else school + "肄业")
    for academy in p.get("academician") or []:
        title = value(academy)
        parts.append(title if title.endswith("院士") else title + "院士")
    if p.get("military_rank"):
        rank = value(p["military_rank"])
        parts.append(rank if rank.endswith("军衔") else rank + "军衔")
    lead = "，".join(parts) + "。"
    roles = item.get("current_roles") or []
    if roles and not any(_public(r, roles) for r in roles):   # 没有公职的人（商界）首段写“现任”；有公职的现任独立成段
        lead += "现任" + "、".join(stated(r["organization"], role_text(r), r.get("appointment_state")) for r in roles) + "。"
    if not roles:
        former = _last_main_role(item)
        if former:
            lead += "曾任" + former + "。"
    return mixed_spacing(lead)


def planned_sentence(record: dict[str, Any]) -> str:
    """生效日未到的拟任、拟卸任（人物记录的 planned_roles）：“拟于2026年12月1日任××；拟于……卸任××。”"""
    groups: dict[tuple[str, str], list[str]] = {}  # 同一天、同一动作的合成一句，同一机构的职务只写一次机构名
    last_org: dict[tuple[str, str], str] = {}
    for p in sorted(record.get("planned_roles") or [], key=lambda x: x.get("date") or ""):
        key = (p.get("date") or "", p.get("action") or "任")
        org = p.get("organization") or ""
        groups.setdefault(key, []).append((org if last_org.get(key) != org else "") + (p.get("role") or ""))
        last_org[key] = org
    parts = []
    for (date, action), roles in groups.items():
        y, m = _ym(date)
        day = date[8:10]
        when = f"{y}年" + (f"{m}月" if m else "") + (f"{int(day)}日" if day.isdigit() else "")
        parts.append(f"拟于{when}{action}{'、'.join(roles)}")
    return "；".join(parts) + "。" if parts else ""


def current_paragraph(item: dict[str, Any], country: str) -> str:
    """有公职的人“现任”独立成段，只写公职：正式公职在前，委员会职务在后；卢旺达党职在前（第 9 节第 5 条、country_rw.md 第 4 条）。
    企业、社团职务不写进现任段，数据里照留（2026-10-22 维护者定：特朗普只写美国总统）。公职都还是提名人选、候任一类、
    没有到任的，现在的企业职务照写在后面。没有公职的人返回空字符串，现任写在首段。"""
    roles = item.get("current_roles") or []
    order = ("party", "politics", "military", "advisory", "committee") if country == "rw" else \
        ("politics", "military", "party", "advisory", "committee")
    public = [r for sector in order for r in roles if r.get("sector") == sector and _public(r, roles)]
    if not public:
        return ""
    if not any(r.get("appointment_state") not in bureaus.PENDING_STATES for r in public):
        public += [r for r in roles if not _public(r, roles)]
    return mixed_spacing("现任" + "、".join(stated(r["organization"], role_text(r), r.get("appointment_state")) for r in public) + "。")


#: 委员会、顾问一类职务挂在这些局的机构下才算公职（国会委员会、司法会议、白宫特使）。
_PUBLIC_BUREAUS = (bureaus.WHITEHOUSE, bureaus.CONGRESS, bureaus.MILITARY, bureaus.JUSTICE)
#: 企业和社团：公司、集团的委员会，基金会、协会、学会、商会、俱乐部的职务都不算公职。
_PRIVATE_ORG = re.compile(r"(?:公司|集团)$|基金会|协会|学会|商会|俱乐部|校友会")


def _public(role: dict[str, Any], roles: list[dict[str, Any]]) -> bool:
    """公职：政府、军队、司法、国会等的职务和党职（sector 是 politics、military、party）；委员会、顾问一类挂在公共机构下的也算。
    企业的委员会（审计委员会等）、社团职务、企业职务和民间顾问职务不算。"""
    sector = role.get("sector")
    if sector in ("politics", "military", "party"):
        return True
    if sector not in ("committee", "advisory"):
        return False
    bureau = bureaus.classify_role(role)[0]
    if bureau in _PUBLIC_BUREAUS:
        return True
    if sector == "advisory" or bureau == bureaus.AI:
        return False
    org = (role.get("organization") or "").strip()
    return not _PRIVATE_ORG.search(org) and org not in {r.get("organization") for r in roles if r.get("sector") == "enterprise"}


# ──────────────────────────────────────────────────────────────── 门槛


def _has_basis(fact: Any) -> bool:
    """有来源，或者按登记的推测规则从有来源的事实推出来（底稿注明“无来源”的不算）。"""
    if not isinstance(fact, dict) or not value(fact):
        return False
    if fact.get("source_ids"):
        return True
    return bool(fact.get("inferred") and fact.get("inference") and "无来源" not in (fact.get("basis") or ""))


def card_type(record: dict[str, Any]) -> tuple[str | None, list[str]]:
    """完整简历卡的门槛（第 4 节第 6 条）：五项齐全；现任职务有一级来源（离任的看最后的主线职务）。"""
    item = record["resume"]
    p = item["person"]
    reasons = []
    for key, label in (("name", "姓名"), ("gender", "性别"), ("nationality", "国籍")):
        if not value(p.get(key)):
            reasons.append(f"缺{label}")
    if not _has_basis(p.get("birth_date")):
        reasons.append("出生年没有来源或可推依据")
    if not _has_basis(p.get("work_start")):
        reasons.append("参加工作年没有来源或可推依据")
    roles = item.get("current_roles") or []
    primary = bureaus.primary_role(roles)
    if primary is None:
        work = [c for c in item.get("careers") or [] if c.get("kind") == "work" and c.get("end")]
        primary = max(work, key=lambda c: _ym(value(c["end"]))) if work else None
    by_id = {s["id"]: s for s in item.get("sources") or []}
    if primary is None or not any(sources.tier(by_id[i]) == 1 for i in primary.get("source_ids") or [] if i in by_id):
        reasons.append("现任职务没有一级来源")
    return (FULL_CARD if not reasons else None), reasons


# ──────────────────────────────────────────────────────────────── 合成


def compose(record: dict[str, Any]) -> dict[str, Any]:
    """人物记录 → {doc: 稿件对象, text: 纯文本, exportable, reason, card_type, date_granularity, country}。"""
    item = record["resume"]
    country = country_of(record)
    name = value(item["person"]["name"]) or record.get("name_zh") or record.get("name_en") or ""
    title = item.get("title") if isinstance(item.get("title"), str) else f"{name}简历"
    lead = lead_paragraph(item, country)
    planned = planned_sentence(record)
    if planned:
        lead = mixed_spacing(lead + planned)  # 生效日之前不改现任，首段末尾写“拟于某月某日任××”
    current = current_paragraph(item, country)
    careers, level = career_entries(item)
    doc: dict[str, Any] = {"title": title, "name": name, "lead": lead, "careers": careers}
    if current:
        doc["current"] = current
    org_tags = [r.get("organization") for r in item.get("current_roles") or [] if r.get("sector") in ("enterprise", None)]
    doc["tags"] = [t for t in org_tags if t][:2]
    lines = [title, "", lead]
    if current:
        lines += ["", current]
    for e in careers:
        lines += ["", e["period"], e["organization"] + e["role"] + e.get("note", "")]
    kind, reasons = card_type(record)
    exportable = kind == FULL_CARD
    reason = "" if exportable else "暂不能导出"  # 给用户看的只这一句；缺什么用 resume.card_type() 或 tools/gaps.py 查
    return {"doc": doc, "text": "\n".join(lines) + "\n", "exportable": exportable, "reason": reason,
            "card_type": kind, "date_granularity": level, "country": country, "bureau": bureaus.label(record.get("bureau"))}


def annotate(record: dict[str, Any]) -> dict[str, Any]:
    """把程序算出的图卡类型和日期粒度存进事实包，供审（schema cadreItem.card_type、date_granularity）。"""
    composed = compose(record)
    item = record["resume"]
    item["card_type"] = composed["card_type"]
    item["date_granularity"] = composed["date_granularity"]
    if not item.get("country_profile"):
        item["country_profile"] = composed["country"]
    return composed


def markdown(record: dict[str, Any]) -> str:
    """Markdown 文本：标题、首段、现任段、履历，只留正文。"""
    composed = compose(record)
    doc = composed["doc"]
    out = [f"# {doc['title']}", "", doc["lead"]]
    if doc.get("current"):
        out += ["", doc["current"]]
    if doc["careers"]:
        out.append("")
        for e in doc["careers"]:
            out.append(f"- {e['period']}　{e['organization']}{e['role']}{e.get('note', '')}")
    return "\n".join(out) + "\n"
