"""人事动态的公开内容：任免结果（2026-10-22 定，docs/PRODUCT.md 第三节“人事动态公开规则”）。

界面的人事动态（列表、单篇）和立即更新之后哪些动态能公开，都只调这里；标题、筛选、去重、合篇不在别处另写。
动态文件里的证据、核查和写回记录不动，这里只从中取已经落地的结果。

- 前台只说两件事：谁任什么职务，谁不再担任什么职务。代理写“代理”；辞职、免职、任期届满一律写“不再担任”。
  决定主体只写参议院确认（这一类本身就是参议院的决定），别的只写职务变化，不补造任命机关。
- 公开的只是落地的结果：到任、就任、宣誓、代理、调任生效、离任、逝世，以及参议院确认（开关）。
  提名、生效日在未来的宣布、媒体预测只留在后台。
- 一项结果是一人、一职、一个变化（任或不再担任；代理和正式分开算）。同一次任职的几次报道只出一项：
  参议院确认之后宣誓到任的并进确认那一项，只更新简历的起始日期；由代理转为正式算新的一项。
- 合篇只合同一天、同一决定主体的事：参议院同一天的确认合成“美国参议院确认一批人事任命”；同一天、同一机构的任免
  合成“某机构人事调整”，分任职、不再担任两段，没有的段不写。不同机构碰巧同一天换人不合。
- 正文用结构化字段套固定模板，不调 AI。发布时间用事件日期，精确到日（维护者 2026-10-22 定），只知道到月的不公开，不用采集日期；每项事实各带自己的来源。

只用标准库。
"""

from __future__ import annotations

import csv
import datetime as dt
import re
import urllib.parse
from collections import Counter, defaultdict
from functools import lru_cache
from typing import Any

from . import bureaus, personnel
from . import sources as tiers
from .headline import _main_roles, _role_text, person_name, post, posts
from .resume import mixed_spacing
from .store import REPO, Store, read_json

#: 维护者手定（2026-10-22）：名单外人员的任免也公开，正文只写姓名，不链接，不建人物页，搜不到。
INCLUDE_OFF_ROSTER = True
#: 维护者手定（2026-10-22）：参议院确认公开，同一天确认的合成一篇。关掉时确认只留后台，宣誓到任才公开。
PUBLISH_SENATE_CONFIRMATIONS = True

SENATE = "美国参议院"
#: 名单文件按导航顺序：白宫、商界、国会、国防、司法。
ROSTERS = ("whitehouse", "business", "congress", "defense", "justice")
#: 落地的类别；参议院确认另算（开关）。
LANDED = {"appoint": "到任", "transfer": "调任生效", "acting": "代理", "leave": "离任", "death": "逝世"}

# 不公开的原因
NOT_VERIFIED = "未通过核查"
NOMINATED = "提名"
ANNOUNCED_FUTURE = "宣布的生效日在未来"
ANNOUNCED = "宣布后未见到任"
FUTURE = "生效日在未来"
CONFIRM_HIDDEN = "参议院确认不公开"
NO_DATE = "事件日期不明"
MONTH_ONLY = "日期只到月"
NO_CHANGE = "没有职务变化"
NO_SOURCE = "没有来源"
CHINESE_COMPANY = "中国企业不收"
OFF_ROSTER = "名单外人员不公开"
UNKNOWN_KIND = "类别不明"

_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
_MONTH = re.compile(r"\d{4}-\d{2}")
#: 职务名本身带任期性质、不是代理的：临时首席执行官、当值轮值董事长。
_NOT_ACTING = re.compile(r"^临时|轮值|当值")
_VERBS = ("任", "代理", "不再担任", "不再代理")
_ACTING_VERBS = ("代理", "不再代理")
#: 合篇里的先后：名单里的人在前（按名单顺序），其余按职务高低。
_RANKS = (r"(?<!副)(?:总统|部长|国务卿|总监|局长|署长|司令|首席执行官|董事长|主席)", r"副部长|副国务卿|副局长", r"法官", r"大使|代表")


def _opt(value: bool | None, default: bool) -> bool:
    return default if value is None else bool(value)


def _today(today: str | None) -> str:
    return today or dt.date.today().isoformat()


# ─────────────────────────────── 名单


