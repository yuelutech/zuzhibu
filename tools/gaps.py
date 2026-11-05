"""缺项清单：每人还缺什么（完整简历卡条件、中文名、照片），补采任务按它分工，维护者按它查进度。

    python tools/gaps.py                                  # 全库，Markdown 表
    python tools/gaps.py --bureau ai --batch 1/2          # 二局按英文名排序后分两批，取第 1 批
    python tools/gaps.py --bureau whitehouse --need any   # 只列还有缺项的人
    python tools/gaps.py --format csv --out gaps.csv

分批按整局排好序后切，不受谁已补齐影响，同一批号前后几次运行指的是同一批人。只用标准库。
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from zuzhibu import bureaus, importer, resume  # noqa: E402
from zuzhibu.store import Store  # noqa: E402

COLUMNS = ["序号", "编号", "中文名", "英文名", "所属局", "分级", "中文名状态", "现任职务", "完整简历卡缺项", "照片", "资料截止"]


def name_state(rec: dict, glossary: dict[str, str]) -> str:
    if not rec.get("name_zh"):
        return "待译"
    if rec.get("name_zh_pending"):
        return "表外译名待核"
    return "译名表" if glossary.get(rec.get("name_en") or "") == rec["name_zh"] else "已有"


def job(rec: dict) -> str:
    roles = rec["resume"].get("current_roles") or []
    primary = bureaus.primary_role(roles)
    if not primary:
        return "（无现任职务）"
    text = resume.stated(primary.get("organization", ""), resume.role_text(primary), primary.get("appointment_state"))
    return text + (f"（另 {len(roles) - 1} 项）" if len(roles) > 1 else "")


def rows(store: Store, bureau: str | None = None, batch: tuple[int, int] | None = None, need: str = "all") -> list[dict]:
    glossary = importer.glossary_names()
    order = {b: i for i, b in enumerate(bureaus.PERSON_BUREAUS)}
    people = [r for r in store.people() if not bureau or r.get("bureau") == bureau]
    people.sort(key=lambda r: (order.get(r.get("bureau"), 99), (r.get("name_en") or r["id"]).lower()))
    if batch:
        k, n = batch
        size = -(-len(people) // n)
        people = people[(k - 1) * size: k * size]
    out = []
    for r in people:
        _, reasons = resume.card_type(r)
        photo = bool((r.get("portrait") or {}).get("file") or (r.get("portrait") or {}).get("url"))
        state = name_state(r, glossary)
        missing = {"resume": bool(reasons), "name": state in ("待译", "表外译名待核"), "photo": not photo}
        if need != "all" and not (any(missing.values()) if need == "any" else missing[need]):
            continue
        out.append({"编号": r["id"], "中文名": r.get("name_zh") or "", "英文名": r.get("name_en") or "",
                    "所属局": bureaus.label(r.get("bureau")) if r.get("bureau") else "未定", "分级": r.get("tier") or "",
                    "中文名状态": state, "现任职务": job(r), "完整简历卡缺项": "、".join(reasons) or "齐",
                    "照片": "有" if photo else "无", "资料截止": r.get("data_cutoff") or ""})
    for n, row in enumerate(out, 1):
        row["序号"] = n
    return out


def render(items: list[dict], fmt: str) -> str:
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(items)
        return buf.getvalue()
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for row in items:
        lines.append("| " + " | ".join(str(row[c]).replace("|", "／") for c in COLUMNS) + " |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="缺项清单")
    ap.add_argument("--bureau", choices=list(bureaus.PERSON_BUREAUS), help="只列一个局")
    ap.add_argument("--batch", help="分批，例如 1/2")
    ap.add_argument("--need", choices=["all", "any", "resume", "name", "photo"], default="all",
                    help="all 全列；any 有缺项的；resume 未达完整简历卡；name 缺中文名或待核；photo 缺照片")
    ap.add_argument("--format", choices=["md", "csv"], default="md")
    ap.add_argument("--out", help="写到文件；不给就打印")
    ap.add_argument("--data", help="数据目录，默认仓库 data/")
    args = ap.parse_args(argv)
    batch = None
    if args.batch:
        k, n = (int(x) for x in args.batch.split("/"))
        if not 1 <= k <= n:
            ap.error("--batch 写成 k/n，1 ≤ k ≤ n")
        batch = (k, n)
    items = rows(Store(args.data).load(), args.bureau, batch, args.need)
    text = render(items, args.format)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8-sig" if args.format == "csv" else "utf-8", newline="")
        print(f"{len(items)} 人 → {args.out}")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text)
        print(f"\n共 {len(items)} 人")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
