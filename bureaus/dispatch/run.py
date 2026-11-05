"""一局更新：采集线索 → 研究 → 核查 → 写回人物记录；本机的立即更新另有获取简历、名单换人（docs/PRODUCT.md 第四、七节）。

在仓库根目录运行，本机已安装并登录 Codex CLI：

    python -m bureaus.dispatch.run --daily                       # 每日更新：近 3 天
    python -m bureaus.dispatch.run --since 2026-08-01 --until 2026-10-17 --origin backfill
    python -m bureaus.dispatch.run --daily --groups congress,gov --jobs 2
    python -m bureaus.dispatch.run --daily --dry-run             # 只采集线索、写提示词，不调 Codex
    python -m bureaus.dispatch.run --inbox inbox/codex/<任务>/events.json   # 补采的动态：只做核查和写回
    python -m bureaus.dispatch.run --recheck <动态编号>,<动态编号>         # 已入库的动态按现行规则重核，不重新写回

使用者用自己的 AI 接口（设置里填的大模型接口和密钥，用使用者自己的额度；软件“立即更新”就是这样调的）：

    python -m bureaus.dispatch.run --backend api --since-last --data <本机数据目录> --jobs 1 \\
        --progress <进度文件> --stop-file <停止文件>

--backend api 时：先检测接口能不能联网检索；先把本机更新目录里以前写进的动态重放到数据上（zuzhibu/localupdates.py）；
窗口从上次截止日到今天；写进的动态另记一份到本机更新目录；状态记在 meta.json 的 user_update，不顶替维护者的每日更新状态。
放行和写回规则与维护者完全相同；结果只写 --data 指的目录，不提交、不推送。

按 6 步上报进度（zuzhibu/progress.py，设置里的进度条读 --progress 文件）：

1. 连接接口：检测接口能不能联网检索；本机更新目录里以前写进的动态重放到数据上；
2. 查询公开信息：从免费公开数据采集线索（collect.py）；
3. 深度思考：按资料库五栏分五组（白宫、商界、国会、国防、司法），每组一次研究会话；
4. 逐条核实：先剔除库里已有的、同批别组已报的、范围外的、没有职务变动的，再程序核查
   （research.precheck：一级来源、摘录逐字找到，人物、职务、类别、日期都在来源里），过不了的一次一条开核查会话，
   会话没联网或没打开过来源的不放行；
5. 获取简历：本机更新（有本机更新目录）时，已核实落地的任免让名单上的位置换人的（规则见 zuzhibu/rosterchange.py），
   给新任人员查标准简历和官网照片（resumes.py）；名单以外的人不取简历，只在动态里出现；
6. 更新简历：放行的动态写回人物记录。换人的，新任的人物记录、照片、名单和这条动态一起落盘，缺一样就都不写，
   这一组留在本机更新目录，下次立即更新接着取简历。每月更新（--daily）和立即更新（--backend api）写完接着刷新商界名单的
   市值（marketcap.py，同一个后端），不另报一步；结果只记日志和运行记录，不影响这次更新算不算完成。

核实通过的动态写进 data/events/，按规则写回 data/people/；搁置的只留在过程记录 output/dispatch/<时间>/held.json。

停止（--stop-file 指的文件出现）：不再开新会话，接口上正在跑的会话取消；已核实的照样写回，再报“已停止”。
市值还没开始刷新的不刷新，刷新到一半停下的市值文件整份不写。
接口密钥、额度、联网检索出问题时同样停下，已核实的照样写回，再报“未完成”和原因。停下的这次不推进截止日，下次把这段重查一遍。

退出码：0 完成；2 Codex 没找到或未登录，或 AI 接口没配好、密钥或额度有问题、不能联网检索；3 有组失败；
4 另一次更新正在运行；5 补采动态文件不合格式；6 使用者停止。只用标准库。
"""

from __future__ import annotations

import argparse
import collections
import concurrent.futures
import copy
import csv
import dataclasses
import datetime as dt
import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

from shared.backends import ai_api, codex_cli, schema_lite
from zuzhibu import bureaus, importer, localupdates, personnel, portraits, progress, renmian, resume, rosterchange
from zuzhibu.store import REPO, Store, name_key, read_json, slugify, write_json

from . import collect, marketcap, research, resumes

DEFAULT_RUNS = REPO / "output" / "dispatch"
GLOSSARY_PERSON = REPO / "shared" / "glossary" / "译名表_v1.4_person.csv"
LOCK_MAX_AGE = 3 * 3600
INBOX_CHUNK = 1    # 补采动态每次核查会话核的条数；会话的联网动作少于条数的整组搁置
#: 名单新人的简历最多取几次（每次立即更新取一次）；都没取到的留在本机更新目录，交维护者每月复核名单。
RESUME_ATTEMPTS = 3
EXIT_STOPPED = 6
#: 名单栏目对应的研究分组。
SECTION_GROUP = {"whitehouse": "whitehouse", "business": "ai", "congress": "congress", "defense": "military", "justice": "justice"}
#: 整次更新都过不去的问题：停下，已核实的照样写回。
FATAL = (ai_api.ApiAuthError, ai_api.ApiNoWebSearch, codex_cli.CodexAuthError, codex_cli.CodexNotFound)


