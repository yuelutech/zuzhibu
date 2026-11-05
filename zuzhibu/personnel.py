"""人事动态写回人物记录（docs/PRODUCT.md 第四节）。

一条已核实的动态写回四项：现任职务、履历记录、资料更新时间、所属局。规则全部在这里，不交模型裁量：

- 正常调任：原职务移出现任、履历写止时间；新职务进现任、履历新增条目；按新的主要职务重定所属局。
- 转入七局：移出后再无现任职务，且核实结果明确没有其他现职（other_current_roles 为空列表）、
  没有公布新任命（new_appointment 为 null），三条都满足才转。缺一条就不转，动态标“待核”。
- 七局人物重新任职：按新职务转入对应业务局。
- 新任职务不在各局范围：二局人物在企业之间调动的仍归二局；其余暂留原局，动态标“待定”。
- 转局保留全部履历、来源和历史动态；每次转局记一条 bureau_history。
- 写回后按事实包 schema 校验，不通过就不落盘，动态标“写回失败”。

只用标准库。
"""

from __future__ import annotations

import copy
import difflib
import re
from typing import Any

from shared.backends import schema_lite

from . import bureaus, portraits, resume, sources
from .store import SCHEMA_PATH, Store, read_json

#: 会改动现任职务的类别；其余（提名、确认、宣布）只记录动态。
ROLE_KINDS = ("appoint", "leave", "transfer", "acting", "death")
RECORD_ONLY_KINDS = ("nominate", "confirm", "announce")
KINDS = ROLE_KINDS + RECORD_ONLY_KINDS
KIND_LABEL = {"appoint": "任命", "leave": "离任", "transfer": "调任", "acting": "代理", "death": "逝世",
              "nominate": "提名", "confirm": "确认", "announce": "宣布"}

#: 动态类别 → 新职务的任职状态（rules/cadre.md 第 1 节第 4 条、第 8 节第 7 条）。
STATE_FOR_KIND = {"appoint": "正式", "transfer": "正式", "acting": "代理", "nominate": "提名人选", "confirm": "提名人选",
                  "announce": "候任"}

#: 任职状态的进度：宣布（拟任、候任、当选人）→ 提名人选 → 到任（代理、临时、正式）。写回和导入简历时只往前走，不倒退。
STATE_RANK = {"拟任": 1, "候任": 1, "当选人": 1, "提名人选": 2, "代理": 3, "临时": 3, "正式": 4}

APPLIED, PENDING, UNDECIDED, FAILED, RECORDED, SKIPPED = (
    "applied", "pending_review", "undecided", "failed", "recorded", "skipped")
STATUS_LABEL = {APPLIED: "已写回", PENDING: "待核", UNDECIDED: "待定", FAILED: "写回失败", RECORDED: "已记录", SKIPPED: "未写回"}

_SCHEMA: dict[str, Any] | None = None


def schema() -> dict[str, Any]:
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = read_json(SCHEMA_PATH)
    return _SCHEMA


def validate_resume(item: dict[str, Any], date: str) -> list[str]:
    return schema_lite.validate({"schema_version": "1.0", "date": date, "items": [item]}, schema())


# ──────────────────────────────────────────────────────────────── 小工具


def fact(value: str, source_ids: list[str] | None = None) -> dict[str, Any]:
    return {"value": value, "source_ids": list(source_ids or []), "inferred": False, "basis": None, "inference": None}


def _norm(text: str) -> str:
    text = re.sub(r"[\s（）()、，,.·\-—]", "", text or "")
    for word in ("美国", "国会", "联邦", "合众国", "United States", "U.S.", "US "):
        text = text.replace(word, "")
    return text.lower()


def _role_key(role: dict[str, Any]) -> tuple[str, str]:
    zh = _norm((role.get("organization") or "") + ("、".join(role.get("roles") or []) or (role.get("role") or "")))
    en = _norm((role.get("organization_en") or "") + (role.get("role_en") or ""))
    return zh, en


def similarity(a: dict[str, Any], b: dict[str, Any]) -> float:
    """两条职务的相似度 0–1：中文机构加职务互相包含算 1，否则取序列相似度；英文同理，取高者。"""
    best = 0.0
    for x, y in zip(_role_key(a), _role_key(b)):
        if not x or not y:
            continue
        if x in y or y in x:
            return 1.0
        best = max(best, difflib.SequenceMatcher(None, x, y).ratio())
    return best


def _rtext(role: dict[str, Any]) -> str:
    return "、".join(role.get("roles") or []) or (role.get("role") or "")


