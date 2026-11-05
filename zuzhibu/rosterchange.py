"""名单换人：立即更新查到名单上的职务换了人，本机名单换上新人、原任移出（docs/PRODUCT.md 第三、七节）。

名单文件是 data/rosters/<栏目>.json（格式见产品说明第三节）。两件事分开判断，宁可漏、不可错，漏了的由维护者每月复核名单：

1. 是不是同一岗位（same_post）：
   - 国会议员按实际议席（州、选区），商界按具体公司加首席执行官；
   - 其余按机构加职务名去掉国名、空白和标点后与名单写法相同。只有职务名相同的（大法官、司法部长），机构还要和这个位置
     现任的机构相同：州最高法院的大法官顶不了联邦最高法院的位置；
   - “代理”“临时”和正职算同一岗位；副职、常务副职和正职不是同一岗位；
   - 一个位置写着两项职务的（“国务卿兼国家安全事务助理”）、两家公司的，不认，交维护者。
2. 有没有足够证据替换现任（find）：
   - 新任要已经落地：任命、调任到任或代理，生效日不晚于今天。提名、参议院确认、生效日在未来的宣布不换名单；
   - 正式到任、名单上这个写法只有一个位置：新任到任就是原任离开的证据；
   - 代理：要有原任已离开这个岗位的正面证据（核实过的离任、逝世、调任动态），否则不换（代理可能只是暂代）；
   - 同一写法有几个位置的（大法官、同一州的两名参议员）：职称相同不能互相替换，只换有正面证据已离开这个岗位的那一位，
     对不上或对上不止一位的不动。
   同一个人由代理转为正式，这个位置只改职务写法；代理的写法保留“代理”，不写成正式，正式的也不改成代理。

换人和新任的人物记录（标准简历、官网照片）、这条动态一起落盘（commit：先记下要改的每个文件原来的内容，逐个写，中途出错
全部退回原样）；新任没有核实的标准简历和官网照片的，这一组都不写（不留半成品）。

只用标准库。
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterator

from . import personnel, resume
from .store import read_json, write_json

SECTIONS = ("whitehouse", "business", "congress", "defense", "justice")
#: 换名单的动态类别和新任的任职状态。
TRIGGER_KINDS = {"appoint": "正式", "transfer": "正式", "acting": "代理"}
LEFT_KINDS = ("leave", "death", "transfer")
_CEO = "首席执行官"
_TOP = ("董事长", "总裁", _CEO)
_SPLIT = re.compile(r"兼|、|，|,|/")
_ACTING = re.compile(r"代理|临时")


def folder(root: str | Path) -> Path:
    return Path(root) / "rosters"


def path_of(root: str | Path, section: str) -> Path:
    return folder(root) / f"{section}.json"


def load(root: str | Path) -> dict[str, dict[str, Any]]:
    """数据目录里的名单文件：{栏目: 名单}。没有的栏目不列。"""
    out = {}
    for name in SECTIONS:
        path = path_of(root, name)
        if path.exists():
            try:
                out[name] = read_json(path)
            except (OSError, ValueError):
                continue
    return out


def slots(rosters: dict[str, dict[str, Any]]) -> Iterator[tuple[str, int, int, dict[str, Any]]]:
    """名单上每个位置：(栏目, 第几组, 组里第几个, 成员)。"""
    for section, roster in rosters.items():
        for gi, group in enumerate(roster.get("groups") or []):
            for mi, member in enumerate(group.get("members") or []):
                yield section, gi, mi, member


def roster_people(rosters: dict[str, dict[str, Any]]) -> set[str]:
    return {m["person"] for _, _, _, m in slots(rosters) if m.get("person")}


# ──────────────────────────────────────────────────────────────── 对职务


def _key(text: str | None) -> str:
    """比对用写法：去国名、“白宫”、空白和标点；“代理”“临时”不算另一个职务。"""
    return personnel._norm(_ACTING.sub("", text or "")).replace("白宫", "")


def _titles(role: dict[str, Any]) -> list[str]:
    text = "、".join(role.get("roles") or []) or (role.get("role") or "")
    return [t.strip() for t in _SPLIT.split(text) if t.strip()]


def _seat_key(member: dict[str, Any]) -> str | None:
    """国会名单的议员位置：参议员按州，众议员按州和选区（不分选区的写州）。"""
    state = member.get("state")
    if not state:
        return None
    if "district" not in member:
        return _key(f"参议员（{state}）")
    district = member.get("district")
    return _key(f"众议员（{state}）" if district in (None, 0, "0", "") else f"众议员（{state}第{district}选区）")


def _business(post: str) -> tuple[str, list[str]] | None:
    """商界名单的职务：(公司, 头衔)。看不出首席执行官的、两家公司的不动。"""
    found = [post.find(t) for t in _TOP if t in post]
    if not found or min(found) <= 0:
        return None
    org = post[:min(found)]
    titles = [t for t in _TOP if t in post[min(found):]]
    if "、" in org or _CEO not in titles:
        return None
    return org, titles


def post_key(section: str, member: dict[str, Any]) -> str | None:
    """名单位置的比对写法；一个位置写着两项职务的返回 None（不动）。"""
    if section == "congress" and member.get("state"):
        return _seat_key(member)
    post = member.get("post") or ""
    if "兼" in post or "、" in post:
        return None
    return _key(post) or None


def match(section: str, member: dict[str, Any], role: dict[str, Any]) -> str | None:
    """这项职务和名单上这个位置怎么对上的：seat（国会议席）、company（公司加首席执行官）、full（机构加职务名）、
    title（只有职务名相同）；对不上返回 None。"""
    if role.get("sector") == "committee":
        return None
    if section == "business":
        parts = _business(member.get("post") or "")
        ok = bool(parts) and _key(role.get("organization")) == _key(parts[0]) and \
            _CEO in [_ACTING.sub("", t) for t in _titles(role)]
        return "company" if ok else None
    key = post_key(section, member)
    if not key:
        return None
    titles = _titles(role) or [""]
    org = role.get("organization") or ""
    if key in {_key(org + t) for t in titles}:
        return "seat" if section == "congress" and member.get("state") else "full"
    if section != "congress" and key in {_key(t) for t in titles if t}:
        return "title"
    return None


def _current(store, pid: str | None) -> list[dict[str, Any]]:
    rec = store.person(pid) if pid and store is not None else None
    return ((rec or {}).get("resume") or {}).get("current_roles") or []


def anchor(store, section: str, member: dict[str, Any]) -> set[str]:
    """名单上这个位置的机构：现任的人物记录里对得上这个位置的现任职务的机构（比对用写法）。"""
    return {_key(r.get("organization")) for r in _current(store, member.get("person")) if match(section, member, r)} - {""}


def same_post(section: str, member: dict[str, Any], role: dict[str, Any], store=None) -> bool:
    """是不是同一岗位。只有职务名相同的，机构要和这个位置现任的机构相同（store 给出现任的人物记录；没给的不认）。"""
    how = match(section, member, role)
    if how in ("seat", "company", "full"):
        return True
    return how == "title" and _key(role.get("organization")) in anchor(store, section, member)


def _join(titles: list[str]) -> str:
    if len(titles) <= 1:
        return "".join(titles)
    return "、".join(titles[:-1]) + "兼" + titles[-1] if len(titles) > 2 else titles[0] + "兼" + titles[1]


def new_post(section: str, member: dict[str, Any], role: dict[str, Any], state: str) -> str:
    """换人以后位置上写的职务：国会不变；商界照新任的头衔；其余照原写法，代理的在职务名前加“代理”，正式的去掉。"""
    post = member.get("post") or ""
    if section == "congress":
        return post
    if section == "business":
        org, _ = _business(post) or (post, [])
        own = [_ACTING.sub("", t) for t in _titles(role)]
        titles = [t for t in _TOP if t in own] or [_CEO]
        if state == "代理":
            titles = ["临时" + t if t == _CEO else t for t in titles]  # 企业的 interim 写“临时××”
        return org + _join(titles)
    base = post.replace("代理", "")
    if state != "代理":
        return base
    for title in sorted((_ACTING.sub("", t) for t in _titles(role)), key=len, reverse=True):
        if title and base.endswith(title):
            return base[: -len(title)] + "代理" + title
    return "代理" + base


# ──────────────────────────────────────────────────────────────── 找变动


def arrived(record: dict[str, Any], today: str) -> str | None:
    """新任已经落地：返回任职状态（正式、代理），没落地返回 None。提名、确认、宣布和生效日在未来的都不算。"""
    state = TRIGGER_KINDS.get(record.get("kind") or "")
    when = (record.get("effective_at") or record.get("date") or "")[:10]
    return state if state and when and when <= today else None


def person_of(store, record: dict[str, Any]) -> str | None:
    """动态说的是库里哪个人：有编号用编号，没有的按外部编号、姓名找（同名多人的不认）。"""
    if record.get("person_id") and store.person(record["person_id"]):
        return record["person_id"]
    rec, _ = personnel.resolve_person(store, record)
    return rec["id"] if rec else None


def tenure_start(store, section: str, member: dict[str, Any]) -> str | None:
    """现任在这个位置上的到任日期（人物记录的现任职务）；查不到返回 None。"""
    starts = [resume.value(r.get("start")) if isinstance(r.get("start"), dict) else (r.get("start") or "")
              for r in _current(store, member.get("person")) if match(section, member, r)]
    starts = [s for s in starts if s]
    return max(starts) if starts else None


def departed(store, section: str, member: dict[str, Any], batch: list[dict[str, Any]], today: str) -> bool:
    """名单上这个位置的现任已核实离开：这次一起写回的动态里、或库里他到任以后的动态里，有他离任、逝世、调任，
    原职务就是这个岗位，生效日不晚于今天。只认这种正面证据：人物记录里没写这项职务不算（记录可能没写全）。"""
    pid = member.get("person")
    if not pid:
        return False
    since = tenure_start(store, section, member)
    found = [(ev, True) for ev in batch]
    if since:
        found += [(ev, False) for ev in store.events() if ev.get("person_id") == pid]
    for ev, current in found:
        if ev.get("kind") not in LEFT_KINDS or (current and person_of(store, ev) != pid):
            continue
        when = (ev.get("effective_at") or ev.get("date") or "")[:10]
        if not when or when > today or (not current and when < since):
            continue
        if any(same_post(section, member, r, store) for r in ev.get("ended_roles") or []):
            return True
    return False


def find(store, rosters: dict[str, dict[str, Any]], record: dict[str, Any], batch: list[dict[str, Any]],
         today: str) -> list[dict[str, Any]]:
    """这条动态让名单上哪些位置换人。在这批动态写回之前判断（现任的人物记录还是写回前的样子）；
    record 是要写回的动态，batch 是这次一起写回的全部动态。

    返回变动：{kind: replace（换人）或 restate（同一人代理转正式，只改写法）, section, group, index, old, post_before,
    post_after, state, evidence, event, date, person_id, name_en, name_zh}。同一岗位在两个栏目都有的
    （司法部长在白宫、司法两栏）各算一处。
    """
    state = arrived(record, today)
    if not state:
        return []
    pid = person_of(store, record)
    person = record.get("person") or {}
    base = {"event": record.get("id"), "date": record.get("date"), "person_id": pid,
            "name_en": person.get("name_en") or "", "name_zh": person.get("name_zh") or ""}
    changes: list[dict[str, Any]] = []
    for role in record.get("new_roles") or []:
        acting = state == "代理" or any(_ACTING.search(t) for t in _titles(role))
        role_state = "代理" if acting else "正式"
        by_section: dict[str, list[tuple[int, int, dict[str, Any]]]] = {}
        for section, gi, mi, member in slots(rosters):
            if same_post(section, member, role, store):
                by_section.setdefault(section, []).append((gi, mi, member))
        for section, found in by_section.items():
            mine = [(gi, mi, m) for gi, mi, m in found if pid and m.get("person") == pid]
            if mine:  # 已经在这个位置上：只有代理转正式时改写法
                gi, mi, member = mine[0]
                post = new_post(section, member, role, role_state)
                if role_state == "正式" and _ACTING.search(member.get("post") or "") and post != member.get("post"):
                    changes.append({**base, "kind": "restate", "section": section, "group": gi, "index": mi,
                                    "old": pid, "post_before": member.get("post"), "post_after": post, "state": role_state,
                                    "evidence": "同一人由代理转为正式到任", "role": role})
                continue
            if len(found) > 1 or acting:
                found = [(gi, mi, m) for gi, mi, m in found if departed(store, section, m, batch, today)]
                why = ("代理，原任已核实离开这个岗位" if len(by_section[section]) == 1
                       else "同一写法有几个位置，只换已核实离开的那一位")
            else:
                why = "正式到任，名单上这个岗位只有一个位置"
            if len(found) != 1:
                continue
            gi, mi, member = found[0]
            changes.append({**base, "kind": "replace", "section": section, "group": gi, "index": mi,
                            "old": member.get("person"), "post_before": member.get("post"),
                            "post_after": new_post(section, member, role, role_state), "state": role_state,
                            "evidence": why, "role": role})
    return changes


# ──────────────────────────────────────────────────────────────── 改名单


def locate(roster: dict[str, Any] | None, change: dict[str, Any], person: str | None) -> dict[str, Any] | None:
    """名单里这处变动所在的位置，且现在坐着 person：先按记下的组和序号找，挪过位置的按人和原写法找。"""
    if not roster:
        return None
    groups = roster.get("groups") or []
    posts = (change.get("post_before"), change.get("post_after"))
    gi, mi = change.get("group"), change.get("index")
    if isinstance(gi, int) and isinstance(mi, int) and gi < len(groups) and mi < len(groups[gi].get("members") or []):
        member = groups[gi]["members"][mi]
        if member.get("person") == person and member.get("post") in posts:
            return member
    hits = [m for g in groups for m in g.get("members") or [] if m.get("person") == person and m.get("post") in posts]
    return hits[0] if len(hits) == 1 else None


def place(rosters: dict[str, dict[str, Any]], change: dict[str, Any], person_id: str, party: str | None = None) -> bool:
    """在内存里的名单上落实一处变动：原任移出、新任坐进这个位置（或同一人改写法）。位置上已不是原任的不动，返回 False。"""
    member = locate(rosters.get(change["section"]), change, change.get("old"))
    if member is None:
        return False
    member["person"] = person_id
    member["post"] = change["post_after"]
    if "party" in member and party is not None:
        member["party"] = party
    return True


def standing(rosters: dict[str, dict[str, Any]], change: dict[str, Any]) -> str:
    """维护者数据换新后，本机这处变动在新名单上的情形：kept（新任已在位、写法已是变动后的）、open（还是原任或原写法，
    可以重放）、merged（位置上已是第三人或没有这个位置：维护者另有安排，以维护者为准）。"""
    roster = rosters.get(change["section"])
    member = locate(roster, change, change.get("new"))
    if member is not None and (member.get("post") == change.get("post_after") or change.get("old") != change.get("new")):
        return "kept"
    if member is not None or locate(roster, change, change.get("old")) is not None:
        return "open"
    return "merged"


# ──────────────────────────────────────────────────────────────── 落盘


def ready(record: dict[str, Any] | None) -> list[str]:
    """人物记录够不够上名单：完整简历卡（五项齐全、现任职务有一级来源）、有照片（本地照片或官网照片）。
    返回不够的原因，空表示够。写法检查另由获取简历时做（bureaus/dispatch/resumes.py）。"""
    if record is None:
        return ["库里没有这个人的记录"]
    kind, reasons = resume.card_type(record)
    problems = [] if kind else [f"不够完整简历卡：{'；'.join(reasons)}"]
    portrait = record.get("portrait") or {}
    if not (portrait.get("file") or portrait.get("url")):
        problems.append("没有照片")
    return problems


def _put(path: Path, raw: bytes) -> None:
    """原样写回字节：先写临时文件再替换（附带数据和快照共用的硬链接文件不就地改）。"""
    tmp = path.with_name(f".tmp-restore-{path.name}")
    tmp.write_bytes(raw)
    os.replace(tmp, path)


def commit(writes: dict[Path, Any]) -> None:
    """一组文件一起写：先记下每个文件原来的内容，逐个写；中途出错全部退回原样（新建的删掉），再把错误抛出。"""
    done: list[tuple[Path, bytes | None]] = []
    try:
        for path, obj in writes.items():
            path = Path(path)
            done.append((path, path.read_bytes() if path.exists() else None))
            write_json(path, obj)
    except BaseException:
        for path, raw in reversed(done):
            try:
                if raw is None:
                    path.unlink(missing_ok=True)
                else:
                    _put(path, raw)
            except OSError:
                pass
        raise
