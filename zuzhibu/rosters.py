"""五栏名单：data/rosters/<栏目>.json（docs/PRODUCT.md 第三节）。界面照名单里的分组和顺序渲染；名单之外的人不在界面出现，
搜索只搜名单里的人，名单外的人没有人物页。

    {"id": "whitehouse", "title": "白宫", "groups": [
      {"title": "总统", "style": "lead", "members": [{"person": "<人物编号>", "post": "美国总统"}]},
      {"title": "内阁成员", "note": "按英文姓氏字母排列", "style": "grid", "members": [...]}]}

style：lead 单人大像、row 一排、grid 网格、list 只列姓名和职务。post 是像下写的职务；short_post 是只供界面显示的短称（可选）。
国会成员另带 party、state、district，供筛选。

商界按市值分层：名单里写 "market_cap": {"tiers": [{"title": "市值万亿美元以上", "min": 1000000000000},
{"title": "市值万亿美元以下", "min": 0}], "unlisted": "未上市公司"}，市值在同目录的 market-caps.json（bureaus/dispatch/marketcap.py
查了写进去，“立即更新”时刷新）；界面按市值把人排进各层、层内从大到小，市值文件变了名单页跟着变。只用标准库。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import portraits, resume
from .store import Store, data_dir

SECTIONS = (("whitehouse", "白宫"), ("business", "商界"), ("congress", "国会"), ("defense", "国防"), ("justice", "司法"))
STYLES = ("lead", "row", "grid", "list")
FILTER_KEYS = ("party", "state", "district")
CAPS_FILE = "market-caps.json"


def folder(root: str | Path | None = None) -> Path:
    return data_dir(root) / "rosters"


def stamp(root: str | Path | None = None) -> tuple:
    """名单文件和市值文件的指纹（文件名、修改时间、大小），换了就重读。"""
    base = folder(root)
    out = []
    for name in [f"{sid}.json" for sid, _title in SECTIONS] + [CAPS_FILE]:
        try:
            st = (base / name).stat()
            out.append((name, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((name, 0, 0))
    return tuple(out)


def _usd(row: dict[str, Any]) -> float:
    usd = row.get("usd")
    return float(usd) if isinstance(usd, (int, float)) and not isinstance(usd, bool) and usd > 0 else -1.0


def market_caps(path: Path) -> dict[str, dict[str, Any]]:
    """市值文件：{"as_of": 交易日, "companies": [{person, company, ticker, usd, date, url, excerpt}]}。返回 人物编号 → 那一条；
    没有或读不了给空。usd 为空的是上市公司但还没查到数。一人兼几家上市公司（几条）的，取市值最大的那条，没数的不顶替有数的。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for c in (data.get("companies") if isinstance(data, dict) else None) or []:
        if isinstance(c, dict) and c.get("person") and (c["person"] not in out or _usd(c) > _usd(out[c["person"]])):
            out[c["person"]] = c
    return out