def same_role(existing: dict[str, Any], event_role: dict[str, Any]) -> bool:
    """记录里的一项职务和动态里的一项职务是否同一职务：机构（中文或英文）和职务（中文或英文）都出现在记录那一项里。

    名册导入的职务没译时中文字段里是英文原文，所以动态的英文写法也拿来比。
    """
    hay = _norm((existing.get("organization") or "") + _rtext(existing)) + "|" + \
        _norm((existing.get("organization_en") or "") + (existing.get("role_en") or ""))
    orgs = [_norm(event_role.get("organization") or ""), _norm(event_role.get("organization_en") or "")]
    roles = [_norm(_rtext(event_role)), _norm(event_role.get("role_en") or "")]
    org_ok = any(o and o in hay for o in orgs)
    role_ok = any(r and r in hay for r in roles) or not any(roles)
    return org_ok and role_ok


_TITLE_SPLIT = re.compile(r"兼|、|，|,|/")
#: 职务名前加了这些（可以叠加，如“常务副”）就是另一个职务：法官和首席法官、部长和代理部长、常务副部长不是同一职务。
_RANK_PREFIX = re.compile(r"(?:副|代理|临时|资深|首席|常务|名誉|荣誉|执行|前)+")


def _titles(role: dict[str, Any]) -> set[str]:
    text, state = _rtext(role), role.get("appointment_state")
    if state in ("代理", "临时") and not text.startswith(state):
        text = state + text  # 代理职务有的存成“局长”加任职状态“代理”，有的直接写“代理局长”，比之前先统一
    org = _norm(role.get("organization") or "")
    titles = set()
    for part in _TITLE_SPLIT.split(text):
        title = _norm(part)
        if org and title.startswith(org) and len(title) > len(org):
            title = title[len(org):]  # “白宫法律顾问”在白宫名下就是“法律顾问”
        if title:
            titles.add(title)
    return titles


