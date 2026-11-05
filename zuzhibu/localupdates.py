"""本机更新：立即更新写进来的动态、给名单新任取到的标准简历和名单变动另记一份；维护者的数据换新以后，按同一套规则
重放回去（docs/PRODUCT.md 第七节）。

    python -m zuzhibu.localupdates status [--journal 目录]
    python -m zuzhibu.localupdates merge --data 数据目录 [--journal 目录] [--date 今天]

本机更新目录（默认 %LOCALAPPDATA%\\zuzhibu\\local，环境变量 ZUZHIBU_HOME 改了软件本机目录的随之改）：

    events/<动态编号>.json   本机写进的每条动态（研究、核查结果和写回结果）；换了名单的，roster 记着换了哪些位置
    people/<人物编号>.json   本机给名单新任取到标准简历和官网照片后的整份人物记录
    pending/<动态编号>.json  名单换人而新任还没取到标准简历和照片、这一组没有写的；下次立即更新接着取
    state.json              最近一次本机更新：时间、窗口、写回和搁置条数；下次立即更新从这里的截止日接着查

联网取维护者的月度数据时（zuzhibu/datasync.py），维护者改过的文件整份换成维护者的版本，本机写回的改动随之不见；
只有本机有的文件（本机新建的人物、本机的动态）原样留着。merge 按下面的规则把本机的逐条重放回去。

谁优先：维护者每月下发的数据是底本，同一件事以维护者为准；本机新核实、维护者还没有的，重放到底本上；
维护者在本机这条之后另有变动的，不拿本机较早的消息去盖。

1. 动态：
   - 维护者数据里已有同一条（同一人、同一类别、日期相差 7 天内）：以维护者的为准，本机这条记“已并入”，数据目录里的本机副本删掉；
   - 人物记录里已经有这条（维护者没改过这个人）：不动；
   - 维护者数据里此人有日期更晚的动态：不重放，免得拿旧消息盖掉新资料；动态标“待核”，交使用者核对；
   - 其余按 zuzhibu/personnel.py 的同一套规则重新写回，动态文件放回 events/。
2. 标准简历：维护者数据里此人已有完整简历卡的，用维护者的；没有这个人、或只有名册级资料的，装上本机取到的标准简历
   和官网照片（动态标“待核”的那一组不装）。
3. 名单：
   - 维护者的名单上这个位置已是新任：不动；已是第三人或没有这个位置：维护者另有安排，以维护者为准；
   - 还是原任（维护者的数据还没跟上）：维护者数据里新任、原任都没有比本机这次更晚的动态，新任的人物记录够上名单
     （完整简历卡、有照片），就把本机这处换人重放上去，旧数据不会盖掉已核实的新任命；否则以维护者为准。
4. 同一组的动态、人物记录、名单一起写（zuzhibu/rosterchange.commit），中途出错全部退回原样，这一组记“重放失败”，
   下次再放。

重放不调模型、不联网。本机更新目录的原件一直留着，不随维护者数据删改；同一份数据重放几次结果一样。
数据分发换快照时在新快照上调 after_sync()，从不抛异常：重放出错时照常用维护者数据，原件留着下次再放。

只用标准库。
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import bureaus, headline, importer, personnel, resume, rosterchange
from .store import Store, name_key, read_json, write_json

HOME_ENV = "ZUZHIBU_HOME"
ORIGIN = "user-api"
SAME_EVENT_DAYS = 7


def app_home() -> Path:
    """软件的本机目录：环境变量 ZUZHIBU_HOME，默认 %LOCALAPPDATA%\\zuzhibu（与数据分发 datasync.app_home 相同）。"""
    env = os.environ.get(HOME_ENV)
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "zuzhibu"


def default_journal() -> Path:
    return app_home() / "local"


def _read_all(folder: Path) -> list[dict[str, Any]]:
    out = []
    if folder.is_dir():
        for path in folder.glob("*.json"):
            try:
                data = read_json(path)
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("id"):
                out.append(data)
    return out


class Journal:
    """本机更新目录。"""

    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(root) if root else default_journal()
        self.events_dir = self.root / "events"
        self.people_dir = self.root / "people"
        self.pending_dir = self.root / "pending"
        self.state_path = self.root / "state.json"

    def events(self) -> list[dict[str, Any]]:
        return sorted(_read_all(self.events_dir), key=lambda e: (e.get("date") or "", e["id"]))

    def event_path(self, eid: str) -> Path:
        return self.events_dir / f"{eid}.json"

    def record(self, event: dict[str, Any]) -> Path:
        """记下一条本机写进数据目录的动态（写回之后的样子，含 applied）。"""
        path = self.event_path(event["id"])
        write_json(path, event)
        return path

    def people(self) -> dict[str, dict[str, Any]]:
        """本机取到标准简历的人物记录：{人物编号: 记录}。"""
        return {rec["id"]: rec for rec in _read_all(self.people_dir)}

    def person_path(self, pid: str) -> Path:
        return self.people_dir / f"{pid}.json"

    def pending(self) -> list[dict[str, Any]]:
        """上次没写的名单换人组：{id, record, changes, pid, attempts, reasons, at}。"""
        return sorted(_read_all(self.pending_dir), key=lambda e: ((e.get("record") or {}).get("date") or "", e["id"]))

    def save_pending(self, entry: dict[str, Any]) -> None:
        write_json(self.pending_dir / f"{entry['id']}.json", entry)

    def drop_pending(self, eid: str) -> None:
        (self.pending_dir / f"{eid}.json").unlink(missing_ok=True)

    def state(self) -> dict[str, Any]:
        try:
            data = read_json(self.state_path)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_state(self, **changes: Any) -> dict[str, Any]:
        state = self.state()
        state.update(changes)
        write_json(self.state_path, state)
        return state

    def last_cutoff(self) -> str | None:
        window = self.state().get("last_window") or []
        return window[1] if len(window) == 2 and window[1] else None


# ──────────────────────────────────────────────────────────────── 重放


def _date(ev: dict[str, Any]) -> dt.date | None:
    try:
        return dt.date.fromisoformat((ev.get("date") or "")[:10])
    except ValueError:
        return None


def _same_person(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a.get("person_id") and a.get("person_id") == b.get("person_id"):
        return True
    key = name_key((a.get("person") or {}).get("name_en") or "")
    return bool(key) and key == name_key((b.get("person") or {}).get("name_en") or "")


def same_event(entry: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """维护者数据里的同一条：同一人、同一类别、日期相差 7 天内（与一局写回时的查重相同）。"""
    when = _date(entry)
    for ev in events:
        if ev.get("kind") != entry.get("kind") or not _same_person(entry, ev):
            continue
        other = _date(ev)
        if when is None or other is None or abs((when - other).days) <= SAME_EVENT_DAYS:
            return ev
    return None


def later_event(entry: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """维护者数据里此人日期更晚的动态（取最晚一条）。"""
    when = _date(entry)
    later = [ev for ev in events if _same_person(entry, ev) and when and _date(ev) and _date(ev) > when]
    return max(later, key=lambda e: (e.get("date") or "", e["id"])) if later else None


def event_file(store: Store, ev: dict[str, Any]) -> Path:
    """动态在数据目录里的文件（与 Store.save_event 相同）。"""
    return store.events_dir / (ev.get("date") or "0000-00")[:7] / f"{ev['id']}.json"


def _remove_event(store: Store, eid: str) -> None:
    path = store._event_paths.pop(eid, None)
    store._events.pop(eid, None)
    if path and path.exists():
        path.unlink()


def install_person(store: Store, local: dict[str, Any] | None) -> str:
    """本机取到的标准简历装进数据（合并规则第 2 条，只改内存）。返回说明；维护者已有完整简历卡或没有本机记录的返回空串。"""
    if not local:
        return ""
    current = store.person(local["id"])
    if current is None:
        rec = copy.deepcopy(local)
        rec["events"] = [e for e in rec.get("events") or [] if store.event(e) is not None]  # 动态由重放接回
        store._people[rec["id"]] = rec
        return "装上本机取到的人物记录"
    if resume.card_type(current)[0]:
        return ""
    rec = copy.deepcopy(current)
    rec["resume"] = copy.deepcopy(local["resume"])
    for key in ("tier", "data_cutoff", "roles_checked_at"):
        if key in local:
            rec[key] = local[key]
    if not (rec.get("portrait") or {}).get("file") and local.get("portrait"):
        rec["portrait"] = copy.deepcopy(local["portrait"])
    store._people[rec["id"]] = rec
    return "装上本机取到的标准简历"


def _roster_blocked(store: Store, change: dict[str, Any], theirs: list[dict[str, Any]]) -> str:
    """本机这处换人能不能重放到维护者的名单上（合并规则第 3 条）。能放返回空串，否则返回原因。"""
    for ev in theirs:
        if ev.get("person_id") in (change.get("new"), change.get("old")) and (ev.get("date") or "") > (change.get("date") or ""):
            return f"维护者数据里有更晚的变动 {ev['id']}，以维护者为准"
    problems = rosterchange.ready(store.person(change.get("new") or ""))
    return f"新任的人物记录还不能上名单（{'；'.join(problems)}）" if problems else ""


def merge(store: Store, journal: Journal, today: str) -> dict[str, Any]:
    """把本机更新目录里的动态、标准简历、名单变动重放到数据目录（规则见本模块说明）。返回各类条数和逐条说明。"""
    entries = journal.events()
    report: dict[str, Any] = {"journal": len(entries), "kept": 0, "replayed": 0, "merged": 0, "pending": 0, "failed": 0,
                              "roster": {"kept": 0, "replayed": 0, "merged": 0}, "changed": False, "details": []}
    if not entries:
        return report
    store.load()
    people = journal.people()
    rosters = rosterchange.load(store.root)
    runs = {e["id"]: (e.get("verification") or {}).get("run") for e in entries}

    def own(ev: dict[str, Any] | None) -> bool:
        """数据目录里的这条是不是本机写进去的那份（编号相同、出自同一次更新）。"""
        return ev is not None and ev["id"] in runs and (ev.get("verification") or {}).get("run") == runs[ev["id"]]

    theirs = [e for e in store.events() if not own(e)]
    before = store.meta().get("updated_at")
    for entry in entries:
        eid = entry["id"]
        mine = store.event(eid) if own(store.event(eid)) else None
        label = headline.event_headline(entry, store) or eid  # 标题由程序按字段生成，研究会话写的只作内部记录
        saved: dict[str, dict[str, Any] | None] = {}
        events_out: list[dict[str, Any]] = []
        removes: list[str] = []
        status, note = None, ""

        def keep(pid: str | None) -> None:
            if pid and pid not in saved:
                saved[pid] = copy.deepcopy(store.person(pid))

        dup = same_event(entry, theirs)
        pid = (mine or entry).get("person_id")
        if dup is not None:
            status, note = "merged", f"维护者数据已有同一条 {dup['id']}，以维护者的为准"
            if mine is not None:
                removes.append(eid)
        else:
            rec = store.person(pid) if pid else None
            if mine is not None and rec is not None and eid in (rec.get("events") or []):
                status = "kept"
            else:
                later = later_event(entry, theirs)
                if later is not None:
                    status = "pending"
                    note = (f"维护者数据里此人有更晚的动态 {later['id']}（{later.get('date')}），本机这条没有重放，"
                            f"免得盖掉新资料；请人工核对")
                    if ((mine or {}).get("applied") or {}).get("reason") != note:
                        ev = _clean(entry)
                        ev["applied"] = {"status": personnel.PENDING, "at": today, "changes": [], "notes": [note],
                                         "bureau_before": None, "bureau_after": None, "person_id": pid, "reason": note}
                        events_out.append(ev)
        if status != "pending" and pid in people:
            keep(pid)
            installed = install_person(store, people[pid])
            if not installed:
                saved.pop(pid, None)
        if status is None:
            keep(pid)
            found, _ = personnel.resolve_person(store, {**entry, "person_id": pid if pid and store.person(pid) else None})
            keep(found["id"] if found else None)
            ev, status, note = _replay(store, entry, mine, today)
            events_out.append(ev)
            if ev.get("person_id") and ev["person_id"] not in saved:
                saved[ev["person_id"]] = None  # 重放时新建的人物记录
        # 名单
        staged: dict[str, dict[str, Any]] = {}
        roster_notes: list[str] = []
        if status not in ("pending", "failed"):
            for change in entry.get("roster") or []:
                where = change.get("section")
                state = rosterchange.standing({**rosters, **staged}, change)
                if state == "open":
                    why = _roster_blocked(store, change, theirs)
                    if why:
                        state = "merged"
                        roster_notes.append(f"名单「{change.get('post_after')}」没有重放：{why}")
                    else:
                        staged.setdefault(where, copy.deepcopy(rosters[where]))
                        person = store.person(change["new"]) or {}
                        party = resume.value(((person.get("resume") or {}).get("person") or {}).get("party")) if where == "congress" else None
                        rosterchange.place(staged, change, change["new"], party or None)
                        state = "replayed"
                report["roster"][state] += 1
        writes: dict[Path, Any] = {store.people_dir / f"{p}.json": store.person(p)
                                   for p in saved if store.person(p) is not None and store.person(p) != saved[p]}
        for ev in events_out:
            writes[event_file(store, ev)] = ev
        for where, roster in staged.items():
            writes[rosterchange.path_of(store.root, where)] = roster
        try:
            rosterchange.commit(writes)
            for gone in removes:
                _remove_event(store, gone)
        except Exception as exc:  # noqa: BLE001  一组写不进去就整组退回，原件留着下次再放
            for p, rec in saved.items():
                if rec is None:
                    store._people.pop(p, None)
                else:
                    store._people[p] = rec
            status, note, staged = "failed", f"重放时写入出错：{type(exc).__name__}：{exc}", {}
        else:
            for ev in events_out:
                store._events[ev["id"]] = ev
                store._event_paths[ev["id"]] = event_file(store, ev)
            rosters.update(staged)
        if writes or removes:
            report["changed"] = True
        report[status] += 1
        note = "；".join(filter(None, [note, *roster_notes]))
        report["details"].append({"id": eid, "headline": label, "status": status, "note": note})
        local = {"status": status, "at": today, **({"note": note} if note else {})}
        if (entry.get("local") or {}).get("status") != status or (entry.get("local") or {}).get("note") != local.get("note"):
            journal.record({**entry, "local": local})
    if report["changed"]:
        meta = importer.write_meta(store, today)
        last_user = (journal.state().get("last_run") or "")[:10]
        meta["updated_at"] = max(filter(None, [before, last_user])) if (before or last_user) else meta["updated_at"]
        meta["user_update"] = {**(meta.get("user_update") or {}), **journal.state(),
                               "last_merge": {k: report[k] for k in ("journal", "kept", "replayed", "merged", "pending", "failed")}
                               | {"roster": report["roster"], "at": today}}
        store.save_meta(meta)
    return report


def _clean(entry: dict[str, Any]) -> dict[str, Any]:
    """本机记的这条去掉本机才有的字段（重放结果、名单变动），还原成数据目录里的动态。"""
    ev = copy.deepcopy(entry)
    for key in ("applied", "local", "roster"):
        ev.pop(key, None)
    return ev


def _replay(store: Store, entry: dict[str, Any], mine: dict[str, Any] | None, today: str) -> tuple[dict[str, Any], str, str]:
    """按现行写回规则把一条本机动态重新写回（只改内存，由 merge 一起落盘）。返回 (动态, replayed 或 failed, 说明)。"""
    ev = _clean(entry)
    if ev.get("person_id") and not store.person(ev["person_id"]):
        ev["person_id"] = None  # 维护者删了或改了这个人的编号：按外部编号、姓名重新找
    if store.event(ev["id"]) is not None and mine is None:
        ev["id"] = store.new_event_id(ev["date"], ev.get("person_id") or "x", ev["kind"])  # 这个编号已是维护者的另一条
    hint = [b for b in ev.get("bureaus") or [] if b in bureaus.ACTIVE_BUREAUS]
    result = personnel.apply_event(store, ev, today, save=False)
    ev["bureaus"] = sorted({*hint[:1], *(b for b in (result.get("bureau_before"), result.get("bureau_after")) if b)})
    if result["status"] == personnel.FAILED:  # 写回失败的也留着，界面显示“写回失败”和原因
        return ev, "failed", "重放时写回失败：" + "；".join(result.get("notes") or [])
    return ev, "replayed", "按现行规则重新写回：" + ("；".join(result.get("changes") or []) or personnel.STATUS_LABEL.get(result["status"], ""))


def after_sync(data_dir: str | os.PathLike, journal_dir: str | os.PathLike | None = None, today: str | None = None) -> dict[str, Any] | None:
    """数据分发换上维护者的新数据后调用：把本机动态重放到 data_dir。没有本机动态返回 None；出错返回 {"error": 原因}，不抛异常。"""
    journal = Journal(journal_dir)
    try:
        if not journal.events():
            return None
        return merge(Store(data_dir), journal, today or dt.date.today().isoformat())
    except Exception as exc:  # noqa: BLE001  换数据不能因为重放失败而中断；原件留着，下次再放
        return {"error": f"{type(exc).__name__}：{exc}"}


# ──────────────────────────────────────────────────────────────── 命令行


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m zuzhibu.localupdates", description="本机更新：查看、重放到数据目录")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status", help="本机更新目录里有什么")
    s.add_argument("--journal")
    m = sub.add_parser("merge", help="把本机动态重放到数据目录")
    m.add_argument("--data", required=True)
    m.add_argument("--journal")
    m.add_argument("--date")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    journal = Journal(args.journal)
    if args.cmd == "status":
        events = journal.events()
        counts: dict[str, int] = {}
        for ev in events:
            key = (ev.get("local") or {}).get("status") or "未重放过"
            counts[key] = counts.get(key, 0) + 1
        print(json.dumps({"journal": str(journal.root), "events": len(events), "status": counts,
                          "people": sorted(journal.people()), "pending": [p["id"] for p in journal.pending()],
                          "state": journal.state()}, ensure_ascii=False, indent=2))
        return 0
    report = merge(Store(args.data), journal, args.date or dt.date.today().isoformat())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