def load(root: str | Path | None = None) -> dict[str, dict[str, Any]]:
    """读五份名单；缺的、读不了的栏目给空名单。"""
    base = folder(root)
    out = {}
    for sid, title in SECTIONS:
        try:
            data = json.loads((base / f"{sid}.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        out[sid] = {"id": sid, "title": data.get("title") or title, "groups": [g for g in data.get("groups") or [] if isinstance(g, dict)]}
        if isinstance(data.get("market_cap"), dict):
            out[sid]["market_cap"] = data["market_cap"]
            out[sid]["caps"] = market_caps(base / CAPS_FILE)
    return out


def _members(group: dict[str, Any]) -> list[dict[str, Any]]:
    return [m for m in group.get("members") or [] if isinstance(m, dict) and m.get("person")]


def listed(rosters: dict[str, dict[str, Any]]) -> dict[str, tuple[str, str]]:
    """名单里的人：人物编号 → (首次出现的栏目, 那里的职务)，按栏目和名单顺序。"""
    out: dict[str, tuple[str, str]] = {}
    for sid, _title in SECTIONS:
        for group in rosters.get(sid, {}).get("groups") or []:
            for m in _members(group):
                out.setdefault(m["person"], (sid, m.get("post") or ""))
    return out


def sections_of(rosters: dict[str, dict[str, Any]], pid: str) -> list[str]:
    """这个人在哪几栏的名单里（按栏目顺序）。"""
    return [sid for sid, _t in SECTIONS
            if any(m["person"] == pid for g in rosters.get(sid, {}).get("groups") or [] for m in _members(g))]


def card(store: Store, member: dict[str, Any]) -> dict[str, Any] | None:
    """名单成员 → 界面卡片：姓名、职务（有短称用短称）、标准像，国会成员带筛选用的党派、州、选区。
    name_en 只供国会页的搜索框按英文名找人，不显示。库里没有的人返回 None。"""
    rec = store.person(member.get("person") or "")
    if not rec:
        return None
    out = {"id": rec["id"], "name": rec.get("name_zh") or rec.get("name_en") or rec["id"],
           "post": member.get("short_post") or member.get("post") or "", "portrait": portraits.display_src(rec)}
    if rec.get("name_en"):
        out["name_en"] = rec["name_en"]
    for key in FILTER_KEYS:
        if member.get(key) is not None:
            out[key] = member[key]
    return out


def ranked(roster: dict[str, Any]) -> list[dict[str, Any]]:
    """按市值分层排序后的分组：市值文件里有数的，从大到小排进第一个够得着的层；市值文件里有、这次还没有数的，留在名单文件里原来那一组、
    排在有数的后面；市值文件里没有的（未上市）进 unlisted 那一组，照名单顺序。各层的版式和说明沿用名单文件里同标题的组。"""
    spec, caps = roster["market_cap"], roster.get("caps") or {}
    tiers = [t for t in spec.get("tiers") or [] if isinstance(t, dict) and t.get("title")]
    if not tiers:
        return roster.get("groups") or []
    file_groups = {g.get("title") or "": g for g in roster.get("groups") or []}
    home: dict[str, str] = {}
    members: list[dict[str, Any]] = []
    for group in roster.get("groups") or []:
        for m in _members(group):
            if m["person"] not in home:
                home[m["person"]] = group.get("title") or ""
                members.append(m)
    sized: dict[str, list] = {t["title"]: [] for t in tiers}
    unsized: dict[str, list] = {t["title"]: [] for t in tiers}
    unlisted = []
    for i, m in enumerate(members):
        cap = caps.get(m["person"])
        if cap is None:
            unlisted.append(m)
            continue
        usd = cap.get("usd")
        if not isinstance(usd, (int, float)) or isinstance(usd, bool) or usd <= 0:
            unsized[home[m["person"]] if home[m["person"]] in unsized else tiers[-1]["title"]].append(m)
            continue
        tier = next((t for t in tiers if usd >= float(t.get("min") or 0)), tiers[-1])
        sized[tier["title"]].append((-float(usd), i, m))

    def shaped(title: str, ms: list[dict[str, Any]]) -> dict[str, Any]:
        g = file_groups.get(title) or {}
        return {"title": title, "style": g.get("style") or "grid", "note": g.get("note") or "", "members": ms}

    out = [shaped(t["title"], [m for _usd, _i, m in sorted(sized[t["title"]], key=lambda x: (x[0], x[1]))] + unsized[t["title"]])
           for t in tiers]
    if spec.get("unlisted"):
        out.append(shaped(spec["unlisted"], unlisted))
    return out


def view(store: Store, roster: dict[str, Any]) -> dict[str, Any]:
    """一栏的界面数据：分组照文件，成员换成卡片；库里没有的人跳过，空分组不出。
    同一人在本栏别的分组里带了党派、州（参议院领导也是参议员），领导分组里照抄过来，像下照样写党派。
    filter 标出议员名单（名单条目自己带党派或州的分组）：国会页按人筛选，身份、党派、州取这个人在这些分组里的那一条，领导块同样筛。"""
    extra: dict[str, dict[str, Any]] = {}
    for group in roster.get("groups") or []:
        for m in _members(group):
            if any(m.get(k) is not None for k in FILTER_KEYS):
                extra.setdefault(m["person"], {k: m[k] for k in FILTER_KEYS if m.get(k) is not None})
    groups = []
    source = ranked(roster) if roster.get("market_cap") and roster.get("caps") else roster.get("groups") or []
    for group in source:
        members = [c for c in (card(store, {**extra.get(m["person"], {}), **m}) for m in _members(group)) if c]
        if not members:
            continue
        style = group.get("style") if group.get("style") in STYLES else "grid"
        groups.append({"title": group.get("title") or "", "note": group.get("note") or "", "style": style,
                       "filter": any(m.get("party") is not None or m.get("state") is not None for m in _members(group)),
                       "members": members})
    return {"id": roster.get("id"), "title": roster.get("title") or "", "groups": groups}


def index(store: Store, rosters: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """搜索用的名单人物索引：姓名、英文名、职务、所在栏目、标准像。名单外的人不在里面。"""
    titles = {sid: rosters.get(sid, {}).get("title") or t for sid, t in SECTIONS}
    out = []
    for pid, (sid, post) in listed(rosters).items():
        rec = store.person(pid)
        if not rec:
            continue
        out.append({"id": pid, "name": rec.get("name_zh") or rec.get("name_en") or pid, "name_en": rec.get("name_en") or "",
                    "post": post, "section": sid, "section_title": titles[sid], "portrait": portraits.display_src(rec)})
    return out


def person(store: Store, rosters: dict[str, dict[str, Any]], pid: str) -> dict[str, Any] | None:
    """人物页：标题（××简历）、标准像、标准简历（首段、现任、逐条经历）。不带来源、动态和导出。名单外的人返回 None。
    sections 是这个人所在的栏目，界面据此高亮栏目。"""
    rec = store.person(pid)
    if not rec or pid not in listed(rosters):
        return None
    doc = resume.compose(rec)["doc"]
    careers = [{"period": c.get("period") or "", "text": (c.get("organization") or "") + (c.get("role") or "")} for c in doc.get("careers") or []]
    return {"id": pid, "name": doc.get("name") or rec.get("name_zh") or "", "title": doc.get("title") or "",
            "portrait": portraits.display_src(rec), "lead": doc.get("lead") or "", "current": doc.get("current") or "",
            "careers": [c for c in careers if c["period"] or c["text"]], "sections": sections_of(rosters, pid)}


def credits(store: Store, rosters: dict[str, dict[str, Any]]) -> list[dict[str, str]]:
    """照片署名：名单里的人当中，照片按许可要署名的（CC BY），读 data/portraits/index.csv（portraits.credit）。"""
    out = []
    for pid in listed(rosters):
        rec = store.person(pid)
        text = portraits.credit(rec, store.root) if rec else None
        if text:
            out.append({"id": pid, "name": rec.get("name_zh") or rec.get("name_en") or pid, "credit": text.removeprefix("照片：")})
    return out