def resolve_codex(explicit: str | None) -> str:
    """显式路径 → PATH → %LOCALAPPDATA%\\OpenAI\\Codex\\bin\\*\\codex.exe 里最新的一个。"""
    if explicit:
        if Path(explicit).is_file():
            return str(Path(explicit).resolve())
        found = shutil.which(explicit)
        if found:
            return found
        raise codex_cli.CodexNotFound(f"找不到 Codex 程序：{explicit}")
    found = shutil.which("codex.exe") or shutil.which("codex")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    candidates = list((Path(local) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe")) if local else []
    if candidates:
        return str(max(candidates, key=lambda p: p.stat().st_mtime).resolve())
    raise codex_cli.CodexNotFound("未找到 Codex 程序：请安装并登录 Codex，或用 --codex 指定 codex.exe")


def glossary_names() -> dict[str, str]:
    with GLOSSARY_PERSON.open(encoding="utf-8-sig", newline="") as fh:
        return {r["english_name"].strip(): (r.get("新华社译名") or r.get("本人汉名或通行汉名") or "").strip()
                for r in csv.DictReader(fh) if (r.get("新华社译名") or r.get("本人汉名或通行汉名"))}


class Log:
    def __init__(self, path: Path):
        self.path = path
        self.secrets: tuple[str, ...] = ()  # 接口密钥：报错原文里有的一律隐去

    def __call__(self, msg: str) -> None:
        line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {ai_api.redact(msg, *self.secrets)}"
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("gbk", errors="replace").decode("gbk"), flush=True)


class Halt:
    """停下来：使用者点了“停止更新”（停止文件出现），或接口出了整次都过不去的问题（FATAL）。
    停下以后不再开新会话；接口上正在跑的会话由 ai_api 取消（ApiConfig.cancel 就是这个对象）。"""

    def __init__(self, report: progress.Progress):
        self.report = report
        self.flag = threading.Event()
        self.error: BaseException | None = None

    def __call__(self) -> bool:
        if not self.flag.is_set() and self.report.stop_requested():
            self.flag.set()
        return self.flag.is_set()

    def fail(self, exc: BaseException) -> None:
        if self.error is None:
            self.error = exc
        self.flag.set()

    @property
    def by_user(self) -> bool:
        return self.flag.is_set() and self.error is None


def reason(exc: BaseException) -> str:
    """停下的原因，用在“更新未完成：××。”里。"""
    if isinstance(exc, codex_cli.CodexNotFound):
        return "没有找到本机 Codex"
    if isinstance(exc, codex_cli.CodexAuthError):
        return "本机 Codex 未登录"
    return ai_api.reason_of(exc)[1]


def parse_groups(text: str) -> list[str]:
    """--groups：资料库五栏的组名（research.GROUPS）；gov 是白宫、国防、司法三组的旧写法。"""
    out: list[str] = []
    for name in (g.strip() for g in text.split(",")):
        for group in research.GROUP_ALIASES.get(name, (name,)):
            if group in research.GROUPS and group not in out:
                out.append(group)
    return out


def group_for(bureau: str | None) -> str:
    """人物所属局对应的研究分组；判不出的归白宫组。"""
    return bureau if bureau in research.GROUPS else "whitehouse"


def group_of(lead: dict[str, Any], store: Store) -> str:
    if lead["source"] == "congress-legislators":
        return "congress"
    rec = store.person(lead.get("person_id") or "")
    return group_for((rec or {}).get("bureau"))


def inbox_group(event: dict[str, Any], store: Store) -> str:
    """补采动态按人物所在局分组；库里没有的人按 bureau_hint。"""
    rec = store.person(event.get("person_id") or "")
    return group_for((rec or {}).get("bureau") or event.get("bureau_hint"))


def roster_text(store: Store, rosters: dict[str, dict[str, Any]]) -> dict[str, str]:
    """研究提示词里的名单职务，按组：每组只列本栏的；国会只列两院领导（议员变动靠国会数据的线索）。"""
    lines: dict[str, list[str]] = {}
    for section, _, _, member in rosterchange.slots(rosters):
        if section == "congress" and member.get("state"):
            continue
        rec = store.person(member.get("person") or "") or {}
        lines.setdefault(SECTION_GROUP[section], []).append(
            f"- {member.get('person') or '（空缺）'}｜{rec.get('name_zh') or ''}｜{rec.get('name_en') or ''}｜{member.get('post') or ''}")
    return {group: "\n".join(found) for group, found in lines.items()}


_TOP_ROLE = re.compile(r"首席执行官|总裁|董事长|创始人|首席技术官|首席科学家|首席人工智能官|chief executive|\bceo\b|president|"
                       r"\bchair|founder|chief technology|\bcto\b|chief scientist|chief ai officer", re.I)


def out_of_scope(record: dict[str, Any], store: Store) -> str | None:
    """二局库外新人：在库人物所在机构的职务都收；别处只收首席执行官、总裁、董事长、创始人、首席技术官、首席科学家一级。"""
    if record.get("person_id"):
        return None
    known = set()
    for rec in store.people():
        if rec.get("bureau") == bureaus.AI:
            known.update(personnel._norm(r.get("organization") or "") for r in rec["resume"].get("current_roles") or [])
            known.update(personnel._norm(r.get("organization_en") or "") for r in rec.get("roster_roles") or [])
    known.discard("")
    for role in (record.get("new_roles") or []) + (record.get("ended_roles") or []):
        if personnel._norm(role.get("organization") or "") in known or personnel._norm(role.get("organization_en") or "") in known:
            return None
        if _TOP_ROLE.search(role.get("role") or "") or _TOP_ROLE.search(role.get("role_en") or ""):
            return None
    return "二局范围外：库外机构的董事、临时院长所长等不收（范围见 research.SCOPES）"


def not_collected(record: dict[str, Any], store: Store, group: str) -> str | None:
    """不收的动态和原因：涉及中国企业职务的（各组都不收，维护者 2026-10-23 定，名单见 bureaus.CHINESE_COMPANIES）；
    二局范围外的（out_of_scope）。要收的返回 None。"""
    if any(bureaus.chinese_company(r) for r in (record.get("new_roles") or []) + (record.get("ended_roles") or [])):
        return "中国企业不收（维护者 2026-10-23 定）"
    return out_of_scope(record, store) if group == "ai" else None


# ──────────────────────────────────────────────────────────────── 第 3 步：研究


def research_group(name: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """一组的研究会话；补采动态（--inbox）不开研究会话。返回 {name, leads, events, gated, research, stats, error, reason}。"""
    log, runs, halt = ctx["log"], ctx["runs"] / name, ctx["halt"]
    leads = [c for c in ctx["leads"] if c["group"] == name]
    runs.mkdir(parents=True, exist_ok=True)
    out: dict[str, Any] = {"name": name, "leads": len(leads), "events": [], "gated": [], "research": None, "stats": {},
                           "error": None, "reason": None}
    if ctx.get("inbox") is not None:
        out["events"] = [e for e in ctx["inbox"] if inbox_group(e, ctx["store"]) == name]
        out["research"] = {"events": out["events"], "checked_candidates": [], "notes": "补采动态，未开研究会话"}
        write_json(runs / "research.json", out["research"])
        log(f"[{name}] 补采动态 {len(out['events'])} 条")
        return out
    prompt = research.research_prompt(today=ctx["today"], since=ctx["since"], until=ctx["until"], scope=research.GROUPS[name],
                                      leads=leads, people=ctx["store"].people(), min_searches=ctx["min_searches"],
                                      roster=ctx["roster_text"].get(name, ""))
    (runs / "research.prompt.md").write_text(prompt, encoding="utf-8")
    if ctx["dry_run"]:
        out["error"] = "dry-run"
        return out
    if halt():
        return out
    ctx["started"](name)
    t0 = time.monotonic()
    log(f"[{name}] 研究会话开始：线索 {len(leads)} 条")
    try:
        data, stats, err = research.call(prompt, research.RESEARCH_SCHEMA, ctx["research_config"], runs, f"{name}-research")
    except ai_api.ApiCancelled:
        log(f"[{name}] 更新已停止，研究会话已取消")
        return out
    except FATAL as exc:
        halt.fail(exc)
        log(f"[{name}] 研究会话停下：{exc}")
        return out
    out["stats"]["research"] = {**stats, "seconds": round(time.monotonic() - t0)}
    if err or data is None:
        out["error"], out["reason"] = f"研究会话失败：{err}", stats.get("reason") or "接口出错"
        log(f"[{name}] {out['error']}")
        return out
    if stats["searches"] <= 0:
        out["error"], out["reason"] = "研究会话没有联网检索记录，结果作废", "该接口不支持联网检索"
        log(f"[{name}] {out['error']}")
        return out
    write_json(runs / "research.json", data)
    out["research"] = data
    out["events"] = data.get("events") or []
    log(f"[{name}] 研究完成：动态 {len(out['events'])} 条，检索 {stats['searches']} 次，{out['stats']['research']['seconds']} 秒")
    return out


# ──────────────────────────────────────────────────────────────── 第 4 步：核实


def verify_all(results: list[dict[str, Any]], ctx: dict[str, Any], jobs: int) -> None:
    """各组研究出的动态先剔除（inbox_screen：库里已有的、同批别组已报的……），再逐条核实。结果追加进各组的 gated。"""
    queued: list[dict[str, Any]] = []  # 已排上核实的动态（写回格式）：别组再报的同一条算重复
    queues = []
    for res in results:
        batch = ctx["inbox"] if ctx.get("inbox") is not None else res["events"]
        queue = []
        for ev in res["events"]:
            skip = inbox_screen(ev, ctx, res["name"], batch, queued)
            if skip:
                res["gated"].append(skip)
                ctx["tick"]()
            else:
                queue.append(ev)
                queued.append(to_record(ctx["store"], ev, {"status": "held", "corrections": [], "reasons": []}, ctx,
                                        ctx["names"], res["name"]))
        queues.append((res, queue, len(res["events"]) - len(queue)))

    def one(item):
        res, queue, screened = item
        verify_one_by_one(res["name"], queue, ctx, ctx["runs"] / res["name"], res)
        res["stats"].setdefault("precheck", {})["screened"] = screened

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        list(pool.map(one, queues))


def verify_one_by_one(name: str, events: list[dict[str, Any]], ctx: dict[str, Any], runs: Path, out: dict[str, Any]) -> None:
    """逐条核查（每月更新、立即更新、补采动态和重核都走这里，2026-10-21 起）：先程序核查，过不了的一次开一个核查会话
    只核一条；会话没联网的整条搁置，会话里没打开过的来源不算核实。结果追加进 out["gated"]。
    停下以后（ctx["halt"]）不再开会话，没核的记“没有核实”。"""
    log = ctx["log"]
    tick = ctx.get("tick") or (lambda: None)
    halt = ctx.get("halt") or (lambda: False)
    pre: list[dict[str, Any] | None] = [None] * len(events)
    if ctx["excerpt_check"] and events:
        prefetch([s.get("url") or "" for ev in events for s in ev.get("sources") or []], ctx)
        pre = [research.precheck(ev, ctx["fetch"]) for ev in events]
    passed = [p for p in pre if p and p["status"] == "verified"]
    rest = [(ev, p) for ev, p in zip(events, pre) if not (p and p["status"] == "verified")]
    for _ in passed:
        tick()
    out["gated"] += passed
    out["stats"]["precheck"] = {"passed": len(passed), "to_session": len(rest), "screened": 0}
    log(f"[{name}] 程序核查放行 {len(passed)} 条，其余 {len(rest)} 条开核查会话，每次 {INBOX_CHUNK} 条")
    size = INBOX_CHUNK
    for k in range(0, len(rest), size):
        chunk = rest[k:k + size]
        if halt():
            out["gated"] += [unchecked(ev) for ev, _ in rest[k:]]
            break
        tick()
        part: dict[str, Any] = {"gated": [], "stats": {}}
        sub = runs / f"part{k // size + 1:03d}"
        sub.mkdir(parents=True, exist_ok=True)
        try:
            verify_events(name, [ev for ev, _ in chunk], ctx, sub, part, opened=True)
        except ai_api.ApiCancelled:
            out["gated"] += [unchecked(ev) for ev, _ in rest[k:]]
            break
        except FATAL as exc:
            if not isinstance(ctx.get("halt"), Halt):
                raise
            ctx["halt"].fail(exc)
            log(f"[{name}] 核查会话停下：{exc}")
            out["gated"] += [unchecked(ev) for ev, _ in rest[k:]]
            break
        done = (part["stats"].get("verify") or {}).get("searches", 0)
        for g, (_, p) in zip(part["gated"], chunk):
            g["method"] = "api" if ctx.get("backend") == "api" else "codex"
            g["precheck"] = p["reasons"] if p else ["没做程序核查（--no-excerpt-check）"]
            if done < len(chunk) and g["status"] == "verified":
                g["status"] = "held"
                g["reasons"].append(f"核查会话联网 {done} 次，少于本组 {len(chunk)} 条动态，视为没有逐条核查")
        out["gated"] += part["gated"]
        total = out["stats"].setdefault("verify", {"searches": 0, "seconds": 0})
        for key in ("searches", "seconds"):
            total[key] += (part["stats"].get("verify") or {}).get(key, 0)


def unchecked(event: dict[str, Any]) -> dict[str, Any]:
    """停下时还没核的一条：搁置，不写回（下次更新重查这段窗口）。"""
    return {"event": copy.deepcopy(event), "status": "held", "corrections": [], "method": "stopped",
            "reasons": ["更新停下，这条没有核实"]}


def verify_events(name: str, events: list[dict[str, Any]], ctx: dict[str, Any], runs: Path, out: dict[str, Any],
                  opened: bool = False) -> None:
    """核查会话 → 放行，结果写进 out["gated"]。opened：会话里没打开过的来源不算核实（补采动态）。"""
    log = ctx["log"]
    t1 = time.monotonic()
    vprompt = research.verify_prompt(today=ctx["today"], events=events)
    (runs / "verify.prompt.md").write_text(vprompt, encoding="utf-8")
    review, vstats, verr = research.call(vprompt, research.VERIFY_SCHEMA, ctx["verify_config"], runs, f"{name}-verify")
    out["stats"]["verify"] = {**vstats, "seconds": round(time.monotonic() - t1)}
    if verr:
        log(f"[{name}] 核查会话失败：{verr}；全部搁置")
    else:
        write_json(runs / "verify.json", review)
    out["gated"] = research.gate(events, review if not verr else None, vstats["searches"] if not verr else 0,
                                 opened=(vstats.get("opened") or []) if opened else None)
    ok = sum(1 for g in out["gated"] if g["status"] == "verified")
    who = "、".join(e.get("name_en") or "" for e in events) if len(events) <= 2 else f"{len(events)} 条"
    log(f"[{name}] 核查完成（{who}）：通过 {ok}，搁置 {len(out['gated']) - ok}，检索 {vstats['searches']} 次，"
        f"{out['stats']['verify']['seconds']} 秒")


def to_record(store: Store, ev: dict[str, Any], gated: dict[str, Any], ctx: dict[str, Any], names: dict[str, str],
              group: str = "") -> dict[str, Any]:
    lead = next((c for c in ctx["leads"] if c["id"] == ev.get("candidate_id")), None)
    person_id = ev.get("person_id") if store.person(ev.get("person_id") or "") else None
    if not person_id and lead and lead.get("person_id") and store.person(lead["person_id"]):
        person_id = lead["person_id"]
    name_en = ev.get("name_en") or (lead or {}).get("name_en") or ""
    name_source = "glossary" if names.get(name_en) else "research"
    name_zh = names.get(name_en) or ev.get("name_zh") or ""
    if person_id:
        name_zh = store.person(person_id).get("name_zh") or name_zh
    date = (ev.get("effective_at") or ev.get("announced_at") or (lead or {}).get("date") or ctx["today"])[:10]
    if date > ctx["today"] and ev.get("announced_at"):
        date = ev["announced_at"][:10]  # 生效日还没到的按宣布记，动态日期取宣布日
    if len(date) == 7:
        date += "-01"
    checks = ctx.get("excerpt_cache") or {}
    sources = [{**s, "checked_at": ctx["today"], "excerpt_check": checks.get((s.get("url", ""), s.get("excerpt", "")))}
               for s in ev.get("sources") or []]
    api = ctx.get("backend") == "api"
    method = gated.get("method") or ("api" if api else "codex")
    if method == "excerpt":
        verification = {"status": gated["status"], "method": method, "checked_at": ctx["today"], "model": None,
                        "by": "程序核查：逐条取来源网页，一级来源的摘录逐字找到，人物、职务、类别、日期都在核实来源里"}
    else:
        who = f"使用者自己的 AI 接口（{ctx['api_label']}）" if api else "本机 Codex "
        by = who + ("核查会话（补采动态，一次一条）" if ctx.get("inbox") is not None else "研究会话，核查会话一次一条")
        verification = {"status": gated["status"], "method": "api" if api else "codex", "by": by, "checked_at": ctx["today"],
                        "model": f"{ctx['model']}/{ctx['effort']}"}
    verification.update(run=ctx["run_id"], corrections=gated["corrections"], reasons=gated["reasons"])
    if gated.get("notes"):
        verification["notes"] = gated["notes"]
    if gated.get("precheck"):
        verification["precheck"] = gated["precheck"]  # 程序核查没过的原因（因此开了核查会话）
    record = {
        "id": store.new_event_id(date, person_id or slugify(name_en, "person"), ev["kind"]),
        "date": date, "kind": ev["kind"], "kind_label": personnel.KIND_LABEL.get(ev["kind"], ev["kind"]),
        "person_id": person_id, "person": {"name_zh": name_zh, "name_en": name_en, "name_zh_source": name_source,
                                           "ids": (lead or {}).get("ids") or {}},
        "announced_at": ev.get("announced_at"), "effective_at": ev.get("effective_at"),
        "ended_roles": ev.get("ended_roles") or [], "new_roles": ev.get("new_roles") or [],
        "other_current_roles": ev.get("other_current_roles"), "new_appointment": ev.get("new_appointment"),
        "new_appointment_checked": bool(ev.get("new_appointment_checked")),
        "headline": ev.get("headline_zh") or "", "summary": ev.get("summary_zh") or "",
        "bureaus": [b for b in [ev.get("bureau_hint")] if b in bureaus.ACTIVE_BUREAUS],
        "sources": sources,
        "verification": verification,
        "origin": ctx["origin"], "lead": ev.get("candidate_id"),
    }
    scope = research.GROUPS.get(group) or ()
    if len(scope) == 1:
        record["default_bureau"] = scope[0]  # 只管一个局的组查出的新人物，程序判不出机构时归这个局
    return record


def _orgs(event: dict[str, Any]) -> set[str]:
    return {personnel._norm(r.get("organization") or "") for r in (event.get("new_roles") or []) + (event.get("ended_roles") or [])} - {""}


def duplicate_of(store: Store, record: dict[str, Any], others: list[dict[str, Any]] = ()) -> str | None:
    """库里（或同批 others 里）已有的同一条动态：同一人、同一类别、日期相差 7 天内或宣布日相同；
    同一人、同一天、同一机构的，类别不同也算。"""
    key = name_key(record["person"].get("name_en") or "")
    for old in [*store.events(), *others]:
        if old is record:
            continue
        same_person = (record["person_id"] and old.get("person_id") == record["person_id"]) or \
                      (key and name_key((old.get("person") or {}).get("name_en") or "") == key)
        if not same_person:
            continue
        if old.get("kind") != record["kind"]:
            if (old.get("date") or "")[:10] == (record.get("date") or "")[:10] and _orgs(old) & _orgs(record):
                return old["id"]  # 同一天、同一机构，类别不同也是同一件事（确认日被改成宣誓日、宣布与到任同日等）
            continue
        try:
            gap = abs((dt.date.fromisoformat(old["date"][:10]) - dt.date.fromisoformat(record["date"][:10])).days)
        except ValueError:
            gap = 0
        if gap <= 7 or (record.get("announced_at") and old.get("announced_at") == record["announced_at"]):
            return old["id"]  # 宣布日相同也算：核查把生效日改成宣誓日后，动态日期会挪开
    return None


_WITHDRAWN = re.compile(r"撤回|withdr[ae]w", re.I)


def withdrawn_later(event: dict[str, Any], batch: list[dict[str, Any]]) -> str | None:
    """提名、确认、宣布之后，同批补采里有此人撤回提名的动态（类别记成宣布、没有职务变动、说的是撤回）：返回撤回的日期。"""
    if event.get("kind") not in personnel.RECORD_ONLY_KINDS:
        return None
    key = name_key(event.get("name_en") or "")
    when = (event.get("announced_at") or event.get("effective_at") or "")[:10]
    for other in batch:
        if other is event or other.get("kind") != "announce" or other.get("ended_roles") or other.get("new_roles") or \
                name_key(other.get("name_en") or "") != key:
            continue
        text = " ".join([other.get("headline_zh") or "", other.get("summary_zh") or ""] +
                        [f"{s.get('title') or ''} {s.get('url') or ''}" for s in other.get("sources") or []])
        date = (other.get("announced_at") or other.get("effective_at") or "")[:10]
        if _WITHDRAWN.search(text) and date and date >= when:
            return date
    return None


def inbox_screen(event: dict[str, Any], ctx: dict[str, Any], group: str,
                 batch: list[dict[str, Any]] | None = None, queued: list[dict[str, Any]] = ()) -> dict[str, Any] | None:
    """核查前先剔除，免得白开核查会话：库里或同批已排上核实的（duplicate）、没有职务变动可写回的、之后撤回了的提名、
    人物编号和英文名对不上的（held）、二局范围外的（out_of_scope）。返回 gate 格式的一条，不用剔除的返回 None。"""
    store = ctx["store"]
    record = to_record(store, event, {"status": "held", "corrections": [], "reasons": []}, ctx, ctx["names"], group)
    base = {"event": copy.deepcopy(event), "corrections": [], "method": "screen"}
    dup = duplicate_of(store, record, queued)
    if dup:
        return {**base, "status": "duplicate", "reasons": [f"库里已有 {dup}"], "duplicate_of": dup}
    record_only = event.get("kind") in personnel.RECORD_ONLY_KINDS  # 提名、确认、宣布只写新职务
    if not event.get("new_roles") and (record_only or not event.get("ended_roles")):
        if event.get("new_appointment") and record_only:
            why = "职务写在 new_appointment，new_roles 是空的（提名、确认、宣布的职务应写进 new_roles）"
        elif event.get("ended_roles"):
            why = "宣布、提名、确认类只写新职务，这条只有原职务（已离任的应按离任报）"
        else:
            why = "撤回提名、委员会表决一类没有职务变动的事项，现行写回规则没有对应写法"
        return {**base, "status": "held", "reasons": [f"没有可写回的职务：{why}；交维护者"]}
    withdrawn = withdrawn_later(event, batch if batch is not None else ctx.get("inbox") or [])
    if withdrawn:
        return {**base, "status": "held", "reasons": [
            f"提名已于 {withdrawn} 撤回（同批有撤回动态）：写回规则没有撤回的写法，不写“提名人选”；交维护者"]}
    rec = store.person(record["person_id"] or "")
    if rec and research.surname(rec.get("name_en") or "") != research.surname(event.get("name_en") or ""):
        return {**base, "status": "held",
                "reasons": [f"人物编号 {rec['id']} 是 {rec.get('name_en')}，和动态里的 {event.get('name_en')} 对不上；交维护者"]}
    why = not_collected(record, store, group)
    if why:
        return {**base, "status": "out_of_scope", "reasons": [why]}
    return None


def prefetch(urls: list[str], ctx: dict[str, Any]) -> None:
    """并行取网页，存进 ctx["pages"]（程序核查和摘录核对共用，同一网页只取一次）。"""
    todo = sorted({u for u in urls if u and u not in ctx["pages"]})
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for url, raw in zip(todo, pool.map(research.fetch_page, todo)):
            ctx["pages"][url] = raw


def to_research_event(stored: dict[str, Any]) -> dict[str, Any]:
    """已入库的动态还原成研究会话的格式（EVENT_SCHEMA），重核用；candidate_id 放动态编号（核查提示词里不出现）。"""
    keep = ("id", "url", "title", "publisher", "published_at", "excerpt")
    person = stored.get("person") or {}
    return {"candidate_id": stored["id"], "kind": stored["kind"], "person_id": stored.get("person_id"),
            "name_en": person.get("name_en") or "", "name_zh": person.get("name_zh") or "",
            "announced_at": stored.get("announced_at"), "effective_at": stored.get("effective_at"),
            "ended_roles": copy.deepcopy(stored.get("ended_roles") or []), "new_roles": copy.deepcopy(stored.get("new_roles") or []),
            "other_current_roles": stored.get("other_current_roles") if isinstance(stored.get("other_current_roles"), list) else None,
            "new_appointment": stored.get("new_appointment") if isinstance(stored.get("new_appointment"), dict) else None,
            "new_appointment_checked": bool(stored.get("new_appointment_checked")),
            "headline_zh": stored.get("headline") or "", "summary_zh": stored.get("summary") or "",
            "sources": [{k: src.get(k) for k in keep} for src in stored.get("sources") or []],
            "bureau_hint": next((b for b in stored.get("bureaus") or [] if b in bureaus.ACTIVE_BUREAUS), "none")}


def recheck(ids: list[str], ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """已入库的动态按现行核查规则重核（程序核查，过不了的一次一条开核查会话），不重新写回。
    通过的更新动态的核查记录（留下原记录）；没通过的列出来，交维护者处理。返回每条 {id, status, reasons, corrections}。"""
    store, today = ctx["store"], ctx["today"]
    stored = {i: store.event(i) for i in ids}
    missing = [i for i, e in stored.items() if e is None]
    if missing:
        raise SystemExit(f"库里没有这些动态：{'、'.join(missing)}")
    out: dict[str, Any] = {"gated": [], "stats": {}}
    runs = ctx["runs"] / "recheck"
    runs.mkdir(parents=True, exist_ok=True)
    verify_one_by_one("recheck", [to_research_event(e) for e in stored.values()], ctx, runs, out)
    results = []
    for g in out["gated"]:
        ev = stored[g["event"]["candidate_id"]]
        old = ev.get("verification") or {}
        results.append({"id": ev["id"], "status": g["status"], "reasons": g["reasons"], "corrections": g["corrections"],
                        "method": g.get("method"), "precheck": g.get("precheck")})
        if g["status"] != "verified":
            continue
        method = g.get("method") or "codex"
        ev["verification"] = {
            "status": "verified", "method": method, "checked_at": today, "run": ctx["run_id"],
            "by": ("程序核查：逐条取来源网页，一级来源的摘录逐字找到，人物、职务、类别、日期都在核实来源里" if method == "excerpt"
                   else "本机 Codex 核查会话（重核，一次一条）"),
            "model": None if method == "excerpt" else f"{ctx['model']}/{ctx['effort']}",
            "corrections": g["corrections"], "reasons": [], **({"precheck": g["precheck"]} if g.get("precheck") else {}),
            "rechecked": {"at": today, "previous": {k: old.get(k) for k in ("by", "run", "checked_at", "method")}}}
        known = {(src.get("url"), src.get("excerpt")) for src in ev.get("sources") or []}
        ev["sources"] = (ev.get("sources") or []) + [{**src, "checked_at": today} for src in g["event"].get("sources") or []
                                                     if (src.get("url"), src.get("excerpt")) not in known]
        store.save_event(ev)
    return results


# ──────────────────────────────────────────────────────────────── 第 5、6 步：获取简历、写回


def gather(results: list[dict[str, Any]], ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """放行的动态整理成要写回的记录（库还是写回前的样子）：二局范围外的、库里或同批已有的剔掉。
    返回 (按日期排好的记录, 搁置, 重复)。记录的研究原稿记在 ctx["origin_of"]（写回失败时进 held.json）。"""
    store = ctx["store"]
    records: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    dups: list[str] = []
    for res in results:
        for g in res["gated"]:
            if g["status"] == "duplicate":
                dups.append(f"{g['event'].get('headline_zh')}（已有 {g['duplicate_of']}）")
                continue
            if g["status"] != "verified":
                held.append(g)
                continue
            record = to_record(store, g["event"], g, ctx, ctx["names"], res["name"])
            why = not_collected(record, store, res["name"])
            if why:
                held.append({**g, "status": "out_of_scope", "reasons": [why]})
                continue
            dup = duplicate_of(store, record, records)
            if dup:
                dups.append(f"{record['headline']}（已有 {dup}）")
                continue
            ctx["origin_of"][record["id"]] = g
            records.append(record)
    records.sort(key=lambda r: (r.get("date") or "", r["id"]))
    return records, held, dups


def new_person_id(store: Store, person: dict[str, Any], taken) -> str:
    """名单新人在库里的编号：照英文名，和库里、这次已用的都不重。"""
    base = store.new_person_id(person.get("name_en") or person.get("name_zh") or "person")
    pid, n = base, 2
    while pid in set(taken):
        pid, n = f"{base}-{n}", n + 1
    return pid


def plan_units(records: list[dict[str, Any]], ctx: dict[str, Any], rosters: dict[str, dict[str, Any]],
               journal: localupdates.Journal | None) -> list[dict[str, Any]]:
    """要写回的一组一组（unit）：{record, changes, pid, fetch, attempts, pending}。

    本机更新时，在这批动态写回之前找名单变动（rosterchange.find）；本机更新目录里上次没取到简历的变动接着办
    （位置上已不是原任的，那处变动作废）。同一位置这批换了几次人的，只留最晚的一次。要上名单的新任，
    库里有完整简历卡和照片的直接用，没有的排上获取简历（fetch）。"""
    store, today, log = ctx["store"], ctx["today"], ctx["log"]
    units: list[dict[str, Any]] = []
    for entry in journal.pending() if journal is not None else []:
        record = entry["record"]
        dup = duplicate_of(store, record, records)
        if dup:
            log(f"上次留下的 {record['id']}：已有同一条 {dup}，不再办")
            journal.drop_pending(entry["id"])
            continue
        changes = [c for c in entry.get("changes") or [] if rosterchange.locate(rosters.get(c["section"]), c, c.get("old"))]
        units.append({"record": record, "changes": changes, "attempts": int(entry.get("attempts") or 0), "pending": entry["id"]})
    for record in records:
        changes = rosterchange.find(store, rosters, record, records, today) if journal is not None else []
        units.append({"record": record, "changes": changes, "attempts": 0, "pending": None})
    units.sort(key=lambda u: (u["record"].get("date") or "", u["record"]["id"]))
    last = {}
    for unit in units:
        for change in unit["changes"]:
            last[(change["section"], change["group"], change["index"])] = change
    taken: dict[str, str] = {}
    for unit in units:
        unit["changes"] = [c for c in unit["changes"] if last[(c["section"], c["group"], c["index"])] is c]
        unit["pid"], unit["fetch"] = None, None
        replace = [c for c in unit["changes"] if c["kind"] == "replace"]
        if not replace:
            continue
        person = unit["record"].get("person") or {}
        key = name_key(person.get("name_en") or "") or person.get("name_zh") or unit["record"]["id"]
        pid = replace[0].get("person_id") or taken.get(key) or new_person_id(store, person, taken.values())
        taken[key] = unit["pid"] = pid
        if rosterchange.ready(store.person(pid)):  # 库里没有完整简历卡或照片：取标准简历和官网照片
            unit["fetch"] = {"id": pid, "name_zh": person.get("name_zh") or "", "name_en": person.get("name_en") or "",
                             "bureau": None, "post": replace[0]["post_after"], "role": replace[0].get("role") or {}}
        for c in unit["changes"]:
            log(f"[名单] {c['section']}「{c['post_before']}」：{c.get('old')} → {pid}（{c['evidence']}）")
    return units


def fetch_resumes(units: list[dict[str, Any]], ctx: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """第 5 步：要上名单的新任人员，每人查一份标准简历和官网照片（resumes.fetch，两次会话）。停下以后不再开新会话。
    取过 RESUME_ATTEMPTS 次的不再取。返回 {人物编号: resumes.fetch 的结果}。"""
    halt, log, report = ctx["halt"], ctx["log"], ctx["report"]
    todo: dict[str, dict[str, Any]] = {}
    for unit in units:
        if unit["fetch"] and unit["attempts"] < RESUME_ATTEMPTS:
            todo.setdefault(unit["pid"], unit["fetch"])
    got: dict[str, dict[str, Any]] = {}
    if not todo or halt():
        return got
    report.step(5, people=len(todo), index=1, total=len(todo))
    for n, (pid, spec) in enumerate(todo.items(), 1):
        if halt():
            break
        report.detail(index=n)
        try:
            got[pid] = resumes.fetch(spec, post=spec["post"], role=spec["role"], today=ctx["today"], config=ctx["research_config"],
                                     runs=ctx["runs"] / "resumes" / pid, label=f"resume-{pid}", fetch_page=ctx["fetch"])
        except ai_api.ApiCancelled:
            break
        except FATAL as exc:
            halt.fail(exc)
            log(f"[简历] 停下：{exc}")
            break
        res = got[pid]
        log(f"[简历] {spec['name_zh'] or spec['name_en']}：" +
            ("取得标准简历和官网照片" if res["status"] == "ok" else "没取到：" + "；".join(res["reasons"][:3])))
    return got


def write_units(units: list[dict[str, Any]], ctx: dict[str, Any], rosters: dict[str, dict[str, Any]],
                journal: localupdates.Journal | None, got: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """第 6 步：一组一组落盘（write_unit）。换人而新任没有取到简历和照片的，这一组都不写，留在本机更新目录。
    返回 {applied: 写进的动态, failed: 写回失败的, pending: 留着的, roster: 名单变动, people: 写过的人物编号}。"""
    store, report, log, today = ctx["store"], ctx["report"], ctx["log"], ctx["today"]
    listed = rosterchange.roster_people(rosters) | {u["pid"] for u in units if u["pid"]}
    report.step(6, people=len({u["pid"] or rosterchange.person_of(store, u["record"]) for u in units} & listed))
    out: dict[str, Any] = {"applied": [], "failed": [], "pending": [], "roster": [], "people": set()}

    def keep(unit, reasons, fetched):
        entry = {"id": unit["record"]["id"], "record": unit["record"], "changes": unit["changes"], "pid": unit["pid"],
                 "attempts": unit["attempts"] + (1 if fetched else 0), "reasons": reasons, "at": today}
        journal.save_pending(entry)
        out["pending"].append(entry)
        log(f"[名单] {unit['record'].get('headline') or unit['record']['id']}：这一组没有写，留待下次（{'；'.join(reasons[:3])}）")

    for unit in units:
        replace = [c for c in unit["changes"] if c["kind"] == "replace"]
        fetched = got.get(unit["pid"]) if unit["fetch"] else None
        if replace and unit["fetch"] and (fetched or {}).get("status") != "ok":
            if fetched:
                why = fetched["reasons"]
            elif unit["attempts"] >= RESUME_ATTEMPTS:
                why = [f"取过 {RESUME_ATTEMPTS} 次都没取到标准简历和官网照片，交维护者复核名单"]
            else:
                why = ["更新停下，没有取简历"]
            keep(unit, why, fetched is not None)
            continue
        result = write_unit(unit, ctx, rosters, journal, fetched)
        if result["status"] == "pending":
            keep(unit, result["reasons"], fetched is not None)
        elif result["status"] == "failed":
            origin = ctx["origin_of"].get(unit["record"]["id"]) or {"event": unit["record"]}
            out["failed"].append({"event": origin["event"], "reasons": result["reasons"], "status": "failed", "corrections": []})
        else:
            out["applied"].append(unit["record"])
            out["roster"] += result["changes"]
            out["people"] |= result["people"]
    return out


def write_unit(unit: dict[str, Any], ctx: dict[str, Any], rosters: dict[str, dict[str, Any]],
               journal: localupdates.Journal | None, fetched: dict[str, Any] | None) -> dict[str, Any]:
    """一组落盘：这条动态写回人物记录；换人的，新任的标准简历和官网照片装进人物记录、名单换人，和动态一起写
    （rosterchange.commit），中途出错全部退回原样；本机更新时这一组另记进本机更新目录。
    返回 {status: applied、pending（新任还不能上名单，或写入出错，留待下次）或 failed（写回规则不收）, reasons, changes, people}。"""
    store, today = ctx["store"], ctx["today"]
    record = unit["record"]
    original = copy.deepcopy(record)
    saved: dict[str, dict[str, Any] | None] = {}

    def keep(pid: str | None) -> None:
        if pid and pid not in saved:
            saved[pid] = copy.deepcopy(store.person(pid))

    def undo(status: str, reasons: list[str]) -> dict[str, Any]:
        for pid, rec in saved.items():
            if rec is None:
                store._people.pop(pid, None)
            else:
                store._people[pid] = rec
        record.clear()
        record.update(original)
        return {"status": status, "reasons": reasons, "changes": [], "people": set()}

    if unit["pending"] and store.event(record["id"]) is not None:  # 上次留下的：编号已被维护者的另一条占了
        record["id"] = store.new_event_id(record["date"], record.get("person_id") or slugify(record["person"].get("name_en") or "", "person"),
                                          record["kind"])
    pid = unit["pid"]
    if fetched is not None:
        keep(pid)
        current = store.person(pid)
        rec = copy.deepcopy(current) if current else personnel.new_record(store, record["person"].get("name_zh") or "",
                                                                            record["person"].get("name_en") or "", today)
        rec["id"] = pid
        if current is not None and resume.card_type(current)[0]:
            rec["portrait"] = rec.get("portrait") if portraits.display_src(rec) else dict(fetched["portrait"])  # 简历照旧，补照片
        else:
            errors = resumes.install(rec, fetched["item"], today, fetched["portrait"])
            if errors:
                return undo("pending", [f"标准简历装不进人物记录：{'；'.join(errors[:3])}"])
        store._people[pid] = rec
    if pid:
        record["person_id"] = pid
    keep(rosterchange.person_of(store, record))
    result = personnel.apply_event(store, record, today, save=False)
    if result["status"] == personnel.FAILED:
        return undo("failed", result["notes"])
    pid = record["person_id"]
    saved.setdefault(pid, None)  # 写回时新建的人物记录
    record["bureaus"] = sorted({*record["bureaus"], *(b for b in (result.get("bureau_before"), result.get("bureau_after")) if b)})
    person = store.person(pid)
    if any(c["kind"] == "replace" for c in unit["changes"]):
        # 上名单的要够格：完整简历卡、有照片；这次取到的标准简历另要过写法检查（库里原有的记录照维护者的）
        problems = resumes.ready(person) if fetched is not None else rosterchange.ready(person)
        if problems:
            return undo("pending", problems)
    staged: dict[str, dict[str, Any]] = {}
    changes = []
    for change in unit["changes"]:
        section = change["section"]
        staged.setdefault(section, copy.deepcopy(rosters[section]))
        party = resume.value(person["resume"]["person"].get("party")) if section == "congress" else None
        if rosterchange.place(staged, change, pid, party or None):
            changes.append({k: change.get(k) for k in ("kind", "section", "group", "index", "old", "post_before", "post_after",
                                                       "state", "evidence", "event", "date")} | {"new": pid, "event": record["id"]})
        else:
            ctx["log"](f"[名单] {section}「{change['post_before']}」上已不是 {change.get('old')}，这处不换")
    path = store.events_dir / (record.get("date") or "0000-00")[:7] / f"{record['id']}.json"
    writes: dict[Path, Any] = {store.people_dir / f"{p}.json": store.person(p) for p in saved if store.person(p) is not None}
    writes[path] = record
    for section in {c["section"] for c in changes}:
        writes[rosterchange.path_of(store.root, section)] = staged[section]
    if journal is not None:
        writes[journal.event_path(record["id"])] = {**record, **({"roster": changes} if changes else {})}
        if fetched is not None:
            writes[journal.person_path(pid)] = person  # 本机取到的标准简历：维护者数据换新后按合并规则重放
    try:
        rosterchange.commit(writes)
    except Exception as exc:  # noqa: BLE001  一组写不进去就整组退回，不留半成品；本机更新时留着下次再写
        return undo("pending" if journal is not None else "failed", [f"写入出错：{type(exc).__name__}：{exc}"])
    store._events[record["id"]] = record
    store._event_paths[record["id"]] = path
    for section in {c["section"] for c in changes}:
        rosters[section] = staged[section]
    if unit["pending"] and journal is not None:
        journal.drop_pending(unit["pending"])
    return {"status": "applied", "reasons": [], "changes": changes, "people": {p for p in saved if store.person(p) is not None}}


class Stopped(Exception):
    """使用者停止了更新（刷新市值核对来源页时）。"""


def refresh_caps(ctx: dict[str, Any], root: Path) -> dict[str, Any]:
    """商界名单的市值（marketcap.refresh，用这次更新的同一个后端）：写回之后、第 6 步里做，不另报一步。
    结果只记日志和运行记录，不影响这次更新算不算完成。停下了就不开始；会话中途停下，接口上的会话取消，
    核对来源页时停下也不往下走，市值文件整份不写（查不齐、核不过时 marketcap 本来就整份不写）。"""
    halt, log = ctx["halt"], ctx["log"]
    runs = ctx["runs"] / "marketcap"
    if halt():
        result = {"ok": False, "written": False, "problems": ["更新停下，没有刷新市值"]}
    else:
        def fetch(url: str) -> str | None:
            if halt():
                raise Stopped
            return ctx["fetch"](url)

        runs.mkdir(parents=True, exist_ok=True)
        try:
            result = marketcap.refresh(root, ctx["research_config"], runs, today=ctx["today"], call=research.call, fetch=fetch)
        except (ai_api.ApiCancelled, Stopped):
            result = {"ok": False, "written": False, "problems": ["更新停下，市值文件没有改"]}
        except FATAL as exc:
            result = {"ok": False, "written": False, "problems": [f"刷新市值停下：{reason(exc)}"]}
        except Exception as exc:  # noqa: BLE001  市值刷新出了意外也不影响这次更新
            result = {"ok": False, "written": False, "problems": [f"刷新市值出错：{type(exc).__name__}：{exc}"]}
        write_json(runs / "result.json", result)
    log("商界市值：" + (f"已刷新到 {result['date']} 收盘" if result.get("written")
                     else "没有改（" + "；".join(result.get("problems") or [])[:400] + "）"))
    return result


def article_ids(store: Store, today: str, log: Log) -> set[str] | None:
    """人事动态栏目里的稿件编号（zuzhibu/renmian.py 的 articles，界面显示的就是这些）。
    算不出来时记日志、返回 None，新增稿件数报 0；写回照常。"""
    try:
        return {a["id"] for a in renmian.articles(store, today)}
    except Exception as exc:  # noqa: BLE001  稿件数只用在提示里，算不出来不影响写回
        log(f"人事动态稿件没算出来：{type(exc).__name__}：{exc}")
        return None


# ──────────────────────────────────────────────────────────────── 报告


def write_report(path: Path, ctx: dict[str, Any], groups: list[dict[str, Any]], applied: list[dict[str, Any]],
                 held: list[dict[str, Any]], dups: list[str], roster: list[dict[str, Any]] = (),
                 pending: list[dict[str, Any]] = (), caps: dict[str, Any] | None = None) -> None:
    via = f"接口：{ctx['api_label']}；" if ctx.get("backend") == "api" else ""
    lines = [f"# 一局更新 {ctx['run_id']}", "",
             f"窗口：{ctx['since']} 至 {ctx['until']}；来源：{ctx['origin']}；{via}模型：{ctx['model']}/{ctx['effort']}", ""]
    for g in groups:
        st = g.get("stats") or {}
        pre = st.get("precheck")
        extra = (f"先剔除 {pre['screened']} 条；程序核查放行 {pre['passed']} 条，开核查会话 {pre['to_session']} 次；"
                 if pre else "")
        lines.append(f"- {g['name']}：线索 {g['leads']}；研究检索 {st.get('research', {}).get('searches', 0)} 次；{extra}"
                     f"核查检索 {st.get('verify', {}).get('searches', 0)} 次；{g.get('error') or '完成'}")
    how = {"excerpt": "程序核查", "codex": "核查会话", "api": "核查会话"}
    lines += ["", "## 写回", ""]
    for a in applied:
        r = a["applied"]
        lines.append(f"- {a['date']}｜{a['headline']}｜{personnel.STATUS_LABEL.get(r['status'], r['status'])}｜"
                     f"{bureaus.label(r.get('bureau_before'))} → {bureaus.label(r.get('bureau_after'))}｜"
                     f"{how.get(a['verification'].get('method'), '核查会话')}")
        lines += [f"  - {c}" for c in r["changes"]] + [f"  - 注：{n}" for n in r["notes"]]
    if roster:
        lines += ["", "## 名单变动", ""]
        lines += [f"- {c['section']}「{c['post_before']}」→「{c['post_after']}」：{c.get('old')} → {c['new']}（{c['evidence']}；"
                  f"动态 {c['event']}）" for c in roster]
    if pending:
        lines += ["", "## 留待下次（新任还没有标准简历和官网照片，这一组没有写）", ""]
        lines += [f"- {p['record'].get('headline') or p['id']}：{'；'.join(p['reasons'][:3])}（已取 {p['attempts']} 次）"
                  for p in pending]
    if caps is not None:
        lines += ["", "## 商界市值", ""]
        lines += [f"- 已刷新到 {caps['date']} 收盘" + (f"（来源页打不开、没有核对：{'、'.join(caps['unreachable'])}）"
                                                    if caps.get("unreachable") else "")] if caps.get("written") \
            else [f"- 没有改：{p}" for p in caps.get("problems") or []]
    lines += ["", "## 搁置", ""]
    for h in held:
        why = "；".join(h["reasons"])
        if h.get("precheck"):
            why += f"（程序核查没过：{'；'.join(h['precheck'])}）"
        lines.append(f"- {h['event'].get('headline_zh') or h['event'].get('headline')}：{why}")
    if dups:
        lines += ["", "## 重复（已有动态，未再写）", ""] + [f"- {d}" for d in dups]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def last_cutoff(meta: dict[str, Any], journal: localupdates.Journal | None) -> str | None:
    """上次更新查到哪天：维护者每日更新、本机立即更新（meta 和本机更新目录里各记一份）里最晚的截止日。"""
    found = [((meta.get(key) or {}).get("last_window") or [None, None])[-1] for key in ("daily", "user_update")]
    found.append(journal.last_cutoff() if journal else None)
    found = [d for d in found if d]
    return max(found) if found else None


def window_days(last: str | None, today: str) -> int:
    """从上次截止日到今天的天数（同 daily.ps1）：最长 62 天，取不到上次的就 31 天。"""
    try:
        return min(62, max(1, (dt.date.fromisoformat(today) - dt.date.fromisoformat(last or "")).days + 1))
    except ValueError:
        return 31


def api_config(args: argparse.Namespace) -> ai_api.ApiConfig:
    """读使用者设置里的 AI 接口（密钥只在这里读进内存，不进命令行、日志和数据）。"""
    from zuzhibu.server import settings_path  # 设置文件的位置与设置页保存的相同

    path = Path(args.settings) if args.settings else settings_path()
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = {}
    return ai_api.config_from_settings(settings.get("api") if isinstance(settings, dict) else None,
                                       deadline=args.timeout * 60, max_searches=max(60, 2 * args.min_searches))


# ──────────────────────────────────────────────────────────────── 主流程


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="一局更新")
    ap.add_argument("--daily", action="store_true", help="窗口取近 --days 天，到今天为止")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--since-last", action="store_true",
                    help="窗口从上次更新的截止日到今天（最长 62 天，取不到就 31 天）；窗口超过 7 天时研究会话最少检索 40 次")
    ap.add_argument("--since"), ap.add_argument("--until")
    ap.add_argument("--groups", default=",".join(research.GROUPS),
                    help="研究分组，逗号分隔：whitehouse、ai、congress、military、justice（资料库五栏）；gov 是白宫、国防、司法三组")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--backend", choices=("codex", "api"), default="codex",
                    help="codex：本机已登录的 Codex；api：使用者在设置里填的 AI 接口，用使用者自己的额度")
    ap.add_argument("--settings", help="--backend api 时读哪个设置文件，默认设置页保存的位置")
    ap.add_argument("--journal", help="本机更新目录（--backend api 时默认 %%LOCALAPPDATA%%\\zuzhibu\\local）；给了就做获取简历、名单换人")
    ap.add_argument("--no-probe", action="store_true", help="--backend api 时不先检测接口能不能联网检索")
    ap.add_argument("--progress", help="进度文件：设置里的进度条读它（zuzhibu/progress.py）")
    ap.add_argument("--stop-file", help="停止文件：出现后不再开新会话，已核实的照样写回")
    ap.add_argument("--model", default=research.DEFAULT_MODEL)
    ap.add_argument("--effort", default=research.DEFAULT_EFFORT)
    ap.add_argument("--codex")
    ap.add_argument("--timeout", type=float, default=30, help="每次会话限时，分钟")
    ap.add_argument("--min-searches", type=int, default=15)
    ap.add_argument("--origin", default=None, help="daily、backfill、user-api；默认 --daily 时为 daily，--backend api 时为 user-api")
    ap.add_argument("--data"), ap.add_argument("--runs", default=str(DEFAULT_RUNS))
    ap.add_argument("--date", help="今天的日期，默认本机日期")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-excerpt-check", action="store_true", help="不另取网页核对摘录")
    ap.add_argument("--inbox", help="补采的动态文件（格式同研究会话输出 RESEARCH_SCHEMA）：不开研究会话，只核查和写回")
    ap.add_argument("--recheck", help="已入库的动态编号（逗号分隔）：按现行规则重核，不重新写回")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    today = args.date or dt.date.today().isoformat()
    if args.recheck:
        return main_recheck(args, today)
    if not (args.inbox or args.daily or args.since_last or (args.since and args.until)):
        ap.error("给 --daily 或 --since-last，或同时给 --since 和 --until")
    report = progress.Progress(args.progress, args.stop_file)
    try:
        code, state, error = update(args, today, report)
    except BaseException:
        report.finish("failed", "程序出错")
        raise
    report.finish(state, error)
    return code


def update(args: argparse.Namespace, today: str, report: progress.Progress) -> tuple[int, str, str | None]:
    """一次更新，按 6 步上报进度。返回 (退出码, 结束状态 done、stopped 或 failed, 未完成的原因)。"""
    use_api = args.backend == "api"
    journal = localupdates.Journal(args.journal) if use_api or args.journal else None  # 本机的立即更新：写进的另记一份
    inbox = None
    if args.inbox:
        data = json.loads(Path(args.inbox).read_text(encoding="utf-8"))
        problems = schema_lite.validate(data, research.RESEARCH_SCHEMA)
        if problems:
            print("补采动态文件不合格式：" + "；".join(problems[:5]))
            return 5, "failed", "补采动态文件不合格式"
        inbox = data["events"]
        dates = sorted((e.get("effective_at") or e.get("announced_at") or today)[:10] for e in inbox) or [today]
        args.since, args.until = dates[0], dates[-1]
    if args.since_last and inbox is None:
        meta_path = Path(Store(args.data).meta_path)
        meta = read_json(meta_path) if meta_path.exists() else {}
        args.daily, args.days = True, window_days(last_cutoff(meta, journal), today)
        if args.days > 7:
            args.min_searches = max(args.min_searches, 40)  # 窗口长，研究会话多检索
    if args.daily and inbox is None:
        since = (dt.date.fromisoformat(today) - dt.timedelta(days=args.days)).isoformat()
        until = today
    else:
        since, until = args.since, args.until
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    runs = Path(args.runs) / run_id
    runs.mkdir(parents=True, exist_ok=True)
    log = Log(runs / "run.log")
    lock = Path(args.runs) / ".lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < LOCK_MAX_AGE and not args.dry_run:
        log(f"另一次更新正在运行（{lock}），本次退出")
        return 4, "failed", "另一次更新正在进行"
    store = Store(args.data).load()
    before = article_ids(store, today, log)  # 人事动态栏目更新前的稿件，更新后再取一次比对
    groups = parse_groups(args.groups)
    halt = Halt(report)
    report.step(1)
    api = None
    if use_api:
        try:
            api = dataclasses.replace(api_config(args), cancel=halt)
        except (ai_api.ApiError, ValueError) as exc:
            log(f"AI 接口不能用于一局更新：{exc}")
            return 2, "failed", reason(exc)
        log.secrets = (api.api_key,)
        model, effort = api.model, api.effort or "默认"
        research_config = verify_config = api
    else:
        try:
            codex = resolve_codex(args.codex) if not args.dry_run else (args.codex or "codex")
        except codex_cli.CodexNotFound as exc:
            log(str(exc))
            return 2, "failed", reason(exc)
        model, effort = args.model, args.effort
        research_config = research.web_config(codex, args.model, args.effort, args.timeout * 60)
        verify_config = research.web_config(codex, args.model, args.effort, args.timeout * 60)
    if not args.dry_run:
        lock.write_text(str(os.getpid()), encoding="utf-8")
    try:
        if api is not None and not args.dry_run and not args.no_probe:
            probe = ai_api.probe(api, runs / "probe")
            log(f"检测联网检索：{probe['message']}（{probe.get('detail') or ''}）")
            if not probe["ok"]:
                if halt.by_user:
                    return EXIT_STOPPED, "stopped", None
                return 2, "failed", ai_api.REASONS.get(probe.get("code") or "", ai_api.REASONS["error"])
        if journal is not None and not args.dry_run:
            merged = localupdates.merge(store, journal, today)  # 维护者数据换新后，先把本机以前写进的重放回去
            if merged["changed"]:
                log("本机更新重放：" + "，".join(f"{k} {merged[k]}" for k in ("kept", "replayed", "merged", "pending", "failed")))
        report.step(2)
        found = collect.collect(store, since, until, runs / "feeds") if inbox is None and not halt() else {"leads": [], "errors": []}
        leads = found["leads"]
        for c in leads:
            c["group"] = group_of(c, store)
        write_json(runs / "leads.json", found)
        log(f"窗口 {since} 至 {until}；线索 {len(leads)} 条；采集错误 {len(found['errors'])} 条 {found['errors'] or ''}")
        pages: dict[str, str | None] = {}

        def fetch(url: str) -> str | None:
            if url not in pages:
                pages[url] = research.fetch_page(url)
            return pages[url]

        origin = args.origin or ("codex-inbox" if inbox is not None else localupdates.ORIGIN if use_api
                                 else "daily" if args.daily else "backfill")
        rosters = rosterchange.load(store.root)
        counter = {"groups": 0, "verify": 0, "total": 0}
        step_lock = threading.Lock()

        def started(name: str) -> None:  # 第 3 步的“白宫 2/5”
            with step_lock:
                counter["groups"] += 1
                report.step(3, group=research.GROUP_LABELS.get(name, name), index=counter["groups"], total=len(groups))

        def tick() -> None:  # 第 4 步的“第 3 条，共 12 条”
            with step_lock:
                counter["verify"] += 1
                report.detail(index=min(counter["verify"], counter["total"]), total=counter["total"])

        ctx = {"today": today, "since": since, "until": until, "leads": leads, "store": store, "log": log, "runs": runs,
               "dry_run": args.dry_run, "min_searches": args.min_searches, "model": model, "effort": effort,
               "origin": origin, "backend": args.backend, "api_label": api.label if api else "",
               "run_id": run_id, "inbox": inbox, "names": glossary_names(), "pages": pages, "fetch": fetch,
               "excerpt_check": not args.no_excerpt_check, "research_config": research_config, "verify_config": verify_config,
               "halt": halt, "report": report, "started": started, "tick": tick, "roster_text": roster_text(store, rosters),
               "origin_of": {}}
        # 第 3 步：研究
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
            results = list(pool.map(lambda g: research_group(g, ctx), groups))
        if args.dry_run:
            log(f"dry-run：提示词在 {runs}")
            return 0, "done", None
        # 第 4 步：逐条核实
        counter["total"] = sum(len(r["events"]) for r in results)
        report.step(4, index=1 if counter["total"] else 0, total=counter["total"])
        verify_all(results, ctx, args.jobs)
        if ctx["excerpt_check"]:
            # 摘录核对并行取网页（程序核查取过的不再取），结果按（链接，摘录）缓存，写回时直接用
            pairs = {(s.get("url", ""), s.get("excerpt", "")) for res in results for g in res["gated"] if g["status"] == "verified"
                     for s in g["event"].get("sources") or []}
            prefetch([u for u, _ in pairs], ctx)
            ctx["excerpt_cache"] = {pair: research.excerpt_check(*pair, fetch=fetch) for pair in pairs}
        records, held, dups = gather(results, ctx)
        # 第 5 步：获取简历（只在本机更新时）；第 6 步：写回，每月更新和立即更新接着刷新商界市值（不另报一步）
        units = plan_units(records, ctx, rosters if journal is not None else {}, journal)
        got = fetch_resumes(units, ctx) if journal is not None else {}
        written = write_units(units, ctx, rosters, journal, got)
        caps = refresh_caps(ctx, store.root) if inbox is None and (args.daily or use_api) else None
        applied, held = written["applied"], held + written["failed"]
        write_json(runs / "held.json", held)
        meta = importer.write_meta(store, today)  # 重算各局人数和资料截止日期
        failed = [r for r in results if r.get("error")]
        stopped = halt.by_user
        if halt.error is not None:
            code, state, error, label = 2, "failed", reason(halt.error), "未完成"
        elif stopped:
            code, state, error, label = EXIT_STOPPED, "stopped", None, "已停止"
        elif failed:
            code, state, error, label = 3, "failed", failed[0].get("reason") or "接口出错", "部分失败"
        else:
            code, state, error, label = 0, "done", None, "完成"
        status = {"last_run": dt.datetime.now().isoformat(timespec="seconds"), "last_status": label,
                  "last_applied": len(applied), "last_held": len(held), "last_run_id": run_id}
        if inbox is not None:
            meta["inbox"] = {**status, "last_file": Path(args.inbox).as_posix(), "last_duplicates": len(dups)}  # 不顶替每日更新状态
        elif journal is not None:
            # 使用者的立即更新：不顶替维护者的每日更新状态；没跑完的截止日不往前推，下次把这段重查一遍
            done = {"last_window": [since, until]} if code == 0 else {}
            state_now = journal.save_state(**status, **done, last_pending=len(written["pending"]),
                                           backend=api.label if api else "本机 Codex", model=model)
            meta["user_update"] = {**(meta.get("user_update") or {}), **state_now}
        else:
            meta.setdefault("daily", {}).update(status, last_window=[since, until])
        store.save_meta(meta)
        listed = rosterchange.roster_people(rosterchange.load(store.root))
        hidden = collections.Counter(why for ok, why in (renmian.is_publishable(r, store, today) for r in applied) if not ok)
        after = article_ids(store, today, log)
        added = len(after - before) if after is not None and before is not None else 0
        # 提示里的“新增人事动态 n 条”“已核实的 n 条”：人事动态栏目里新出现的稿件；“更新简历 n 份”：写过的名单人物
        report.result(events=added, resumes=len(written["people"] & listed))
        write_report(runs / "report.md", ctx, results, applied, held, dups, written["roster"], written["pending"], caps)
        log(f"{label}：写入动态 {len(applied)} 条（" + "".join(f"{why} {n} 条不公开，" for why, n in hidden.items())
            + f"人事动态新增稿件 {added} 篇），名单变动 {len(written['roster'])} 处，留待下次 {len(written['pending'])} 组，"
            f"搁置 {len(held)} 条，重复 {len(dups)} 条；报告 {runs / 'report.md'}")
        return code, state, error
    finally:
        if not args.dry_run and lock.exists():
            lock.unlink()


def main_recheck(args: argparse.Namespace, today: str) -> int:
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    runs = Path(args.runs) / run_id
    runs.mkdir(parents=True, exist_ok=True)
    log = Log(runs / "run.log")
    lock = Path(args.runs) / ".lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < LOCK_MAX_AGE:
        log(f"另一次更新正在运行（{lock}），本次退出")
        return 4
    try:
        codex = resolve_codex(args.codex)
    except codex_cli.CodexNotFound as exc:
        log(str(exc))
        return 2
    lock.write_text(str(os.getpid()), encoding="utf-8")
    try:
        pages: dict[str, str | None] = {}

        def fetch(url: str) -> str | None:
            if url not in pages:
                pages[url] = research.fetch_page(url)
            return pages[url]

        ctx = {"today": today, "store": Store(args.data).load(), "log": log, "runs": runs, "run_id": run_id, "model": args.model,
               "effort": args.effort, "excerpt_check": not args.no_excerpt_check, "pages": pages, "fetch": fetch, "inbox": None,
               "verify_config": research.web_config(codex, args.model, args.effort, args.timeout * 60)}
        results = recheck([i.strip() for i in args.recheck.split(",") if i.strip()], ctx)
        write_json(runs / "recheck.json", results)
        lines = [f"# 重核 {run_id}", ""]
        for r in results:
            line = f"- {r['id']}｜{'通过' if r['status'] == 'verified' else '没通过'}｜{'程序核查' if r['method'] == 'excerpt' else '核查会话'}"
            if r["reasons"]:
                line += "｜原因：" + "；".join(r["reasons"])
            if r["corrections"]:
                line += "｜更正：" + "；".join(r["corrections"])
            lines.append(line)
        (runs / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        held = [r["id"] for r in results if r["status"] != "verified"]
        log(f"重核完成：通过 {len(results) - len(held)} 条，没通过 {len(held)} 条 {held or ''}；报告 {runs / 'report.md'}")
        return 0
    except (codex_cli.CodexAuthError, codex_cli.CodexNotFound) as exc:
        log(f"Codex 未登录或找不到：{exc}")
        return 2
    finally:
        if lock.exists():
            lock.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