def titles_conflict(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """两项职务的职务名明确不是同一个：董事和董事长、秘书和秘书长、法官和首席法官、部长和代理部长。

    机构名加职务名互相包含时 similarity 会算成同一职务，写回时就会移错、合错，用这个挡住。
    兼任写在一起的（“总裁兼首席执行官”）拆开比，有一个相同就不算冲突。
    """
    ta, tb = _titles(a), _titles(b)
    if not ta or not tb or ta & tb:
        return False
    for x in ta:
        for y in tb:
            short, long = sorted((x, y), key=len)
            if long.startswith(short) or (long.endswith(short) and _RANK_PREFIX.fullmatch(long[:-len(short)])):
                return True
    return False


def same_title(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """同一机构、职务名（连同代理、临时）完全相同：“局长”加任职状态“代理”和“代理局长”是同一职务。"""
    ta = _titles(a)
    return bool(ta) and ta == _titles(b) and _norm(a.get("organization") or "") == _norm(b.get("organization") or "")


def _untranslated(text: str) -> bool:
    return bool(text) and not re.search(r"[\u4e00-\u9fff]", text)


def _is_congress_seat(role: dict[str, Any]) -> bool:
    text = bureaus.role_text(role)
    return bureaus.classify_role(role)[0] == bureaus.CONGRESS and bool(
        re.search(r"参议员|众议员|Senator|Representative|Delegate|Resident Commissioner", text))


def _sector_for(role: dict[str, Any]) -> str:
    if role.get("sector") in ("enterprise", "advisory", "politics", "committee"):
        return role["sector"]
    bureau, _ = bureaus.classify_role(role)
    if bureau in (bureaus.CONGRESS, bureaus.WHITEHOUSE, bureaus.MILITARY, bureaus.JUSTICE):
        return "politics"
    return "enterprise"


def _event_date(event: dict[str, Any]) -> str:
    return event.get("effective_at") or event.get("announced_at") or event.get("date")


def fill_defaults(item: dict[str, Any], country: str | None = None) -> dict[str, Any]:
    """补齐 2026-10-17 事实包 schema 新增的字段（null、[]、false），已有的不动。"""
    for src in item.get("sources") or []:
        if src.get("tier") not in (1, 2, 3):
            src["tier"] = sources.tier_of(src.get("url") or "")
    if item.get("line") != "cadre":
        return item
    person = item["person"]
    for key, value in (("ethnicity", None), ("academician", []), ("military_rank", None), ("dropout", None),
                       ("death_date", None), ("status", None)):
        person.setdefault(key, value)
    for key, value in (("country_profile", country), ("card_type", None), ("date_granularity", None),
                       ("disambiguation", None), ("review_items", [])):
        item.setdefault(key, value)
    for role in item.get("current_roles") or []:
        role.setdefault("appointment_state", None)
    for career in item.get("careers") or []:
        career.setdefault("appointment_state", None)
        career.setdefault("stable_identity", False)
    return item


def merge_sources(item: dict[str, Any], event_sources: list[dict[str, Any]], checked_at: str) -> dict[str, str]:
    """把动态的来源并进简历来源表（同一链接只记一次），返回 动态来源编号 → 简历来源编号。"""
    existing = {s["url"]: s["id"] for s in item.get("sources") or []}
    used = {s["id"] for s in item.get("sources") or []}
    mapping: dict[str, str] = {}
    n = len(used) + 1
    for src in event_sources or []:
        url = src.get("url") or ""
        if not url:
            continue
        if url in existing:
            mapping[src.get("id", url)] = existing[url]
            continue
        while f"s{n}" in used:
            n += 1
        sid = f"s{n}"
        used.add(sid)
        item.setdefault("sources", []).append({
            "id": sid, "url": url, "title": src.get("title") or url,
            "published_at": src.get("published_at") or None,
            "checked_at": src.get("checked_at") or checked_at,
            "excerpt": src.get("excerpt") or "",
            "tier": src.get("tier") if src.get("tier") in (1, 2, 3) else sources.tier_of(url),
        })
        existing[url] = sid
        mapping[src.get("id", url)] = sid
    return mapping


def _map_ids(ids: list[str] | None, mapping: dict[str, str], default: list[str]) -> list[str]:
    out = list(dict.fromkeys(mapping[i] for i in ids or [] if i in mapping))  # 动态的几条来源可能是同一网页
    return out or list(default)


def new_resume(pid: str, name_zh: str, name_en: str) -> dict[str, Any]:
    """一局新发现、库里没有的人：只建名册级简历（seed），其余字段留空，待建简历时补。"""
    shown = name_zh or name_en
    person = {k: None for k in ("gender", "nationality", "birth_date", "birth_place", "work_start", "education",
                                "degree", "honorary_degree", "party", "party_joined_at")}
    person["name"] = fact(shown)
    person["work_start_action"] = "参加工作"
    return fill_defaults({"id": f"cadre-{pid}"[:96], "line": "cadre", "subject_id": pid, "title": f"{shown}简历",
                          "sources": [], "tags": ["seed"], "person": person, "current_roles": [], "careers": [],
                          "appointments": [], "event_date": None, "paragraphs": [], "company_tag": "", "conflicts": [],
                          "unknowns": ["一局动态新建的记录：只有动态里核实的职务，其余简历事实待取证"], "excluded": []})


def new_record(store: Store, name_zh: str, name_en: str, today: str) -> dict[str, Any]:
    pid = store.new_person_id(name_en or name_zh)
    return {"id": pid, "name_zh": name_zh, "name_en": name_en, "bureau": None, "also_in": [], "tier": "seed",
            "ids": {}, "resume": new_resume(pid, name_zh, name_en), "data_cutoff": today, "updated_at": today,
            "roles_checked_at": today, "events": [], "bureau_history": [], "origin": "一局动态新建"}


# ──────────────────────────────────────────────────────────────── 找人


_MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                "November", "December")


def _mentions_date(text: str | None, ymd: str) -> bool:
    """原文里有这一天的常见英文写法：August 21, 2026、Aug. 21, 2026、Sept. 8, 2026、21 August 2026、2026-08-21。"""
    y, m, d = ymd[:4], int(ymd[5:7]), int(ymd[8:10])
    name = _MONTH_NAMES[m - 1]
    forms = {f"{name} {d}, {y}", f"{name[:3]}. {d}, {y}", f"{name[:3]} {d}, {y}", f"{name[:4]}. {d}, {y}", f"{d} {name} {y}", ymd}
    return any(f in (text or "") for f in forms)


def sworn_in(event: dict[str, Any], today: str) -> bool:
    """参议院确认之后已宣誓到任：确认动态的生效日（宣誓日）不晚于今天，且有一级来源撑住这一天——
    摘录里写着这一天，或核查更正时为生效日附的一级来源（来源的 supports 有 effective_at）。"""
    when = (event.get("effective_at") or "")[:10]
    if event.get("kind") != "confirm" or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", when) or when > today:
        return False
    return any(sources.tier(s) == 1 and ("effective_at" in (s.get("supports") or []) or _mentions_date(s.get("excerpt"), when))
               for s in event.get("sources") or [])


def planned_date(event: dict[str, Any], today: str) -> str | None:
    """生效日在今天之后（宣布了、日子还没到）：返回生效日，否则 None。看 effective_at，没有就看新职务的起始日；
    只写到月的，当月不算将来。"""
    dates = [event.get("effective_at")] + [r.get("start") for r in event.get("new_roles") or []]
    future = sorted(d[:10] for d in dates if d and re.fullmatch(r"\d{4}-\d{2}(?:-\d{2})?", d[:10]) and d[:10] > today)
    return future[0] if future else None


def _plan_roles(rec: dict[str, Any], event: dict[str, Any], planned: str, mapping: dict[str, str],
                event_source_ids: list[str], result: dict[str, Any]) -> None:
    """生效日未到的职务记进人物记录的 planned_roles（不进简历事实包的现任和履历）；同一职务再报的替换旧的。"""
    plans = rec.setdefault("planned_roles", [])
    for action, key in (("任", "new_roles"), ("卸任", "ended_roles")):
        for role in event.get(key) or []:
            text = re.sub(r"^(?:候任|拟任)|(?:提名人选|当选人)$", "", (role.get("role") or "").strip())
            entry = {"action": action, "organization": (role.get("organization") or "").strip(), "role": text,
                     "organization_en": role.get("organization_en") or "", "role_en": role.get("role_en") or "",
                     "date": (role.get("start") if action == "任" else role.get("end")) or planned,
                     "sector": _sector_for(role), "source_ids": _map_ids(role.get("source_ids"), mapping, event_source_ids),
                     "event": event.get("id")}
            if not entry["organization"] and not entry["role"]:
                continue
            plans[:] = [p for p in plans if not (p["action"] == action and same_title(p, entry))]
            plans.append(entry)
            result["changes"].append(f"拟于 {entry['date']} {action}{entry['organization']}{entry['role']}（生效日之前不改现任）")


def _drop_planned(rec: dict[str, Any], event: dict[str, Any], result: dict[str, Any]) -> None:
    """到任、离任写回以后，对应的拟任条目删掉。"""
    plans = rec.get("planned_roles") or []
    done = [("任", r) for r in event.get("new_roles") or []] + [("卸任", r) for r in event.get("ended_roles") or []]
    keep = [p for p in plans if not any(p["action"] == action and (same_title(p, role) or same_role(p, role)) for action, role in done)]
    if len(keep) != len(plans):
        result["notes"].append(f"拟任已到期落实，删去 {len(plans) - len(keep)} 条拟任")
        rec["planned_roles"] = keep
    if not rec.get("planned_roles"):
        rec.pop("planned_roles", None)


def resolve_person(store: Store, event: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """返回 (人物记录或 None, 说明)。同名多人时返回 None。"""
    pid = event.get("person_id")
    if pid and store.person(pid):
        return store.person(pid), "按人物编号"
    person = event.get("person") or {}
    for kind, value in (person.get("ids") or {}).items():
        rec = store.find_by_external(kind, value)
        if rec:
            return rec, f"按{kind}编号"
    hits = store.find_by_name(person.get("name_en"), person.get("name_zh"))
    if len(hits) == 1:
        return hits[0], "按姓名"
    if len(hits) > 1:
        return None, "同名多人：" + "、".join(h["id"] for h in hits)
    return None, "库里没有此人"


# ──────────────────────────────────────────────────────────────── 写回


def apply_event(store: Store, event: dict[str, Any], today: str, *, save: bool = True) -> dict[str, Any]:
    """把一条已核实的动态写回人物记录。返回写回结果（同时写进 event['applied']）。"""
    result: dict[str, Any] = {"status": SKIPPED, "at": today, "changes": [], "notes": [],
                              "bureau_before": None, "bureau_after": None, "person_id": None}
    verification = (event.get("verification") or {}).get("status")
    if verification != "verified":
        result["notes"].append("动态未核实通过，不写回")
        event["applied"] = result
        return result
    kind = event.get("kind")
    if kind not in KINDS:
        result.update(status=FAILED)
        result["notes"].append(f"不认识的动态类别：{kind}")
        event["applied"] = result
        return result

    rec, how = resolve_person(store, event)
    person = event.get("person") or {}
    if rec is None and how.startswith("同名多人"):
        result.update(status=PENDING)
        result["notes"].append(how)
        event["applied"] = result
        return result
    created = False
    if rec is None:
        rec = portraits.attach(new_record(store, person.get("name_zh") or "", person.get("name_en") or "", today), store.root)
        if rec["name_zh"] and person.get("name_zh_source") != "glossary":
            rec["name_zh_pending"] = True  # 研究会话给的表外译名，待按译名表规则核定
        created = True
        result["changes"].append(f"新建人物记录 {rec['id']}（{how}）")
    original = copy.deepcopy(rec)
    item = rec["resume"]
    result["person_id"] = rec["id"]
    result["bureau_before"] = None if created else rec.get("bureau")
    checked_at = (event.get("verification") or {}).get("checked_at") or today
    mapping = merge_sources(item, event.get("sources") or [], checked_at)
    event_source_ids = list(dict.fromkeys(mapping.values()))
    when = _event_date(event)

    planned = planned_date(event, today)
    if planned is None and sworn_in(event, today):
        kind = "appoint"  # 确认之后有一级来源给出宣誓日，就是到任（2026-10-21 定）
        result["notes"].append(f"参议院确认后已于 {event['effective_at'][:10]} 到任（一级来源），按到任写回")
    if planned:
        # 宣布了、生效日还没到：现任和履历都不动，记进拟任，简历首段写“拟于某月某日任××”，到期由每月更新复核
        result["status"] = RECORDED
        _plan_roles(rec, event, planned, mapping, event_source_ids, result)
    if planned or kind in RECORD_ONLY_KINDS:
        if not planned:
            # 宣布任命但未到任：现任写“××提名人选”或“候任××”，履历新增同名一条（rules/cadre.md 第 8 节第 7 条）
            result["status"] = RECORDED
            _add_roles(item, event, event.get("announced_at") or when, mapping, event_source_ids, result)
        if not rec.get("bureau"):
            guess = bureaus.classify_role((event.get("new_roles") or [{}])[0])[0] if event.get("new_roles") else None
            rec["bureau"] = guess or event.get("default_bureau")
            if rec["bureau"]:
                result["changes"].append(f"所属局：按{KIND_LABEL[kind]}职务定为{bureaus.label(rec['bureau'])}")
    else:
        _end_roles(item, event, when, result)
        _add_roles(item, event, when, mapping, event_source_ids, result, kind)
        _drop_planned(rec, event, result)
        _decide_bureau(rec, event, result)
    _set_status(item, event, when, event_source_ids, bool(rec.get("planned_roles")))

    for kind_id, value in (person.get("ids") or {}).items():
        rec.setdefault("ids", {}).setdefault(kind_id, value)  # 国会 bioguide 等外部编号，下次对比数据集时认得出
    for p in ("name_zh", "name_en"):
        if not rec.get(p) and person.get(p):
            rec[p] = person[p]
            if p == "name_zh" and person.get("name_zh_source") != "glossary":
                rec["name_zh_pending"] = True  # 研究会话给的表外译名，待按译名表规则核定
    rec.pop("pending_roles", None)  # 旧版另存的待任职务，现在写在现任职务里
    if event["id"] not in rec.setdefault("events", []):
        rec["events"].append(event["id"])
    rec["updated_at"] = today
    if kind in ROLE_KINDS:
        rec["roles_checked_at"] = checked_at
    if result["status"] in (APPLIED, PENDING, UNDECIDED, RECORDED) and rec.get("bureau") != result["bureau_before"]:
        rec.setdefault("bureau_history", []).append({
            "from": result["bureau_before"], "to": rec.get("bureau"), "date": when, "event": event["id"],
            "reason": result.get("reason", ""),
        })
    result["bureau_after"] = rec.get("bureau")
    resume.annotate(rec)

    errors = validate_resume(item, today)
    if errors:
        result["status"] = FAILED
        result["notes"].append("schema 校验不通过：" + "；".join(errors[:5]))
        if not created:
            store._people[rec["id"]] = original  # 不落盘，内存也退回原样
        event["applied"] = result
        event["person_id"] = None if created else rec["id"]
        return result
    event["person_id"] = rec["id"]
    event["applied"] = result
    if save:
        store.save_person(rec)
        store.save_event(event)
    else:
        store._people[rec["id"]] = rec
    return result


def _end_roles(item: dict[str, Any], event: dict[str, Any], when: str, result: dict[str, Any]) -> None:
    current = item.setdefault("current_roles", [])
    removed: list[dict[str, Any]] = []
    for ended in event.get("ended_roles") or []:
        end = ended.get("end") or when
        if any(similarity(ended, r) >= 0.6 for r in removed):
            continue  # 已随议员席位一并移出
        if not current:
            result["notes"].append(f"原任“{bureaus.role_text(ended)}”：记录里本来就没有现任职务")
            continue
        scored = sorted(((0.0 if titles_conflict(ended, r) else 1.0 if same_role(r, ended) else similarity(ended, r), i)
                         for i, r in enumerate(current)), reverse=True)
        score, index = scored[0]
        if score < 0.6:
            result["notes"].append(f"原任“{bureaus.role_text(ended)}”在记录的现任职务里找不到对应项，未移出")
            continue
        targets = [index]
        if _is_congress_seat(current[index]):
            # 卸任议员席位时，所在院的委员会和领导职务一并结束
            targets += [i for i, r in enumerate(current) if i != index and bureaus.classify_role(r)[0] == bureaus.CONGRESS]
        for i in sorted(set(targets), reverse=True):
            role = current.pop(i)
            removed.append(role)
            result["changes"].append(f"现任职务移出：{bureaus.role_text(role)}")
            _close_career(item, role, end, result)


def _close_career(item: dict[str, Any], role: dict[str, Any], end: str, result: dict[str, Any]) -> None:
    careers = item.setdefault("careers", [])
    open_work = [(similarity(role, c), n) for n, c in enumerate(careers)
                 if c.get("kind") == "work" and (c.get("ongoing") or c.get("end") is None) and not titles_conflict(role, c)]
    open_work.sort(reverse=True)
    if role.get("sector") == "committee":
        return  # 委员会职务不单列履历条目
    if open_work and open_work[0][0] >= 0.6:
        career = careers[open_work[0][1]]
        career["end"] = fact(end[:10], role.get("source_ids"))
        career["ongoing"] = False
        result["changes"].append(f"履历止时间：{career['organization']}{career.get('role') or ''} 至 {end[:7]}")
        return
    if role.get("start"):
        careers.append({"kind": "work", "start": copy.deepcopy(role["start"]), "end": fact(end[:10], role.get("source_ids")),
                        "organization": role.get("organization") or "", "role": role.get("role") or None,
                        "program": None, "degree_awarded": None, "concurrent": bool(role.get("concurrent")),
                        "source_ids": list(role.get("source_ids") or []), "ongoing": False, "roles": list(role.get("roles") or []),
                        "role_category": None, "role_basis": None, "study_level": None, "full_time": None, "annotations": [],
                        "appointment_state": role.get("appointment_state"), "stable_identity": False})
        result["changes"].append(f"履历新增已结束条目：{bureaus.role_text(role)}")
    else:
        item.setdefault("unknowns", []).append(f"原任“{bureaus.role_text(role)}”起始时间未查到，履历未单列")
        result["notes"].append(f"原任“{bureaus.role_text(role)}”没有起始时间，履历未单列")


def _add_roles(item: dict[str, Any], event: dict[str, Any], when: str, mapping: dict[str, str],
               event_source_ids: list[str], result: dict[str, Any], kind: str | None = None) -> None:
    kind = kind or event.get("kind")
    current = item.setdefault("current_roles", [])
    careers = item.setdefault("careers", [])
    for new in event.get("new_roles") or []:
        state = STATE_FOR_KIND.get(kind, "正式")
        organization = (new.get("organization") or "").strip()
        role = (new.get("role") or "").strip()
        if state in bureaus.PENDING_STATES:
            role = re.sub(r"^(?:候任|拟任)|(?:提名人选|当选人)$", "", role)  # 状态词记在任职状态里，职务名不再带，免得写成“××提名人选提名人选”
        if not organization and not role:
            continue
        if state in bureaus.PENDING_STATES:
            # 提名、确认、宣布在前，此人后来已在同一机构到任：这个阶段已经过去，不再另写一条待任职务（中文写法不同也不会重复）
            since = (event.get("announced_at") or when or "")[:10]
            later = next((r for r in current if r.get("appointment_state") not in bureaus.PENDING_STATES
                          and _norm(r.get("organization") or "") == _norm(organization)
                          and ((r.get("start") or {}).get("value") or "") >= since), None) if since else None
            if later is not None:
                result["notes"].append(f"“{organization}{role}”：此人已于 {later['start']['value']} 在同一机构到任"
                                       f"（{_rtext(later)}），{KIND_LABEL.get(kind, '')}阶段不另写")
                continue
        start = new.get("start") or when
        source_ids = _map_ids(new.get("source_ids"), mapping, event_source_ids)
        sector = _sector_for(new)
        if state == "代理" and sector == "enterprise":
            state = "临时"  # 企业的 interim 写“临时××”
        entry = {"organization": organization, "role": role, "start": fact(start[:10], source_ids),
                 "concurrent": bool(new.get("concurrent")), "source_ids": source_ids, "roles": [], "sector": sector,
                 "appointment_state": state}
        probe = {**new, "role": role, "appointment_state": state}
        twin = next((r for r in current if not titles_conflict(r, probe)
                     and (same_role(r, probe) or same_title(r, probe) or similarity(entry, r) >= 0.95)), None)
        if twin is None:
            # 后写到的早期动态：履历里这段任职已经结束（例如 6 月出任代理、8 月已卸任），不再挂回现任
            closed = next((c for c in careers if c.get("kind") == "work" and not c.get("ongoing") and c.get("end")
                           and same_title(c, probe)
                           and ((c.get("end") or {}).get("value") or "") >= start[:10]), None)
            if closed is not None:
                result["notes"].append(f"“{organization}{role}”：履历里这段任职已结束（至 {closed['end']['value']}），不再列为现任")
                continue
        if twin is not None and twin.get("appointment_state") in bureaus.PENDING_STATES and state not in bureaus.PENDING_STATES:
            # 到任：提名、候任那一条改成正式职务，起始日期取到任日（rules/cadre.md 第 8 节第 7 条）
            twin.update(organization=organization or twin["organization"], role=role or twin.get("role", ""),
                        start=fact(start[:10], source_ids), appointment_state=state)
            twin["source_ids"] = list(dict.fromkeys([*(twin.get("source_ids") or []), *source_ids]))
            for career in careers:
                if career.get("appointment_state") in bureaus.PENDING_STATES and career.get("ongoing") and \
                        not titles_conflict(career, probe) and (same_role(career, probe) or similarity(career, twin) >= 0.6):
                    career.update(start=fact(start[:10], source_ids), appointment_state=state,
                                  organization=organization or career["organization"], role=role or career.get("role"))
            result["changes"].append(f"到任：{organization}{role}由{KIND_LABEL.get('nominate')}改为正式职务，起始 {start[:7]}")
            continue
        if twin is not None and twin.get("appointment_state") in bureaus.PENDING_STATES and state in bureaus.PENDING_STATES                 and STATE_RANK.get(state, 0) > STATE_RANK.get(twin.get("appointment_state"), 0):
            # 宣布之后又提名：“候任”改“提名人选”，进度只往前走
            result["changes"].append(f"任职状态：{organization}{role}由{twin['appointment_state']}改为{state}")
            twin["appointment_state"] = state
            twin["source_ids"] = list(dict.fromkeys([*(twin.get("source_ids") or []), *source_ids]))
            for career in careers:
                if career.get("appointment_state") in bureaus.PENDING_STATES and career.get("ongoing") and same_title(career, twin):
                    career["appointment_state"] = state
            continue
        if twin is not None:
            # 名册导入时没译的职务，换成动态里核实过的中文写法
            if _untranslated(twin.get("organization", "")) and organization and not _untranslated(organization):
                twin["organization"] = organization
            if _untranslated(_rtext(twin)) and role and not _untranslated(role):
                twin["role"], twin["roles"] = role, []
            twin["source_ids"] = list(dict.fromkeys([*(twin.get("source_ids") or []), *source_ids]))
            if not twin.get("start"):
                twin["start"] = fact(start[:10], source_ids)
            result["notes"].append(f"新任“{organization}{role}”已在现任职务里，补了来源和译名")
            continue
        # 新的主职排在最前，兼任排在后面
        current.insert(0 if not entry["concurrent"] else len(current), entry)
        result["changes"].append(f"现任职务新增：{organization}{role}")
        if entry["sector"] == "committee":
            continue
        careers.append({"kind": "work", "start": fact(start[:10], source_ids), "end": None, "organization": organization,
                        "role": role or None, "program": None, "degree_awarded": None, "concurrent": entry["concurrent"],
                        "source_ids": source_ids, "ongoing": True, "roles": [], "role_category": None, "role_basis": None,
                        "study_level": None, "full_time": None, "annotations": [], "appointment_state": state,
                        "stable_identity": False})
        result["changes"].append(f"履历新增：{start[:7]} {organization}{role}")


def _set_status(item: dict[str, Any], event: dict[str, Any], when: str, source_ids: list[str], planned: bool = False) -> None:
    """人物状态（schema cadrePerson.status）：逝世写卒年；已到任的有现职为在任；只有提名、候任一类的取其状态；
    都没有的为离任，只有拟任（生效日未到）的、此前也没有离任记录的为拟任。"""
    person = item["person"]
    if event.get("kind") == "death":
        person["death_date"] = fact(when[:10], source_ids)
        person["status"] = "逝世"
        return
    if person.get("status") == "逝世":
        return
    roles = item.get("current_roles") or []
    seated = [r for r in roles if r.get("appointment_state") not in bureaus.PENDING_STATES]
    if seated:
        person["status"] = "在任"
    elif roles:
        person["status"] = roles[0].get("appointment_state") or "在任"
    elif planned and person.get("status") in (None, "拟任"):
        person["status"] = "拟任"
    else:
        person["status"] = "离任"


def assign_bureau(store: Store, rec: dict[str, Any], today: str, reason: str) -> str | None:
    """所属局未定的人物按现行归局规则补定（归局规则改了以后用）：有现任职务按主要职务，没有的按最近一条动态的原职务。

    记一条 bureau_history；此人动态的写回结果里所属局空着的补上，“待定”改为“已写回”。定不了返回 None，什么都不改。
    """
    if rec.get("bureau"):
        return None
    bureau, why = bureaus.classify_person(rec["resume"].get("current_roles") or [])
    if bureau is None:
        for eid in reversed(rec.get("events") or []):
            ended = (store.event(eid) or {}).get("ended_roles") or []
            if ended:
                bureau, why = bureaus.classify_role(ended[0])
                why = f"原任“{bureaus.role_text(ended[0])}”：{why}"
                break
    if bureau is None:
        return None
    rec["bureau"] = bureau
    rec.setdefault("bureau_history", []).append({"from": None, "to": bureau, "date": today, "event": None,
                                                 "reason": f"{reason}：{why}"})
    rec["updated_at"] = today
    for eid in rec.get("events") or []:
        ev = store.event(eid)
        applied = (ev or {}).get("applied") or {}
        if not ev or applied.get("bureau_after"):
            continue
        applied["bureau_after"] = bureau
        if applied.get("status") == UNDECIDED:
            applied["status"] = APPLIED
        applied.setdefault("notes", []).append(f"{today} {reason}，所属局定为{bureaus.label(bureau)}")
        ev["bureaus"] = sorted({*(ev.get("bureaus") or []), bureau})
        store.save_event(ev)
    store.save_person(rec)
    return bureau


def _decide_bureau(rec: dict[str, Any], event: dict[str, Any], result: dict[str, Any]) -> None:
    item = rec["resume"]
    remaining = item.get("current_roles") or []
    before = rec.get("bureau")
    # 新建的记录没有原局：以原任职务所在的局作原局（待核、待定时人物先放在那里）
    ended = event.get("ended_roles") or []
    fallback = before or (bureaus.classify_role(ended[0])[0] if ended else None) or event.get("default_bureau")
    others = event.get("other_current_roles")
    appointment = event.get("new_appointment")
    if not remaining:
        checks = {
            "记录里已无现任职务": True,
            "已核实没有其他现职": isinstance(others, list) and not others,
            "已核实没有公布新任命": appointment is None and event.get("new_appointment_checked") is True,
        }
        failed = [k.replace("已核实", "未核实") for k, ok in checks.items() if not ok]
        if failed:
            result["status"] = PENDING
            result["reason"] = "转入七局前核对未通过：" + "、".join(failed)
            result["notes"].append(result["reason"] + "；暂不转局")
            rec["bureau"] = fallback
            return
        rec["bureau"] = bureaus.RETIRED
        result["status"] = APPLIED
        result["reason"] = "已无现任职务，核实没有其他现职和新任命"
        if before != bureaus.RETIRED:
            result["changes"].append(f"所属局：{bureaus.label(before)} → {bureaus.label(bureaus.RETIRED)}")
        return
    bureau, why = bureaus.classify_person(remaining)
    if isinstance(others, list) and not others and not (event.get("new_roles") or []):
        # 核实说已无其他现职，记录里却还有：多半是记录里的旧职务没更新，交人工
        result["notes"].append("核实称没有其他现职，记录里仍有：" + "、".join(bureaus.role_text(r) for r in remaining))
    primary = bureaus.primary_role(remaining) or {}
    if bureau is None and fallback == bureaus.AI and primary.get("sector") in ("enterprise", "advisory"):
        # 二局人物在企业之间调动（含离职创业），新机构不在名单里也留在二局
        bureau, why = bureaus.AI, f"主要职务“{bureaus.role_text(primary)}”：企业职务，原属二局"
    if bureau is None:
        result["status"] = UNDECIDED
        result["reason"] = f"新任职务不在各局范围（{why}），暂留原局"
        rec["bureau"] = fallback
        return
    rec["bureau"] = bureau
    result["status"] = APPLIED
    result["reason"] = why
    if before is None:
        result["changes"].append(f"所属局：定为{bureaus.label(bureau)}")
    elif before != bureau:
        verb = "重新任职" if before == bureaus.RETIRED else "调整归属"
        result["changes"].append(f"所属局（{verb}）：{bureaus.label(before)} → {bureaus.label(bureau)}")