def roster_order(store: Store) -> dict[str, int]:
    """名单里的人 → 第一次出现的位置（栏目按导航顺序，组和成员按名单文件里的顺序）。"""
    folder = store.root / "rosters"
    files = [folder / f"{name}.json" for name in ROSTERS]
    if folder.is_dir():
        files += sorted(p for p in folder.glob("*.json") if p not in files)
    order: dict[str, int] = {}
    for path in files:
        if not path.exists():
            continue
        for group in read_json(path).get("groups") or []:
            for member in group.get("members") or []:
                if member.get("person"):
                    order.setdefault(member["person"], len(order))
    return order


# ─────────────────────────────── 能不能公开


def _roles(roles: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [r for r in roles or [] if isinstance(r, dict) and ((r.get("organization") or "").strip() or _role_text(r))]


def _event_when(event: dict[str, Any]) -> tuple[str, str] | None:
    """(事件日期, 精度)。有生效日用生效日，只写到月的精度到月；没有生效日的，宣布即生效，用动态日期。
    宣布日、生效日都没有的，动态日期可能只是采集日期，算日期不明。"""
    eff = (event.get("effective_at") or "")[:10]
    if _DAY.fullmatch(eff):
        return eff, "day"
    if _MONTH.fullmatch(eff):
        return eff, "month"
    date = (event.get("date") or "")[:10]
    if event.get("announced_at") and _DAY.fullmatch(date):
        return date, "day"
    return None


def _confirm_day(event: dict[str, Any]) -> str | None:
    """参议院确认的日子：宣布日（表决日）；已宣誓到任的确认动态，动态日期是宣誓日，不能拿来当确认日。"""
    for value in (event.get("announced_at"), event.get("date")):
        if _DAY.fullmatch((value or "")[:10]):
            return value[:10]
    return None


def _reports(event: dict[str, Any], today: str, confirmations: bool) -> tuple[list[dict[str, Any]], str]:
    """(能公开的报道, 原因)。报道是一条动态里的一次落地：多数动态一份；参议院确认后已宣誓到任的确认动态有确认、
    到任两份；写回时并进这条动态的参议院确认（verification.merged）另算一份确认，日期取它的表决日。"""
    kind = event.get("kind")
    if (event.get("verification") or {}).get("status") != "verified":
        return [], NOT_VERIFIED
    if any(bureaus.chinese_company(r) for r in _roles(event.get("new_roles")) + _roles(event.get("ended_roles"))):
        return [], CHINESE_COMPANY  # 维护者 2026-10-23 定：不收中国企业（bureaus.CHINESE_COMPANIES）
    if kind == "nominate":
        return [], NOMINATED
    if kind == "announce":
        return [], ANNOUNCED_FUTURE if personnel.planned_date(event, today) else ANNOUNCED
    if kind != "confirm" and kind not in LANDED:
        return [], UNKNOWN_KIND
    new = _roles(event.get("new_roles"))
    ended = [] if kind == "confirm" else _roles(event.get("ended_roles"))
    if not new and not ended:
        return [], NO_CHANGE
    found = [s for s in event.get("sources") or [] if s.get("url")]
    if not found:
        return [], NO_SOURCE
    base = {"event": event["id"], "person_id": event.get("person_id"), "person": event.get("person") or {},
            "sources": found, "own_sources": False, "new": new, "ended": ended}
    if kind == "confirm":
        out, day = [], _confirm_day(event)
        if confirmations and day and day <= today:
            out.append({**base, "id": event["id"], "how": "confirm", "date": day, "precision": "day"})
        if personnel.sworn_in(event, today):
            out.append({**base, "id": event["id"] + "-sworn", "how": "appoint", "date": event["effective_at"][:10], "precision": "day"})
        if not out:
            return [], CONFIRM_HIDDEN if not confirmations else FUTURE if day else NO_DATE
        return out, "参议院确认" if out[0]["how"] == "confirm" else "宣誓到任"
    if personnel.planned_date(event, today):
        return [], FUTURE
    when = _event_when(event)
    if not when:
        return [], NO_DATE
    if when[0] > today:
        return [], FUTURE
    if when[1] == "month":
        return [], MONTH_ONLY
    out = [{**base, "id": event["id"], "how": kind, "date": when[0], "precision": when[1]}]
    merged = (event.get("verification") or {}).get("merged") or []
    for entry in merged if confirmations and new else []:
        day = (entry.get("announced_at") or "")[:10]
        if entry.get("kind") == "confirm" and _DAY.fullmatch(day) and day <= today:
            ids = set(entry.get("sources") or [])
            out.append({**base, "id": entry.get("id") or event["id"] + "-confirm", "how": "confirm", "date": day,
                        "precision": "day", "ended": [], "sources": [s for s in found if s.get("id") in ids] or found,
                        "own_sources": True})
    return out, LANDED[kind]


def _check(event: dict[str, Any], today: str, confirmations: bool, roster: dict[str, int] | None) -> tuple[list[dict[str, Any]], str]:
    reports, why = _reports(event, today, confirmations)
    if reports and roster is not None and event.get("person_id") not in roster:
        return [], OFF_ROSTER
    return reports, why


def is_publishable(event: dict[str, Any], store: Store | None = None, today: str | None = None, *,
                   include_off_roster: bool | None = None, confirmations: bool | None = None) -> tuple[bool, str]:
    """一条动态能不能公开：(能否, 原因)。能公开的，原因写落地的类别（到任、调任生效、代理、离任、逝世、参议院确认、
    宣誓到任）；不能公开的写为什么（提名、宣布的生效日在未来、事件日期不明……）。

    能公开不等于单独成篇：同一次任职的重复报道在 results() 里并进先报的那一项。名单外人员开关关着时要给 store（读名单）。
    """
    roster = None if _opt(include_off_roster, INCLUDE_OFF_ROSTER) else roster_order(store or Store())
    reports, why = _check(event, _today(today), _opt(confirmations, PUBLISH_SENATE_CONFIRMATIONS), roster)
    return bool(reports), why


# ─────────────────────────────── 事实和去重


def _state(role: dict[str, Any], kind: str | None = None) -> str:
    """任职状态：职务名写着“代理”“临时”的照写；代理类的动态职务名没写的也按代理，不写成正式（轮值、当值是正式职务）。"""
    text = _role_text(role)
    if text.startswith("代理"):
        return "代理"
    if text.startswith("临时"):
        return "临时"
    if kind == "acting" and not _NOT_ACTING.search(text):
        return "代理"
    return "正式"


def _post_key(role: dict[str, Any]) -> str:
    """比对用的职务写法：机构加职务（大使馆的大使写成驻某国大使），去掉代理、特命全权。"""
    return personnel._norm(post(role, strip_acting=True).replace("特命全权", ""))


def _en_norm(text: str) -> str:
    """英文写法比对用：United States、U.S.、the、of 一类不算，代理、临时不算。"""
    text = re.sub(r"\bu\.\s?s\.(?:\s?a\.)?", " ", text.lower())
    text = re.sub(r"\b(?:united states(?: of america)?|usa?|the|of|for|to|and|acting|interim)\b", " ", text)
    return re.sub(r"[^a-z0-9]", "", text)


def _post_key_en(role: dict[str, Any]) -> str:
    org, title = _en_norm(role.get("organization_en") or ""), _en_norm(role.get("role_en") or "")
    return f"{org}|{title}" if org and title else ""


def _same_post(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """同一职务：中文写法相同；或英文写法相同、中文职务名又不明确冲突（副部长和常务副部长、法官和首席法官算冲突）。
    副职、首席、不同席位、不同驻在国的写法不同，不会并到一起。"""
    if a["zh"] and a["zh"] == b["zh"]:
        return True
    return bool(a["en"] and a["en"] == b["en"]) and not personnel.titles_conflict(a["role"], b["role"])


def _fact_sources(role: dict[str, Any], report: dict[str, Any]) -> list[dict[str, Any]]:
    """这项事实的来源：职务自己标的来源（source_ids），没标的用整条动态的；一级来源排前面。"""
    found = report["sources"]
    if not report["own_sources"]:
        ids = set(role.get("source_ids") or [])
        found = [s for s in found if s.get("id") in ids] or found
    return sorted(found, key=lambda s: tiers.tier(s) != 1)


def _facts(report: dict[str, Any], store: Store, roster: dict[str, int]) -> list[dict[str, Any]]:
    """一份报道里的事实：新职务一项一条（任），原职务一项一条（不再担任）。同一份报道里由代理转为正式的，
    “任正式职务”已经说明，不再另出“不再代理”。"""
    name, _ = person_name(report, store)
    pid = report.get("person_id")
    common = {"report": report["id"], "event": report["event"], "person_id": pid, "person": pid or "name:" + name,
              "name": name, "in_roster": pid in roster, "how": report["how"], "date": report["date"],
              "precision": report["precision"]}
    out = []
    for side, key, kind in (("任", "new", report["how"]), ("免", "ended", None)):
        for i, role in enumerate(report[key], 1):
            out.append({**common, "id": f"{report['id']}#{'n' if side == '任' else 'e'}{i}", "side": side,
                        "state": _state(role, kind), "role": role, "zh": _post_key(role), "en": _post_key_en(role),
                        "sources": _fact_sources(role, report)})
    formal = [f for f in out if f["side"] == "任" and f["state"] == "正式"]
    return [f for f in out if not (f["side"] == "免" and f["state"] != "正式" and any(_same_post(f, g) for g in formal))]


def _sort_date(item: dict[str, Any]) -> str:
    return item["date"] if item["precision"] == "day" else item["date"] + "-00"


def _components(facts: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """按“同一职务”连通分组（并查集）。"""
    parent = list(range(len(facts)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(facts)):
        for j in range(i + 1, len(facts)):
            if _same_post(facts[i], facts[j]):
                parent[find(i)] = find(j)
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for i, fact in enumerate(facts):
        groups[find(i)].append(fact)
    return list(groups.values())


def _runs(facts: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """按日期排好的同一职务的事实切成几次任免：同一边连着的是同一次的几次报道，中间隔着反向变化的另算一次。"""
    runs: list[list[dict[str, Any]]] = []
    open_runs: dict[str, list[dict[str, Any]]] = {}
    for fact in facts:
        run = open_runs.get(fact["side"])
        if run is None:
            run = open_runs[fact["side"]] = []
            runs.append(run)
        run.append(fact)
        open_runs.pop("免" if fact["side"] == "任" else "任", None)
    return runs


def _pick(run: list[dict[str, Any]]) -> dict[str, Any]:
    """一次任免的几次报道里留哪一项：有参议院确认的留最早的确认（之后的宣誓到任只更新简历起始日期），
    否则留日期精确到日的最早一次。"""
    pool = [f for f in run if f["how"] == "confirm"] or [f for f in run if f["precision"] == "day"] or run
    return min(pool, key=lambda f: (_sort_date(f), f["id"]))


def _dedupe(facts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """同一人、同一任职状态、同一职务的事实按日期排开切成几次任免，每次只留一项。返回 (留下的, {并掉的: 留下的})。"""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        groups[(fact["person"], fact["state"])].append(fact)
    kept, folded = [], {}
    for group in groups.values():
        for same in _components(group):
            for run in _runs(sorted(same, key=lambda f: (_sort_date(f), f["id"]))):
                rep = _pick(run)
                rep["duplicates"] = [f for f in run if f is not rep]
                folded.update({f["id"]: rep["id"] for f in rep["duplicates"]})
                kept.append(rep)
    return kept, folded


def _build(store: Store, today: str | None, include_off_roster: bool | None, confirmations: bool | None) -> dict[str, Any]:
    today = _today(today)
    confirmations = _opt(confirmations, PUBLISH_SENATE_CONFIRMATIONS)
    roster = roster_order(store)
    gate = None if _opt(include_off_roster, INCLUDE_OFF_ROSTER) else roster
    reasons: dict[str, str] = {}
    publishable, facts = [], []
    for event in sorted(store.events(), key=lambda e: e["id"]):
        reports, why = _check(event, today, confirmations, gate)
        if not reports:
            reasons[event["id"]] = why
            continue
        publishable.append(event["id"])
        for report in reports:
            facts.extend(_facts(report, store, roster))
    for i, fact in enumerate(facts):
        fact["seq"] = i
    kept, folded = _dedupe(facts)
    kept.sort(key=lambda f: (_sort_date(f), f["id"]), reverse=True)
    return {"roster": roster, "reasons": reasons, "publishable": publishable, "facts": facts, "results": kept, "folded": folded}


# ─────────────────────────────── 写法


def _verb(fact: dict[str, Any]) -> str:
    acting = fact["state"] == "代理"
    if fact["side"] == "任":
        return "代理" if acting else "任"
    return "不再代理" if acting else "不再担任"


def _when(item: dict[str, Any]) -> str:
    """正文里的日期：9月1日；只知道到月的写 9月。年份看发布时间。"""
    date = item["date"]
    return f"{int(date[5:7])}月" + ("" if item["precision"] == "month" else f"{int(date[8:10])}日")


def _date_cn(date: str, precision: str) -> str:
    return f"{date[:4]}年{int(date[5:7])}月" + ("" if precision == "month" else f"{int(date[8:10])}日")


def _post_list(roles: list[dict[str, Any]], strip_acting: bool = False, last_org: str | None = None) -> tuple[str, str | None]:
    """正文里几项职务连写，全部写出（标题才省略）：紧接着同一机构的只写职务。返回 (文字, 最后写到的机构)。"""
    parts = []
    for role in roles:
        org = (role.get("organization") or "").strip()
        text = _role_text(role)
        if strip_acting and text.startswith("代理"):
            text = text[2:]
        parts.append(text if org and org == last_org and text else post(role, strip_acting))
        last_org = org or last_org
    return "、".join(parts), last_org


def _segments(facts: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    """一个人的几项事实按动词归段，次序是任、代理、不再担任、不再代理。"""
    buckets: dict[str, list[dict[str, Any]]] = {verb: [] for verb in _VERBS}
    for fact in facts:
        buckets[_verb(fact)].append(fact["role"])
    return [(verb, buckets[verb]) for verb in _VERBS if buckets[verb]]


def _phrases(segments: list[tuple[str, list[dict[str, Any]]]], last_org: str | None = None) -> tuple[str, str | None]:
    parts = []
    for verb, roles in segments:
        text, last_org = _post_list(roles, verb in _ACTING_VERBS, last_org)
        parts.append(verb + text)
    return "，".join(parts), last_org


def _join(items: list[tuple[str, list[tuple[str, list[dict[str, Any]]]]]]) -> str:
    """合篇一段里的几个人：职务完全相同的挨着就合写（甲、乙任某职），其余逐人写，逗号隔开。"""
    merged: list[tuple[list[str], list[tuple[str, list[dict[str, Any]]]], tuple]] = []
    for name, segments in items:
        key = tuple((verb, _post_list(roles, verb in _ACTING_VERBS)[0]) for verb, roles in segments)
        if merged and merged[-1][2] == key:
            merged[-1][0].append(name)
        else:
            merged.append(([name], segments, key))
    parts, last_org = [], None
    for names, segments, _ in merged:
        text, last_org = _phrases(segments, last_org)
        parts.append("、".join(names) + text)
    return "，".join(parts)


def _fact_text(fact: dict[str, Any]) -> str:
    """一项事实的一句短话（来源按事实列时用）。"""
    role = fact["role"]
    if fact["how"] == "confirm":
        return f"{SENATE}确认{fact['name']}为{post(role)}"
    if fact["how"] == "death":
        return f"{post(role)}{fact['name']}逝世"
    verb = _verb(fact)
    return f"{fact['name']}{verb}{post(role, verb in _ACTING_VERBS)}"


# ─────────────────────────────── 来源的发布机构

ORG_GLOSSARY = REPO / "shared" / "glossary" / "译名表_v1.4_org.csv"
PERSON_GLOSSARY = REPO / "shared" / "glossary" / "译名表_v1.4_person.csv"

#: 来源一栏写发布机构的中文名：常见的列在这里，其余查译名表，查不到照原文。
PUBLISHERS = {
    "united states senate": SENATE, "u.s. senate": SENATE, "美国参议院": SENATE,
    "the white house": "美国白宫", "white house": "美国白宫", "白宫": "美国白宫", "美国白宫": "美国白宫",
    "美国总统声明": "美国白宫", "presidential statement archive": "美国白宫",
    "associated press": "美联社", "ap": "美联社", "reuters": "路透社", "cnn": "美国有线电视新闻网",
    "npr": "美国全国公共广播电台", "abc news": "美国广播公司新闻网", "cbs news": "哥伦比亚广播公司新闻网",
    "deutsche welle": "德国之声", "anadolu agency": "阿纳多卢通讯社", "the hill": "《国会山报》", "usa today": "《今日美国报》",
    "stars and stripes": "《星条旗报》", "星条旗报": "《星条旗报》", "纽约邮报": "《纽约邮报》", "military times": "《军事时报》",
    "the jerusalem post": "《耶路撒冷邮报》", "wired": "《连线》杂志", "air & space forces magazine": "《空天军杂志》",
    "usni news": "美国海军学会新闻网", "federal news network": "联邦新闻网", "fox news media": "福克斯新闻传媒",
    "u.s. department of the treasury": "美国财政部", "u.s. department of health and human services": "美国卫生与公众服务部",
    "national capital planning commission": "美国国家首都规划委员会",
    "u.s. consumer product safety commission": "美国消费品安全委员会",
    "national credit union administration": "美国全国信用合作社管理局", "farm credit administration": "美国农业信贷管理局",
    "national labor relations board": "美国全国劳资关系委员会", "u.s. office of special counsel": "美国特别检察官办公室",
    "u.s. court of appeals for the federal circuit": "美国联邦巡回上诉法院",
    "national archives and records administration": "美国国家档案和记录管理局",
    "public company accounting oversight board": "美国公众公司会计监督委员会",
    "national endowment for the humanities": "美国国家人文基金会",
    "national endowment for the humanities newsletter": "美国国家人文基金会",
    "u.s. house committee on the budget": "美国众议院预算委员会", "south carolina state register": "南卡罗来纳州公报",
    "apple": "苹果公司", "amd": "超威半导体公司（AMD）", "google": "谷歌公司", "adobe": "Adobe公司", "amazon": "亚马逊公司",
    "broadcom": "博通公司", "openai": "开放人工智能研究中心（OpenAI）", "scale ai": "Scale AI公司", "mongodb": "MongoDB公司",
    "meta": "元宇宙平台公司（Meta）", "huawei": "华为技术有限公司", "华为": "华为技术有限公司", "boston dynamics": "波士顿动力公司",
    "discovery loop": "探索循环公司", "sidley austin": "盛德律师事务所", "columbia business school": "哥伦比亚大学商学院",
}


@lru_cache(maxsize=1)
def _glossary_orgs() -> dict[str, str]:
    out: dict[str, str] = {}
    if ORG_GLOSSARY.exists():
        with ORG_GLOSSARY.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                name, key = (row.get("简历写法") or "").strip(), (row.get("english_name") or "").strip().lower()
                if name and key:
                    out.setdefault(key, name)
    return out


@lru_cache(maxsize=1)
def _glossary_people() -> dict[str, str]:
    """译名表的人名：英文名 → 中文名；带中间名缩写的另记一个不带的（Mark R. Warner 也认 Mark Warner）。"""
    out: dict[str, str] = {}
    if PERSON_GLOSSARY.exists():
        with PERSON_GLOSSARY.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                name, key = (row.get("新华社译名") or "").strip(), (row.get("english_name") or "").strip().lower()
                if name and key:
                    out.setdefault(key, name)
                    out.setdefault(re.sub(r"\s+[a-z]\.(?=\s)", "", key), name)
    return out


def publisher(source: dict[str, Any]) -> str:
    """来源的发布机构，写中文：“路透社（Investing.com转载）”写“路透社”，“Amazon / SEC”写“亚马逊公司”，
    参议员办公室写“美国参议员某某办公室”。没写发布机构的写网站域名。"""
    raw = re.sub(r"（[^）]*转载[^）]*）|\([^)]*转载[^)]*\)", "", (source.get("publisher") or "").strip())
    first = re.split(r"\s*(?:；|;|／| / |/)\s*", raw)[0].strip()
    if not first:
        return re.sub(r"^www\.", "", urllib.parse.urlsplit(source.get("url") or "").hostname or "")
    key = first.lower()
    m = re.match(r"(?:office of )?u\.s\. senator (.+)$", key)
    if m and m.group(1) in _glossary_people():
        return f"美国参议员{_glossary_people()[m.group(1)]}办公室"
    return PUBLISHERS.get(key) or _glossary_orgs().get(key) or first


def _source(source: dict[str, Any]) -> dict[str, Any]:
    return {"publisher": publisher(source), "title": source.get("title") or "", "url": source.get("url") or "",
            "published_at": source.get("published_at")}


# ─────────────────────────────── 结果、合篇


def _result(fact: dict[str, Any]) -> dict[str, Any]:
    role = fact["role"]
    return {
        "id": fact["id"], "person_id": fact["person_id"], "name": fact["name"], "in_roster": fact["in_roster"],
        "change": "逝世" if fact["how"] == "death" else _verb(fact), "state": fact["state"],
        "organization": (role.get("organization") or "").strip(), "role": _role_text(role), "post": mixed_spacing(post(role)),
        "how": fact["how"], "decider": SENATE if fact["how"] == "confirm" else "",
        "date": fact["date"], "precision": fact["precision"], "text": mixed_spacing(_fact_text(fact)),
        "event": fact["event"], "report": fact["report"], "sources": [_source(s) for s in fact["sources"]],
        "duplicates": [{"id": d["id"], "event": d["event"], "how": d["how"], "date": d["date"]} for d in fact["duplicates"]],
    }


def _clauses(kept: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """留下的事实按报道归拢：一份报道里一个人的几项任免是一句话。"""
    out: dict[str, dict[str, Any]] = {}
    for fact in sorted(kept, key=lambda f: f["seq"]):
        clause = out.setdefault(fact["report"], {k: fact[k] for k in (
            "report", "event", "person", "person_id", "name", "in_roster", "how", "date", "precision")} | {"facts": []})
        clause["facts"].append(fact)
    return list(out.values())


def _orgs(clause: dict[str, Any]) -> dict[str, str]:
    """一句话的决定主体所在机构：有任职的看任职的机构，只有离任的看离任的机构（委员会职务不算）。规范写法 → 原写法。"""
    facts = [f for f in clause["facts"] if f["side"] == "任"] or clause["facts"]
    out: dict[str, str] = {}
    for role in _main_roles([f["role"] for f in facts]):
        org = (role.get("organization") or "").strip()
        if org:
            out.setdefault(personnel._norm(org), org)
    return out


def _org_groups(clauses: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """同一天的几句话，机构有交集的连成一篇。"""
    groups: list[tuple[set[str], list[dict[str, Any]]]] = []
    for clause in clauses:
        keys = set(_orgs(clause)) or {"#" + clause["report"]}
        hit = [g for g in groups if g[0] & keys]
        for g in hit:
            keys |= g[0]
        members = [c for g in hit for c in g[1]] + [clause]
        groups = [g for g in groups if not any(g is h for h in hit)] + [(keys, members)]
    return [members for _, members in groups]


def _groups(clauses: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    senate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    same_day: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    out: list[tuple[str, list[dict[str, Any]]]] = []
    for clause in clauses:
        if clause["how"] == "confirm":
            senate[clause["date"]].append(clause)
        elif clause["how"] == "death":
            out.append(("one", [clause]))
        else:
            same_day[(clause["date"], clause["precision"])].append(clause)
    out += [("senate", group) for group in senate.values()]
    out += [("org", group) for clauses_of_day in same_day.values() for group in _org_groups(clauses_of_day)]
    return out


def _rank(clause: dict[str, Any], roster: dict[str, int]) -> tuple:
    if clause["person_id"] in roster:
        return 0, roster[clause["person_id"]], ""
    text = "、".join(post(f["role"]) for f in clause["facts"])
    for i, rx in enumerate(_RANKS, 1):
        if re.search(rx, text):
            return i, 0, clause["name"]
    return len(_RANKS) + 1, 0, clause["name"]


def _by_person(clauses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一篇里同一个人的几句并成一句。"""
    out: dict[str, dict[str, Any]] = {}
    for clause in clauses:
        if clause["person"] in out:
            out[clause["person"]]["facts"] = sorted(out[clause["person"]]["facts"] + clause["facts"], key=lambda f: f["seq"])
        else:
            out[clause["person"]] = dict(clause)
    return list(out.values())


def _title_org(clauses: list[dict[str, Any]]) -> str:
    """“某机构人事调整”的机构：几句话里出现最多的机构，一样多取先出现的。"""
    count: Counter[str] = Counter()
    shown: dict[str, str] = {}
    for clause in clauses:
        for key, org in _orgs(clause).items():
            count[key] += 1
            shown.setdefault(key, org)
    return shown[max(shown, key=lambda k: (count[k], -list(shown).index(k)))] if shown else ""


def _single(clause: dict[str, Any]) -> tuple[str, list[str]]:
    name, facts, when = clause["name"], clause["facts"], _when(clause)
    roles = [f["role"] for f in facts]
    if clause["how"] == "confirm":
        return f"{SENATE}确认{name}为{posts(roles)}", [f"{SENATE}{when}确认{name}为{_post_list(roles)[0]}。"]
    if clause["how"] == "death":
        return f"{posts(_main_roles(roles)[:1])}{name}逝世", [f"{_post_list(roles)[0]}{name}{when}逝世。"]
    segments = _segments(facts)
    verb, first = segments[0]
    text, _ = _phrases(segments)
    title = f"{name}{verb}{posts(first, strip_acting=verb in _ACTING_VERBS)}"
    return title, [f"{name}{when}{'起' if verb == '代理' else ''}{text}。"]


def _merged(kind: str, clauses: list[dict[str, Any]]) -> tuple[str, list[str]]:
    when = _when(clauses[0])
    if kind == "senate":
        items = [(c["name"], [("为", [f["role"] for f in c["facts"]])]) for c in clauses]
        return f"{SENATE}确认一批人事任命", [f"{SENATE}{when}确认{_join(items)}。"]
    took, left = [], []
    for clause in clauses:
        segments = _segments(clause["facts"])
        if [s for s in segments if s[0] in ("任", "代理")]:
            took.append((clause["name"], [s for s in segments if s[0] in ("任", "代理")]))
        if [s for s in segments if s[0] in ("不再担任", "不再代理")]:
            left.append((clause["name"], [s for s in segments if s[0] in ("不再担任", "不再代理")]))
    paragraphs, lead = [], f"{when}，"
    for items in (took, left):  # 先任职，后不再担任；没有的段不写
        if items:
            paragraphs.append(f"{lead}{_join(items)}。")
            lead = ""
    return f"{_title_org(clauses)}人事调整", paragraphs


def _article(kind: str, clauses: list[dict[str, Any]], roster: dict[str, int]) -> dict[str, Any]:
    clauses = sorted(_by_person(clauses), key=lambda c: _rank(c, roster))
    head = clauses[0]
    if len(clauses) == 1:
        title, paragraphs = _single(head)
        aid = head["report"]
    else:
        title, paragraphs = _merged(kind, clauses)
        aid = f"{head['date']}-senate" if kind == "senate" else "group-" + min(c["report"] for c in clauses)
    facts = [f for c in clauses for f in c["facts"]]
    return {
        "id": aid, "title": mixed_spacing(title), "date": head["date"], "date_cn": _date_cn(head["date"], head["precision"]),
        "paragraphs": [mixed_spacing(p) for p in paragraphs],
        "sources": [{"result": f["id"], "fact": mixed_spacing(_fact_text(f)), "sources": [_source(s) for s in f["sources"]]}
                    for f in facts],
        "source_line": "、".join(dict.fromkeys(publisher(f["sources"][0]) for f in facts if f["sources"])),
        "people": [{"person_id": c["person_id"], "name": c["name"], "in_roster": c["in_roster"]} for c in clauses],
        "results": [f["id"] for f in facts], "events": list(dict.fromkeys(f["event"] for f in facts)),
        "_sort": _sort_date(head),
    }


def _articles(built: dict[str, Any]) -> list[dict[str, Any]]:
    out = [_article(kind, group, built["roster"]) for kind, group in _groups(_clauses(built["results"]))]
    out.sort(key=lambda a: (a["_sort"], a["id"]), reverse=True)
    for art in out:
        del art["_sort"]
    return out


# ─────────────────────────────── 对外接口


def results(store: Store, today: str | None = None, *, include_off_roster: bool | None = None,
            confirmations: bool | None = None) -> list[dict[str, Any]]:
    """去重后的任免结果，新的在前。一项是一人、一职、一个变化：change 是任、代理、不再担任、不再代理或逝世；
    decider 只有参议院确认才写；duplicates 是并进这一项的其他报道（如确认之后的宣誓到任）；sources 是这项事实自己的来源。"""
    built = _build(store, today, include_off_roster, confirmations)
    return [_result(f) for f in built["results"]]


def articles(store: Store, today: str | None = None, *, include_off_roster: bool | None = None,
             confirmations: bool | None = None) -> list[dict[str, Any]]:
    """人事动态的稿件，新的在前：{id, title, date（事件日期，到日）, date_cn, paragraphs, sources（按事实：
    [{result, fact, sources}]）, source_line（来源一栏）, people（[{person_id, name, in_roster}]，名单外的只写姓名）,
    results, events}。"""
    return _articles(_build(store, today, include_off_roster, confirmations))


def article(store: Store, aid: str, today: str | None = None, **switches: bool | None) -> dict[str, Any] | None:
    return next((a for a in articles(store, today, **switches) if a["id"] == aid), None)


def stats(store: Store, today: str | None = None, *, include_off_roster: bool | None = None,
          confirmations: bool | None = None) -> dict[str, Any]:
    """按现有数据算的数：动态总数、能公开的动态、不能公开的按原因、事实条数、去重后的结果、并掉的重复报道、
    整条都并进别处的动态、稿件篇数（其中合篇）。"""
    built = _build(store, today, include_off_roster, confirmations)
    arts = _articles(built)
    kept_events = {f["event"] for f in built["results"]}
    return {
        "events": len(store.events()),
        "publishable_events": len(built["publishable"]),
        "unpublished": dict(Counter(built["reasons"].values()).most_common()),
        "facts": len(built["facts"]),
        "results": len(built["results"]),
        "duplicates": len(built["folded"]),
        "folded_events": sum(1 for eid in built["publishable"] if eid not in kept_events),
        "articles": len(arts),
        "merged_articles": sum(1 for a in arts if len(a["people"]) > 1),
    }
