"""写回规则改动后重算：把受一局动态影响的人物恢复到首发状态，再按现行规则依次重放全部已核实动态。

    python -m zuzhibu.replay [--data 数据目录] [--date 今天]

动态本身（研究和核查的结果、来源）不变，只重算写回结果；一局新建的人物按动态重建，编号不变。
只用标准库。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from . import bureaus, importer, personnel
from .store import Store


def replay(store: Store, today: str, aipolis_root: Path | None) -> dict[str, Any]:
    store.load()
    events = sorted(store.events(), key=lambda e: (e.get("date") or "", e["id"]))
    created: set[str] = set()
    for pid in {e.get("person_id") for e in events if e.get("person_id")}:
        rec = store.person(pid)
        if rec is None:
            continue
        if rec.get("origin") == "一局动态新建":
            created.add(pid)
        (store.people_dir / f"{pid}.json").unlink()
    store.load()
    importer.build(store, today, aipolis_root)  # 恢复首发记录；一局新建的不在原料里，不会回来
    store.load()
    counts: dict[str, int] = {}
    for ev in events:
        if ev.get("person_id") in created:
            ev["person_id"] = None
        hint = [b for b in ev.get("bureaus") or [] if b in bureaus.ACTIVE_BUREAUS]
        ev.pop("applied", None)
        result = personnel.apply_event(store, ev, today, save=False)
        ev["bureaus"] = sorted({*hint[:1], *(b for b in (result.get("bureau_before"), result.get("bureau_after")) if b)})
        if result["status"] != personnel.FAILED and ev.get("person_id"):
            store.save_person(store.person(ev["person_id"]))
        store.save_event(ev)
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    importer.write_meta(store, today)
    return {"events": len(events), "rebuilt_people": len(created), "status": counts}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="按现行写回规则重放全部动态")
    ap.add_argument("--data")
    ap.add_argument("--date")
    ap.add_argument("--aipolis", help="aipolis 目录；不给就读本机设置 aipolis")
    args = ap.parse_args(argv)
    today = args.date or dt.date.today().isoformat()
    aipolis = importer.default_aipolis() if args.aipolis is None else (Path(args.aipolis) if args.aipolis and Path(args.aipolis).exists() else None)
    report = replay(Store(args.data), today, aipolis)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
