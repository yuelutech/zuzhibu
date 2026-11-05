"""补采收件箱：外部会话（本机 Codex 等）补采的结果先放 inbox/codex/<任务名>/，检查过再入库。

任务文件夹里按需有（格式见 inbox/README.md）：

    README.md               做了什么、没做完什么
    roster.csv              库里没有的现任人员（新增名册行）
    resumes/<编号>.json     干部简历事实包，格式见 shared/schema/bundle.schema.json
    events.json             人事动态，格式同一局研究会话输出（bureaus/dispatch/research.py 的 RESEARCH_SCHEMA）
    names.csv               中文名（译名表里没有的人）
    portraits.csv           照片链接

    python tools/inbox.py check inbox/codex/<任务名>            # 检查：补采方自查、维护者复核都用它
    python tools/inbox.py check inbox/codex/<任务名> --fetch    # 另取网页核对来源摘句（联网，维护者复核用）
    python tools/inbox.py months inbox/codex/<任务名>           # 履历起止只有年份、成稿要全篇写到月的，按推测规则补月份
    python tools/inbox.py import inbox/codex/<任务名>           # 入库：名册、事实包、中文名、照片
    python -m bureaus.dispatch.run --inbox inbox/codex/<任务名>/events.json   # 动态另走一局核查和写回

检查只读，不改任何文件；有错误退出码 1。入库把名册行并进各局名册、事实包放进各局 data/verified/、
中文名记进 shared/glossary/补充译名_person.csv（待审）、照片下载做成小图，再跑一遍首发导入。只用标准库。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bureaus.dispatch import research  # noqa: E402
from shared.backends import schema_lite  # noqa: E402
from zuzhibu import bureaus, importer, personnel, resume, sources  # noqa: E402
from zuzhibu.store import Store, name_key, read_json, slugify, write_json  # noqa: E402

ROSTER_COLUMNS = ["person_id", "bureau", "english_name", "position_en", "organization_en", "since",
                  "official_bio_url", "official_portrait_url", "notes"]
NAME_COLUMNS = ["person_id", "english_name", "name_zh", "basis", "source_url"]
PORTRAIT_COLUMNS = ["person_id", "image_url", "source_page", "license"]
ROSTER_BUREAUS = (bureaus.AI, bureaus.WHITEHOUSE, bureaus.MILITARY, bureaus.JUSTICE)
# 名册行和事实包按局放：二局 personnel-sv；三局 personnel-whitehouse；五局、六局 personnel-others；七局 retired
BUREAU_DIRS = {bureaus.AI: "personnel-sv", bureaus.WHITEHOUSE: "personnel-whitehouse", bureaus.MILITARY: "personnel-others",
               bureaus.JUSTICE: "personnel-others", bureaus.RETIRED: "retired"}
SUPPLEMENT = REPO / "shared" / "glossary" / "补充译名_person.csv"
SUPPLEMENT_COLUMNS = ["english_name", "中文名", "依据", "依据URL", "person_id", "任务", "日期", "status"]
_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.done: list[str] = []

    def error(self, where: str, msg: str) -> None:
        self.errors.append(f"{where}：{msg}")

    def warn(self, where: str, msg: str) -> None:
        self.warnings.append(f"{where}：{msg}")

    def text(self) -> str:
        lines = [*self.done, ""]
        lines += [f"错误 {len(self.errors)} 条（须改）"] + [f"  ✗ {e}" for e in self.errors]
        lines += [f"提示 {len(self.warnings)} 条（看情况）"] + [f"  · {w}" for w in self.warnings]
        return "\n".join(lines)


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [{k.strip(): (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(fh)]


def _columns(path: Path, need: list[str], rep: Report) -> list[dict[str, str]] | None:
    rows = _csv(path)
    with path.open(encoding="utf-8-sig", newline="") as fh:
        header = [h.strip() for h in next(csv.reader(fh), [])]
    missing = [c for c in need if c not in header]
    if missing:
        rep.error(path.name, f"缺列 {missing}；列须是 {','.join(need)}")
        return None
    return rows


def _http(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


# ──────────────────────────────────────────────────────────────── 检查


def check_roster(task: Path, store: Store, rep: Report) -> dict[str, dict[str, str]]:
    path = task / "roster.csv"
    if not path.exists():
        return {}
    rows = _columns(path, ROSTER_COLUMNS, rep)
    out: dict[str, dict[str, str]] = {}
    for n, row in enumerate(rows or [], 2):
        where = f"roster.csv 第 {n} 行（{row.get('english_name')}）"
        pid = row["person_id"]
        if row["bureau"] not in ROSTER_BUREAUS:
            rep.error(where, f"bureau 须是 {'/'.join(ROSTER_BUREAUS)}")
        for key in ("english_name", "position_en", "organization_en", "official_bio_url"):
            if not row.get(key):
                rep.error(where, f"{key} 不能空")
        if pid != slugify(row["english_name"]):
            rep.error(where, f"person_id 须是 {slugify(row['english_name'])}（英文名小写，空格和标点换成连字符）")
        if store.person(pid) or store.find_by_name(row["english_name"]):
            hit = store.person(pid) or store.find_by_name(row["english_name"])[0]
            rep.error(where, f"库里已有此人 {hit['id']}，已在库的人不进 roster.csv")
        if row["since"] and not _DATE.match(row["since"]):
            rep.error(where, "since 写 YYYY-MM-DD、YYYY-MM 或 YYYY")
        url = row.get("official_bio_url", "")
        if url and (not _http(url) or sources.tier_of(url) != 1):
            rep.error(where, f"official_bio_url 须是一级来源（官方网站）：{url}")
        if row.get("official_portrait_url") and not _http(row["official_portrait_url"]):
            rep.error(where, "official_portrait_url 须是网址")
        out[pid] = row
    rep.done.append(f"roster.csv：{len(out)} 人")
    return out


def _record_for(pid: str, item: dict[str, Any], store: Store, roster: dict[str, dict[str, str]]) -> dict[str, Any]:
    rec = store.person(pid)
    base = {"id": pid, "name_zh": (rec or {}).get("name_zh") or resume.value(item["person"].get("name")),
            "name_en": (rec or {}).get("name_en") or (roster.get(pid) or {}).get("english_name", ""),
            "bureau": (rec or {}).get("bureau") or (roster.get(pid) or {}).get("bureau")}
    return {**base, "resume": item}


def check_resumes(task: Path, store: Store, roster: dict[str, dict[str, str]], today: str, rep: Report,
                  names: dict[str, dict[str, str]]) -> list[str]:
    folder = task / "resumes"
    if not folder.is_dir():
        return []
    from tools.lint import cadre_lint
    glossary_lint = cadre_lint.load_glossary()
    glossary = importer.glossary_names()
    schema = personnel.schema()
    ok: list[str] = []
    full = 0
    for path in sorted(folder.glob("*.json")):
        pid = path.stem
        where = f"resumes/{path.name}"
        try:
            bundle = read_json(path)
        except (OSError, ValueError) as exc:
            rep.error(where, f"读不出 JSON：{exc}")
            continue
        problems = schema_lite.validate(bundle, schema)
        if problems:
            rep.error(where, "不合 bundle.schema.json：" + "；".join(problems[:4]))
            continue
        items = bundle["items"]
        if len(items) != 1 or items[0].get("line") != "cadre":
            rep.error(where, "items 只放一份干部简历（line 为 cadre）")
            continue
        item = items[0]
        rec = store.person(pid)
        if not rec and pid not in roster:
            rep.error(where, "库里没有这个编号，roster.csv 里也没有；新人先进 roster.csv")
            continue
        if rec and rec.get("bureau") == bureaus.CONGRESS:
            rep.error(where, "国会议员的简历走四局夜间重核，补采不收")
            continue
        if item.get("subject_id") != pid:
            rep.error(where, f"subject_id 须与文件名一致：{pid}")
        if not (str(bundle.get("date") or "") <= today):
            rep.error(where, f"date 晚于今天 {today}")
        by_id = {}
        for s in item.get("sources") or []:
            by_id[s.get("id")] = s
            if not _http(s.get("url") or ""):
                rep.error(where, f"来源 {s.get('id')} 没有网址")
            if (s.get("checked_at") or "") > today:
                rep.error(where, f"来源 {s.get('id')} checked_at 晚于今天")
        for n, role in enumerate(item.get("current_roles") or []):
            ids = role.get("source_ids") or []
            if not ids:
                rep.error(where, f"current_roles[{n}] 没有来源")
            for i in ids:
                if i not in by_id:
                    rep.error(where, f"current_roles[{n}] 引用了不存在的来源 {i}")
        name = resume.value(item["person"].get("name"))
        name_en = (rec or {}).get("name_en") or roster.get(pid, {}).get("english_name", "")
        if glossary.get(name_en) and name and name != glossary[name_en]:
            rep.error(where, f"中文名须照译名表写“{glossary[name_en]}”，现为“{name}”")
        if names.get(pid) and name and name != names[pid]["name_zh"]:
            rep.error(where, f"中文名与 names.csv 不一致：{name} / {names[pid]['name_zh']}")
        record = _record_for(pid, personnel.fill_defaults(json.loads(json.dumps(item)), item.get("country_profile") or "us"),
                             store, roster)
        kind, reasons = resume.card_type(record)
        if kind:
            full += 1
        else:
            rep.warn(where, "未达完整简历卡：" + "、".join(reasons))
        composed = resume.compose(record)
        # 补采可以保留事实缺项；只降级相应的缺项提示，已有值的格式错误仍拦截。
        missing_messages = {message for key, message in (
            ("gender", "首段缺性别"),
            ("nationality", "国籍写“××籍”，如“美国籍”"),
            ("birth_date", "首段缺出生年月"),
            ("work_start", "首段缺参加工作（入伍）时间"),
        ) if not resume.value(record["resume"]["person"].get(key))}
        for v in cadre_lint.lint_text(composed["text"], glossary=glossary_lint, country=composed["country"]):
            missing = not kind and v.code == "CR10" and v.message in missing_messages
            (rep.error if v.level == "error" and not missing else rep.warn)(
                where, f"成稿检查 {v.code} {v.rule}：{v.message}「{v.text}」")
        if rec:
            gone = [r for r in rec["resume"].get("current_roles") or []
                    if not any(personnel.same_role(r, b) for b in item.get("current_roles") or [])]
            if gone:
                rep.warn(where, "库里的现任职务事实包里没有：" + "；".join(resume.role_text(r) for r in gone)
                         + "。如已离任，events.json 里要有离任动态")
        ok.append(pid)
    rep.done.append(f"resumes/：{len(ok)} 份可入库，其中够完整简历卡 {full} 份")
    return ok


def check_events(task: Path, store: Store, today: str, rep: Report) -> int:
    path = task / "events.json"
    if not path.exists():
        return 0
    try:
        data = read_json(path)
    except (OSError, ValueError) as exc:
        rep.error("events.json", f"读不出 JSON：{exc}")
        return 0
    problems = schema_lite.validate(data, research.RESEARCH_SCHEMA)
    if problems:
        rep.error("events.json", "不合 RESEARCH_SCHEMA：" + "；".join(problems[:5]))
        return 0
    for n, ev in enumerate(data["events"]):
        where = f"events.json 第 {n + 1} 条（{ev.get('headline_zh')}）"
        if ev.get("person_id") and not store.person(ev["person_id"]):
            rep.error(where, f"person_id {ev['person_id']} 库里没有；库外新人填 null")
        if not ev.get("person_id") and store.find_by_name(ev.get("name_en") or ""):
            rep.warn(where, f"库里已有同名人物 {store.find_by_name(ev['name_en'])[0]['id']}，person_id 应填编号")
        for key in ("announced_at", "effective_at"):
            if ev.get(key) and (not _DATE.match(ev[key]) or ev[key] > today):
                rep.error(where, f"{key} 须是不晚于今天的 YYYY-MM-DD 或 YYYY-MM")
        if not ev.get("sources"):
            rep.error(where, "没有来源")
        ids = {s["id"] for s in ev.get("sources") or []}
        for s in ev.get("sources") or []:
            if not _http(s.get("url") or ""):
                rep.error(where, f"来源 {s.get('id')} 没有网址")
        for key in ("ended_roles", "new_roles"):
            for r in ev.get(key) or []:
                if not r.get("source_ids") or any(i not in ids for i in r["source_ids"]):
                    rep.error(where, f"{key} 里的职务要引用本条来源的编号")
        if ev["kind"] in ("leave", "death") and ev.get("other_current_roles") is None:
            rep.warn(where, "离任、逝世要查明其他现职（other_current_roles 填列表，没有就填 []），否则不能转七局")
    rep.done.append(f"events.json：{len(data['events'])} 条（入库走 python -m bureaus.dispatch.run --inbox）")
    return len(data["events"])


def check_names(task: Path, store: Store, roster: dict[str, dict[str, str]], rep: Report) -> dict[str, dict[str, str]]:
    path = task / "names.csv"
    if not path.exists():
        return {}
    rows = _columns(path, NAME_COLUMNS, rep)
    glossary = importer.glossary_names()
    out = {}
    for n, row in enumerate(rows or [], 2):
        where = f"names.csv 第 {n} 行（{row.get('english_name')}）"
        pid = row["person_id"]
        if not store.person(pid) and pid not in roster:
            rep.error(where, "编号库里没有，roster.csv 里也没有")
        if not row["name_zh"] or not row["basis"] or not _http(row["source_url"]):
            rep.error(where, "name_zh、basis、source_url 都要填")
        if glossary.get(row["english_name"]):
            rep.error(where, f"译名表已有“{glossary[row['english_name']]}”，照表用，不另报")
        if " " in row["english_name"] and "·" not in row["name_zh"] and not re.search(r"[一-鿿]{2,4}$", row["name_zh"]):
            rep.warn(where, "西文名的姓名之间用“·”")
        out[pid] = row
    rep.done.append(f"names.csv：{len(out)} 人")
    return out


def check_portraits(task: Path, store: Store, roster: dict[str, dict[str, str]], rep: Report) -> int:
    path = task / "portraits.csv"
    if not path.exists():
        return 0
    rows = _columns(path, PORTRAIT_COLUMNS, rep)
    for n, row in enumerate(rows or [], 2):
        where = f"portraits.csv 第 {n} 行（{row.get('person_id')}）"
        if not store.person(row["person_id"]) and row["person_id"] not in roster:
            rep.error(where, "编号库里没有，roster.csv 里也没有")
        if not _http(row["image_url"]) or not _http(row["source_page"]) or not row["license"]:
            rep.error(where, "image_url、source_page 要是网址，license 要填")
    rep.done.append(f"portraits.csv：{len(rows or [])} 张")
    return len(rows or [])


def check_excerpts(task: Path, rep: Report) -> None:
    """取网页原文核对摘句：逐字找到的算核实；找不到的列出来；网页取不到的（403、超时）只计数。"""
    pairs: list[tuple[str, str, str]] = []
    for path in sorted((task / "resumes").glob("*.json")) if (task / "resumes").is_dir() else []:
        try:
            item = read_json(path)["items"][0]
        except (OSError, ValueError, KeyError, IndexError):
            continue
        pairs += [(f"resumes/{path.name} {s.get('id')}", s.get("url") or "", s.get("excerpt") or "") for s in item.get("sources") or []]
    if (task / "events.json").exists():
        try:
            events = read_json(task / "events.json").get("events") or []
        except (OSError, ValueError):
            events = []
        for n, ev in enumerate(events, 1):
            pairs += [(f"events.json 第 {n} 条 {s.get('id')}", s.get("url") or "", s.get("excerpt") or "") for s in ev.get("sources") or []]
    unique = sorted({(u, e) for _, u, e in pairs if u and e.strip()})
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        result = dict(zip(unique, pool.map(lambda ue: research.excerpt_check(*ue), unique)))
    found = sum(1 for v in result.values() if v)
    missing = [(w, u) for w, u, e in pairs if result.get((u, e)) is False]
    blocked = sum(1 for v in result.values() if v is None)
    for where, url in missing:
        rep.warn(where, f"摘句在网页上没找到：{url}")
    rep.done.append(f"来源摘句：{len(unique)} 处，网页上逐字找到 {found}，没找到 {sum(1 for v in result.values() if v is False)}，网页取不到 {blocked}")


def check(task: Path, store: Store, today: str, fetch: bool = False) -> tuple[Report, dict[str, Any]]:
    rep = Report()
    if not task.is_dir():
        rep.error(str(task), "文件夹不存在")
        return rep, {}
    roster = check_roster(task, store, rep)
    names = check_names(task, store, roster, rep)
    ok = check_resumes(task, store, roster, today, rep, names)
    check_events(task, store, today, rep)
    check_portraits(task, store, roster, rep)
    if fetch:
        check_excerpts(task, rep)
    if not (task / "README.md").exists():
        rep.warn("README.md", "没有任务说明：写清做了什么、没做完什么")
    return rep, {"roster": roster, "names": names, "resumes": ok}


# ──────────────────────────────────────────────────────────────── 入库


def _append_csv(path: Path, columns: list[str], rows: list[dict[str, str]]) -> None:
    exists = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if exists:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            columns = next(csv.reader(fh))
    if exists and not path.read_bytes().endswith(b"\n"):
        with path.open("a", encoding="utf-8", newline="") as fh:
            fh.write("\n")
    with path.open("a", encoding="utf-8-sig" if not exists else "utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n", extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerows(rows)


def import_task(task: Path, store: Store, today: str, found: dict[str, Any]) -> list[str]:
    done = []
    label = task.name
    # 1. 名册行并进各局名册
    by_dir: dict[str, list[dict[str, str]]] = {}
    for pid, row in found["roster"].items():
        note = "；".join(x for x in [row.get("notes"), f"补采任务 {label}（{today}）"] if x)
        by_dir.setdefault(BUREAU_DIRS[row["bureau"]], []).append({**row, "since": row["since"], "notes": note, "sec_filing_url": ""})
    for d, rows in by_dir.items():
        path = REPO / "bureaus" / d / "roster" / "roster.csv"
        cols = ["english_name", "position_en", "organization_en", "since", "official_bio_url", "official_portrait_url",
                "sec_filing_url", "notes"]
        _append_csv(path, cols, rows)
        with (path.parent / "sources.md").open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"\n- {today} 补采任务 {label}：新增 {len(rows)} 人（官方页面见各行 official_bio_url）\n")
        done.append(f"名册 bureaus/{d}/roster/roster.csv 新增 {len(rows)} 人")
    # 2. 中文名记进补充译名表（待审）
    names = [{"english_name": r["english_name"], "中文名": r["name_zh"], "依据": r["basis"], "依据URL": r["source_url"],
              "person_id": pid, "任务": label, "日期": today, "status": "待审"} for pid, r in found["names"].items()]
    if names:
        _append_csv(SUPPLEMENT, SUPPLEMENT_COLUMNS, names)
        done.append(f"中文名 {len(names)} 人记进 {SUPPLEMENT.relative_to(REPO).as_posix()}（待审）")
    # 3. 事实包放进各局 data/verified/
    for pid in found["resumes"]:
        rec = store.person(pid)
        bureau = (rec or {}).get("bureau") or found["roster"][pid]["bureau"]
        target = REPO / "bureaus" / BUREAU_DIRS.get(bureau, "personnel-others") / "data" / "verified" / f"{pid}.json"
        bundle = read_json(task / "resumes" / f"{pid}.json")
        bundle["source_task"] = label
        write_json(target, bundle)
    if found["resumes"]:
        done.append(f"事实包 {len(found['resumes'])} 份放进各局 data/verified/")
    # 4. 照片
    if (task / "portraits.csv").exists():
        from tools import import_portraits
        import_portraits.main(["--from-csv", str(task / "portraits.csv")])
        done.append("照片已下载并做成小图（见上一行统计）")
    # 5. 首发导入：名册、事实包、中文名、照片并进人物库
    report = importer.build(store, today, importer.default_aipolis())
    done.append(f"导入：写入 {report['written']}，保留 {report['kept']}，未变 {report['unchanged']}；{report['notes'] or ''}")
    return done


# ──────────────────────────────────────────────────────────────── 履历补月份


def fill_months(item: dict[str, Any]) -> list[str]:
    """履历起止只有年份的，按 cadre.md 第 3 节第 3 条补月份：上下条衔接 → 学年 → 7 月；记推测规则和依据。返回改动说明。"""
    careers = sorted(item.get("careers") or [], key=resume._sort_key)
    entry_month, leave_month = ("09", "05") if item.get("country_profile") == "us" else ("09", "07")
    notes = []
    for i, career in enumerate(careers):
        for side in ("start", "end"):
            fact = career.get(side)
            year = resume.value(fact) if fact else ""
            if len(year) != 4:
                continue
            near = careers[i - 1].get("end") if side == "start" and i else (careers[i + 1].get("start") if side == "end" and i + 1 < len(careers) else None)
            near_v = resume.value(near) if near else ""
            if near_v[:4] == year and len(near_v) >= 7:
                month, rule = near_v[5:7], "adjacent_entry"
                basis = f"上下条衔接：{'上一条止于' if side == 'start' else '下一条起于'}{year}年{int(month)}月"
            elif career["kind"] == "study":
                month, rule = (entry_month, "academic_calendar") if side == "start" else (leave_month, "academic_calendar")
                basis = f"学年：{'入学' if side == 'start' else '毕业'}取 {int(month)} 月"
            else:
                month, rule, basis = "07", "mid_year_default", "履历缺月份，取 7 月"
            fact.update(value=f"{year}-{month}", inferred=True,
                        basis="；".join(x for x in [fact.get("basis"), basis + "（cadre.md 第 3 节第 3 条）"] if x),
                        inference={"rule": rule, "from_date": year, "duration_years": None})
            notes.append(f"{career.get('organization') or ''}{career.get('role') or ''} {('起' if side == 'start' else '止')} {year} → {year}年{int(month)}月（{basis}）")
    return notes


def _set_month(fact: dict[str, Any], year: str, month: str, rule: str, basis: str) -> None:
    # 年份本来就是推出来的（如按第一份全职工作），保留原推测规则，月份的推法写进依据
    fact.update(value=f"{year}-{month}", inferred=True,
                basis="；".join(x for x in [fact.get("basis"), basis + "（cadre.md 第 3 节第 3 条）"] if x),
                inference=fact.get("inference") or {"rule": rule, "from_date": year, "duration_years": None})


def fill_person_months(item: dict[str, Any]) -> list[str]:
    """首段出生年月、参加工作时间只有年份的，按 cadre.md 第 3 节第 3 条补月份。
    出生月：来源没有就取 7 月；参加工作月：毕业月的下一个月（当年毕业、毕业月有记载）→ 7 月。"""
    person, notes = item["person"], []
    birth = person.get("birth_date")
    if birth and len(resume.value(birth)) == 4:
        year = resume.value(birth)
        _set_month(birth, year, "07", "mid_year_default", "出生月份无记载，取 7 月")
        notes.append(f"出生 {year} → {year}年7月（出生月份无记载，取 7 月）")
    work = person.get("work_start")
    if work and len(resume.value(work)) == 4:
        year = resume.value(work)
        grads = [resume.value(c["end"]) for c in item.get("careers") or []
                 if c.get("kind") == "study" and c.get("end") and len(resume.value(c["end"])) >= 7 and resume.value(c["end"])[:4] == year]
        month = max(grads)[5:7] if grads else None
        if month and month != "12":
            month = f"{int(month) + 1:02d}"
            _set_month(work, year, month, "graduation_next_month", f"毕业月的下一个月：{year}年{int(month) - 1}月毕业")
            notes.append(f"参加工作 {year} → {year}年{int(month)}月（毕业月的下一个月）")
        else:
            _set_month(work, year, "07", "mid_year_default", "参加工作月份无记载，取 7 月")
            notes.append(f"参加工作 {year} → {year}年7月（参加工作月份无记载，取 7 月）")
    return notes


def needs_months(item: dict[str, Any], store: Store, roster: dict[str, dict[str, str]], pid: str, glossary_lint) -> tuple[bool, bool]:
    """(首段要补月份, 履历要补月份)：看成稿检查的 CR10 写到月、CR11 全篇写到月。"""
    from tools.lint import cadre_lint
    record = _record_for(pid, personnel.fill_defaults(json.loads(json.dumps(item)), item.get("country_profile") or "us"), store, roster)
    composed = resume.compose(record)
    found = cadre_lint.lint_text(composed["text"], glossary=glossary_lint, country=composed["country"])
    lead = any(v.code == "CR10" and "写到月" in v.message for v in found)
    body = any(v.code == "CR11" and "全篇写到月" in v.message for v in found)
    return lead, body


def months_in_bundle(path: Path, store: Store, roster: dict[str, dict[str, str]], glossary_lint) -> list[str]:
    bundle = read_json(path)
    item = bundle["items"][0]
    lead, body = needs_months(item, store, roster, path.stem, glossary_lint)
    notes = (fill_person_months(item) if lead else []) + (fill_months(item) if body else [])
    if notes:
        write_json(path, bundle)
    return notes


def months(task: Path, store: Store, roster: dict[str, dict[str, str]]) -> list[str]:
    """成稿检查报 CR10 写到月、CR11“全篇写到月”的事实包，补齐月份后写回（任务文件夹，或单个事实包文件）。"""
    from tools.lint import cadre_lint
    glossary_lint = cadre_lint.load_glossary()
    paths = [task] if task.is_file() else sorted((task / "resumes").glob("*.json"))
    out = []
    for path in paths:
        out += [f"{path.stem}：{n}" for n in months_in_bundle(path, store, roster, glossary_lint)]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="补采收件箱：检查、入库；months 按推测规则补履历月份")
    ap.add_argument("action", choices=["check", "import", "months"])
    ap.add_argument("task", help="任务文件夹，例如 inbox/codex/sanju-roster")
    ap.add_argument("--date", help="今天的日期，默认本机日期")
    ap.add_argument("--data", help="数据目录，默认仓库 data/")
    ap.add_argument("--fetch", action="store_true", help="另取网页核对来源摘句（联网）")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    today = args.date or dt.date.today().isoformat()
    store = Store(args.data).load()
    task = Path(args.task)
    if args.action == "months":
        roster = {r["person_id"]: r for r in _csv(task / "roster.csv")} if task.is_dir() and (task / "roster.csv").exists() else {}
        for line in months(task, store, roster) or ["没有要补月份的事实包"]:
            print(line)
        return 0
    rep, found = check(task, store, today, fetch=args.fetch)
    print(rep.text())
    if args.action == "check" or rep.errors:
        if args.action == "import":
            print("\n有错误，没有入库")
        return 1 if rep.errors else 0
    for line in import_task(task, store, today, found):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
