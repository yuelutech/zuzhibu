"""首发基准导入：把各局已有成果写进统一人物库 data/people/（docs/PRODUCT.md 第三、十节）。

来源（都在仓库里，aipolis 金标除外）：

- 四局：bureaus/personnel-congress/data/bundles/（538 份，60 份 gold、478 份 draft）；
  data/verified/ 里核查通过的重核结果替换对应底稿，分级记 reverified。总统、副总统两份归三局。
- 二局：bureaus/personnel-sv/roster/roster.csv（97 人名册，seed）；黄仁勋用 aipolis 发布件（gold）替换名册行。
- 五局、六局及独立机构：bureaus/personnel-others/roster/roster.csv（34 人名册，seed），按机构归局。
- 三局：bureaus/personnel-whitehouse/roster/roster.csv（补采名册，有就读）。
- 补采简历：bureaus/<各局>/data/verified/<人物编号>.json（tools/inbox.py 检查后放入，四局除外），替换名册底稿，分级记 draft；
  一局写回过的记录，事实包日期不早于最近一条动态才替换，动态和转局记录保留。
- 补充译名：shared/glossary/补充译名_person.csv（待审），译名表 v1.4 没有的人用它，标“译名待核”。

导入不覆盖已有一局动态写回过的记录（events 不为空），除非 --force。
名册记录的中文名只取译名表 v1.4；表里没有的留空，界面显示英文并标“待译”。

    python -m zuzhibu.importer                 # 全部导入
    python -m zuzhibu.importer --aipolis <aipolis 目录>  # 不给就读本机设置 aipolis（zuzhibu/local.py）

只用标准库。
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import sys
from pathlib import Path
from typing import Any

from . import bureaus, local, personnel, portraits, resume
from .store import REPO, Store, events_of, read_json

CONGRESS_DIR = REPO / "bureaus" / "personnel-congress" / "data"
SV_ROSTER = REPO / "bureaus" / "personnel-sv" / "roster" / "roster.csv"
OTHERS_ROSTER = REPO / "bureaus" / "personnel-others" / "roster" / "roster.csv"
WH_ROSTER = REPO / "bureaus" / "personnel-whitehouse" / "roster" / "roster.csv"
SUPPLEMENT_NAMES = REPO / "shared" / "glossary" / "补充译名_person.csv"
TIERS = ("gold", "reverified", "draft", "seed", "held")
GLOSSARY = REPO / "shared" / "glossary"
ROSTER_DATE = "2026-10-17"   # 两份名册 sources.md 记的查阅日期


def _csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def glossary_names() -> dict[str, str]:
    out = {}
    for row in _csv(GLOSSARY / "译名表_v1.4_person.csv"):
        name = (row.get("新华社译名") or row.get("本人汉名或通行汉名") or "").strip()
        if name:
            out[row["english_name"].strip()] = name
    return out


def supplement_names() -> dict[str, str]:
    """补充译名表（补采得来，待审）：英文名 → 中文名。"""
    if not SUPPLEMENT_NAMES.exists():
        return {}
    return {row["english_name"].strip(): row["中文名"].strip() for row in _csv(SUPPLEMENT_NAMES) if row.get("中文名", "").strip()}


def verified_bundles() -> dict[str, tuple[Path, dict[str, Any]]]:
    """补采简历：bureaus/<各局>/data/verified/<人物编号>.json（四局按国会编号存，另行处理）。"""
    out: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in sorted((REPO / "bureaus").glob("*/data/verified/*.json")):
        if path.parent == CONGRESS_DIR / "verified":
            continue
        out[path.stem] = (path, read_json(path))
    return out


def keep_dispatch_roles(old_item: dict[str, Any], new_item: dict[str, Any], event_urls: set[str], today: str) -> list[str]:
    """补采事实包替换简历时，一局写回过的待任职务（提名人选、候任、拟任、当选人）不丢、不倒退（2026-10-21 堵的口子）。

    来源是此人动态来源的现任职务算一局写回的。新事实包里没有的补回去（连同来源）；同一机构已有在这之后到任的职务，
    说明这个阶段已经过去，不补。新事实包里有同一职务、任职状态进度靠前的，改用一局写回的状态。返回说明。
    """
    old_src = {s["id"]: s for s in old_item.get("sources") or []}
    current = new_item.setdefault("current_roles", [])
    notes = []
    for role in old_item.get("current_roles") or []:
        srcs = [old_src[i] for i in role.get("source_ids") or [] if i in old_src]
        if role.get("appointment_state") not in bureaus.PENDING_STATES or not any(s.get("url") in event_urls for s in srcs):
            continue
        text = f"{role.get('organization') or ''}{resume.role_text(role)}（{role['appointment_state']}）"
        twin = next((r for r in current if not personnel.titles_conflict(r, role)
                     and (personnel.same_title(r, role) or personnel.same_role(r, role))), None)
        if twin is not None:
            if personnel.STATE_RANK.get(role["appointment_state"], 0) > personnel.STATE_RANK.get(twin.get("appointment_state"), 4):
                twin["appointment_state"] = role["appointment_state"]
                notes.append(f"保留一局写回的任职状态：{text}")
            continue
        since = ((role.get("start") or {}).get("value") or "")[:10]
        if any(r.get("appointment_state") not in bureaus.PENDING_STATES and personnel._norm(r.get("organization") or "")
               == personnel._norm(role.get("organization") or "") and ((r.get("start") or {}).get("value") or "") >= since
               for r in current):
            continue
        mapping = personnel.merge_sources(new_item, srcs, today)
        moved = copy.deepcopy(role)
        moved["source_ids"] = list(dict.fromkeys(mapping[i] for i in role.get("source_ids") or [] if i in mapping))
        if isinstance(moved.get("start"), dict):
            moved["start"]["source_ids"] = list(dict.fromkeys(mapping[i] for i in moved["start"].get("source_ids") or [] if i in mapping))
        current.append(moved)
        notes.append(f"补回一局写回的待任职务：{text}")
    return notes


def use_bundle(rec: dict[str, Any], path: Path, bundle: dict[str, Any], keep_from: dict[str, Any] | None = None,
               event_urls: set[str] | None = None, today: str = "") -> list[str]:
    """补采事实包替换记录里的简历：分级 draft（程序检查通过，待独立核查）。

    给了 keep_from（原记录）和 event_urls（此人动态的来源链接）时，一局写回过的待任职务按 keep_dispatch_roles 保留。
    """
    item = json.loads(json.dumps(bundle["items"][0]))
    item = personnel.fill_defaults(item, item.get("country_profile") or "us")
    item["tags"] = ["draft"] + [t for t in item.get("tags") or [] if t not in TIERS]
    for role in item.get("current_roles") or []:
        role["appointment_state"] = role.get("appointment_state") or "正式"
    item["person"]["status"] = item["person"].get("status") or ("在任" if item.get("current_roles") else "离任")
    rec.update(tier="draft", resume=item, data_cutoff=max_checked(item) or bundle.get("date") or rec.get("data_cutoff"))
    note = f"补采简历 {path.relative_to(REPO).as_posix()}（{bundle.get('source_task') or '外部会话'}，程序检查通过，待独立核查）"
    rec["origin"] = "；".join(x for x in [(rec.get("origin") or "").split("；补采简历")[0], note] if x)
    name = resume.value(item["person"].get("name"))
    if name and not rec.get("name_zh"):
        rec["name_zh"], rec["name_zh_pending"] = name, True
    if keep_from and event_urls:
        return keep_dispatch_roles(keep_from.get("resume") or {}, item, event_urls, today or bundle.get("date") or "")
    return []


def event_source_urls(store: Store, pid: str) -> set[str]:
    return {s.get("url") for e in events_of(store, pid) for s in e.get("sources") or [] if s.get("url")}


def bundle_is_current(store: Store, pid: str, bundle: dict[str, Any]) -> bool:
    """事实包日期不早于此人最近一条动态，才能替换一局写回过的记录。"""
    dates = [e.get("date") or "" for e in events_of(store, pid)]
    return not dates or str(bundle.get("date") or "") >= max(dates)


def glossary_orgs() -> dict[str, str]:
    out = {}
    for row in _csv(GLOSSARY / "译名表_v1.4_org.csv"):
        name = (row.get("简历写法") or row.get("全称") or "").strip()
        if name:
            out[row["english_name"].strip()] = name
    return out


def glossary_titles() -> dict[str, str]:
    return {row["english_title"].strip().lower(): row["干部简历写法"].strip()
            for row in _csv(GLOSSARY / "译名表_v1.4_title.csv") if row.get("干部简历写法", "").strip()}


def max_checked(item: dict[str, Any]) -> str | None:
    dates = [s.get("checked_at") for s in item.get("sources") or [] if s.get("checked_at")]
    return max(dates) if dates else None


# ──────────────────────────────────────────────────────────────── 四局


def congress_records() -> list[dict[str, Any]]:
    index = {r["id"]: r for r in _csv(CONGRESS_DIR / "index.csv")}
    verified: dict[str, dict[str, Any]] = {}
    progress = CONGRESS_DIR / "verified" / "进度.csv"
    if progress.exists():
        for row in _csv(progress):
            path = CONGRESS_DIR / "verified" / f"{row['bioguide']}.json"
            if row.get("状态") == "done" and path.exists():
                verified[row["bioguide"]] = read_json(path)
    out = []
    for bid, row in index.items():
        bundle = verified.get(bid) or read_json(CONGRESS_DIR / "bundles" / f"{bid}.json")
        item = bundle["items"][0]
        tier = "reverified" if bid in verified else (item.get("tags") or ["draft"])[0]
        if bid in ("president", "vice-president"):
            bureau = bureaus.WHITEHOUSE
            ids: dict[str, str] = {"congress_import": bid}
        else:
            bureau = bureaus.CONGRESS
            ids = {"bioguide": bid}
        out.append({
            "name_zh": row["name_zh"], "name_en": row["name_en"], "bureau": bureau, "also_in": [], "tier": tier,
            "ids": ids, "resume": item, "data_cutoff": max_checked(item) or bundle.get("date"),
            "origin": "四局首发：Care ceo-profiles 导入" + ("，reverify.py 重核通过" if bid in verified else ""),
        })
    return out


# ──────────────────────────────────────────────────────────────── 名册（二局、五局、六局）


def _split(text: str) -> list[str]:
    return [p.strip() for p in (text or "").split(";") if p.strip()]


def _title_zh(title_en: str, titles: dict[str, str], enterprise: bool = True) -> str | None:
    """整条职衔在译名表里才译；复合职衔拆“ and ”逐项都能译才译，否则返回 None。

    译名表的职务表按企业职务编写。政府机构的一两个词的通用职衔（Governor、Chairman、Director 等）
    要看机构才能定（美联储的 Governor 是理事，不是州长），这类不译，留英文待译。
    """
    key = title_en.strip().lower()
    if not enterprise and len(key.split()) <= 2:
        return None
    if key in titles:
        return titles[key]
    if not enterprise:
        return None
    parts = [p.strip() for p in key.replace(" and ", ", ").split(",") if p.strip()]
    if parts and all(p in titles for p in parts):
        return "、".join(titles[p] for p in parts)
    return None


def roster_records(path: Path, names: dict[str, str], orgs: dict[str, str], titles: dict[str, str],
                   extra_names: dict[str, str] | None = None) -> list[dict[str, Any]]:
    out = []
    for row in _csv(path):
        name_en = row["english_name"].strip()
        name_zh = names.get(name_en, "")
        pending = not name_zh and bool((extra_names or {}).get(name_en))
        name_zh = name_zh or (extra_names or {}).get(name_en, "")
        org_parts, pos_parts = _split(row["organization_en"]), _split(row["position_en"])
        if len(org_parts) != len(pos_parts):
            org_parts = [row["organization_en"].strip()] * len(pos_parts)
        sources = []
        for url, title in ((row.get("official_bio_url"), "官方页面"), (row.get("sec_filing_url"), "美国证券交易委员会申报文件")):
            if url:
                # 名册任务只收官方页面和证券交易委员会申报，都是一级来源（rules/cadre.md 第 4 节、第 9 节第 7 条）
                sources.append({"id": f"s{len(sources) + 1}", "url": url.strip(), "title": f"{title}（{row['organization_en']}）",
                                "published_at": None, "checked_at": ROSTER_DATE, "excerpt": "", "tier": 1})
        source_ids = [s["id"] for s in sources]
        roles, roster_roles, unknowns = [], [], []
        for n, (org_en, pos_en) in enumerate(zip(org_parts, pos_parts)):
            org_zh = orgs.get(org_en)
            sector = personnel._sector_for({"organization_en": org_en, "role_en": pos_en})
            title_zh = _title_zh(pos_en, titles, enterprise=sector == "enterprise")
            role = {"organization": org_zh or org_en, "role": title_zh or pos_en, "organization_en": org_en, "role_en": pos_en}
            start = row.get("since") if n == 0 and row.get("since") else None
            roles.append({"organization": role["organization"], "role": role["role"],
                          "start": personnel.fact(start, source_ids) if start else None, "concurrent": n > 0,
                          "source_ids": source_ids, "roles": [], "sector": sector, "appointment_state": "正式"})
            roster_roles.append({"organization_en": org_en, "role_en": pos_en})
            if not org_zh or not title_zh:
                unknowns.append(f"职务待译：{org_en} {pos_en}")
        bureau, why = bureaus.classify_person([{**r, **rr} for r, rr in zip(roles, roster_roles)])
        out.append({
            "name_zh": name_zh, "name_en": name_en, **({"name_zh_pending": True} if pending else {}),
            "bureau": bureau, "also_in": [], "tier": "seed", "ids": {},
            "roster_roles": roster_roles, "portrait": {"url": row["official_portrait_url"].strip(), "source": row.get("official_bio_url", "").strip()}
            if row.get("official_portrait_url") else None,
            "notes": [n for n in [row.get("notes", "").strip()] if n],
            "_roles": roles, "_sources": sources,
            "_unknowns": (["中文名待译（译名表无此人）"] if not name_zh else ["中文名取补充译名表，待审"] if pending else []) + unknowns,
            "data_cutoff": ROSTER_DATE, "origin": f"名册首发：{path.relative_to(REPO).as_posix() if path.is_relative_to(REPO) else path.name}（{why}）",
        })
    return out


def aipolis_gold(root: Path) -> dict[str, dict[str, Any]]:
    """aipolis 已发布的干部简历（data/releases），按 subject_id 取最新一份。"""
    out: dict[str, dict[str, Any]] = {}
    releases = root / "data" / "releases"
    if not releases.is_dir():
        return out
    for path in sorted(releases.glob("*.json")):
        item = read_json(path).get("item") or {}
        if item.get("line") == "cadre":
            out[item["subject_id"]] = item
    return out


# ──────────────────────────────────────────────────────────────── 写入


def build(store: Store, today: str, aipolis_root: Path | None, force: bool = False) -> dict[str, Any]:
    names, orgs, titles, extra_names = glossary_names(), glossary_orgs(), glossary_titles(), supplement_names()
    gold = aipolis_gold(aipolis_root) if aipolis_root else {}
    bundles = verified_bundles()
    used: set[str] = set()
    report = {"written": 0, "kept": 0, "unchanged": 0, "by_bureau": {}, "by_tier": {}, "notes": []}
    incoming = congress_records()
    for roster in (SV_ROSTER, OTHERS_ROSTER, WH_ROSTER):
        if roster.exists():
            incoming += roster_records(roster, names, orgs, titles, extra_names)
    for rec in incoming:
        existing = (store.find_by_external("bioguide", rec["ids"]["bioguide"]) if rec["ids"].get("bioguide") else None)
        if not existing:
            hits = store.find_by_name(rec["name_en"])
            existing = hits[0] if len(hits) == 1 else None
        pid = existing["id"] if existing else store.new_person_id(rec["name_en"], rec["name_zh"])
        bundle = bundles.get(pid) if "resume" not in rec else None
        if bundle and existing and existing.get("events") and not bundle_is_current(store, pid, bundle[1]):
            report["notes"].append(f"{pid}：补采简历早于最近的动态，未替换")
            bundle = None
        if bundle:
            used.add(pid)
        if existing and existing.get("events") and not force and not bundle:
            # 一局写回过的记录不重建，只补照片
            before = existing.get("portrait")
            portraits.attach(existing, store.root)
            if existing.get("portrait") != before:
                store.save_person(existing)
            report["kept"] += 1
            continue
        if bundle and existing and existing.get("events"):
            rec.update(events=existing["events"], bureau=existing.get("bureau"), also_in=existing.get("also_in") or [])
        if "resume" not in rec:
            subject = "jensen-huang" if rec["name_en"] == "Jensen Huang" else None
            if bundle:
                kept = use_bundle(rec, *bundle, keep_from=existing, event_urls=event_source_urls(store, pid), today=today)
                report["notes"] += [f"{pid}：{n}" for n in kept]
            elif subject and subject in gold:
                item = gold[subject]
                item = personnel.fill_defaults(json.loads(json.dumps(item)), "us")
                item["person"]["status"] = item["person"].get("status") or "在任"
                for role in item.get("current_roles") or []:
                    role["appointment_state"] = role.get("appointment_state") or "正式"
                rec.update(tier="gold", resume=item, data_cutoff=max_checked(item) or rec["data_cutoff"],
                           origin="二局首发：aipolis 发布件（金标）")
                rec["name_zh"] = rec["name_zh"] or resume.value(item["person"]["name"])
            else:
                item = personnel.new_resume(pid, rec["name_zh"], rec["name_en"])
                item["sources"] = rec["_sources"]
                item["current_roles"] = rec["_roles"]
                item["unknowns"] = rec["_unknowns"] + ["名册记录：简历待建"]
                item["person"]["status"] = "在任"
                item["country_profile"] = "us" if rec["bureau"] in (bureaus.WHITEHOUSE, bureaus.MILITARY, bureaus.JUSTICE) else None
                rec["resume"] = item
        for key in ("_roles", "_sources", "_unknowns"):
            rec.pop(key, None)
        rec["id"] = pid
        rec.setdefault("events", [])
        portraits.attach(rec, store.root)
        resume.annotate(rec)
        if existing and all(existing.get(k) == rec.get(k) for k in ("resume", "tier", "bureau", "name_zh", "name_en", "roster_roles", "portrait")):
            report["unchanged"] = report.get("unchanged", 0) + 1
            continue  # 内容没变不重写，免得每天几百个文件只改了更新日期
        rec["updated_at"] = today
        rec["roles_checked_at"] = rec["data_cutoff"]
        history = list((existing or {}).get("bureau_history") or [])
        if not history or history[-1].get("to") != rec["bureau"]:
            history.append({"from": history[-1]["to"] if history else None, "to": rec["bureau"], "date": today, "event": None,
                             "reason": rec["origin"]})
        rec["bureau_history"] = history
        errors = personnel.validate_resume(rec["resume"], today)
        if errors:
            report["notes"].append(f"{pid}：schema 校验不通过，未导入：{errors[:3]}")
            continue
        ordered = {k: rec.get(k) for k in ("id", "name_zh", "name_en", "name_zh_pending", "bureau", "also_in", "tier", "ids", "data_cutoff",
                                           "roles_checked_at", "updated_at", "origin", "portrait", "roster_roles", "notes",
                                           "events", "bureau_history", "resume") if k in rec}
        store.save_person(ordered)
        report["written"] += 1
        report["by_bureau"][rec["bureau"] or "未定"] = report["by_bureau"].get(rec["bureau"] or "未定", 0) + 1
        report["by_tier"][rec["tier"]] = report["by_tier"].get(rec["tier"], 0) + 1
    # 名册以外的人（一局新建的）：补采简历、补充译名直接改记录
    for pid, (path, bundle) in bundles.items():
        if pid in used:
            continue
        rec = store.person(pid)
        if not rec:
            # 一局动态新建的人：从头导入时还没回放动态，人物记录不在，事实包等回放后再用
            report.setdefault("bundles_waiting", []).append(pid)
            continue
        if rec.get("bureau") == bureaus.CONGRESS or not bundle_is_current(store, pid, bundle):
            report["notes"].append(f"{pid}：补采简历早于最近的动态或属四局，未替换")
            continue
        new = json.loads(json.dumps(rec))
        kept = use_bundle(new, path, bundle, keep_from=rec, event_urls=event_source_urls(store, pid), today=today)
        report["notes"] += [f"{pid}：{n}" for n in kept]
        if new["resume"] == rec["resume"]:
            report["unchanged"] += 1
            continue
        if personnel.validate_resume(new["resume"], today):
            report["notes"].append(f"{pid}：补采简历 schema 校验不通过，未替换")
            continue
        new["updated_at"], new["roles_checked_at"] = today, new["data_cutoff"]
        portraits.attach(new, store.root)
        resume.annotate(new)
        store.save_person(new)
        report["written"] += 1
        report["by_tier"]["draft"] = report["by_tier"].get("draft", 0) + 1
    for rec in store.people():
        name = extra_names.get(rec.get("name_en") or "")
        if name and not names.get(rec.get("name_en") or "") and rec.get("name_zh") != name \
                and (not rec.get("name_zh") or rec.get("name_zh_pending")):
            rec["name_zh"], rec["name_zh_pending"] = name, True
            store.save_person(rec)
            report["written"] += 1
    write_meta(store, today)
    return report


def write_meta(store: Store, today: str) -> dict[str, Any]:
    """各局人数和实际资料截止日期（最早、最晚），写进 data/meta.json。"""
    store.load()
    meta = store.meta()
    per: dict[str, dict[str, Any]] = {}
    for rec in store.people():
        b = rec.get("bureau") or "未定"
        d = per.setdefault(b, {"people": 0, "cutoff_min": None, "cutoff_max": None, "tiers": {}})
        d["people"] += 1
        d["tiers"][rec.get("tier")] = d["tiers"].get(rec.get("tier"), 0) + 1
        cut = rec.get("data_cutoff")
        if cut:
            d["cutoff_min"] = min(filter(None, [d["cutoff_min"], cut]))
            d["cutoff_max"] = max(filter(None, [d["cutoff_max"], cut]))
    meta.update({"schema": "zuzhibu-data/1", "baseline": "2026年10月版", "updated_at": today, "bureaus": per,
                 "events": len(store.events())})
    meta.setdefault("daily", {"last_run": None, "last_status": None, "window_days": 92,
                              "default_view": {"from": "2026-08-01", "to": "2026-10-31"}})
    store.save_meta(meta)
    return meta


def default_aipolis() -> Path | None:
    """aipolis 目录（取已发布的金标简历）：本机设置 aipolis；没设或不在就不取。"""
    found = local.get_path("aipolis")
    return found if found and found.exists() else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="首发基准导入到 data/people/")
    ap.add_argument("--data", help="数据目录，默认仓库 data/")
    ap.add_argument("--aipolis", help="aipolis 目录（取已发布的金标简历）；不给就读本机设置 aipolis，给空字符串跳过")
    ap.add_argument("--date", default=None, help="导入日期，默认今天")
    ap.add_argument("--force", action="store_true", help="一局写回过的记录也覆盖")
    args = ap.parse_args(argv)
    import datetime as dt
    today = args.date or dt.date.today().isoformat()
    store = Store(args.data).load()
    aipolis = default_aipolis() if args.aipolis is None else (Path(args.aipolis) if args.aipolis else None)
    report = build(store, today, aipolis if aipolis and aipolis.exists() else None, force=args.force)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["notes"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
