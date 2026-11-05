"""译名表同步：把维护者本机的译名表母本（v1.4 三张表）拷进 shared/glossary/，维护者姓名换成“AI政见”，换行统一成 LF，其余逐字节不变。母本不改。

    python tools/sync_glossary.py --source <母本目录> --name <维护者姓名>           # 同步
    python tools/sync_glossary.py --source <母本目录> --name <维护者姓名> --check   # 只比对：副本和“母本换名后”不一致就列出来，退出码 1

两项都可以不给，改读本机设置的 glossary_master、maintainer_name（zuzhibu/local.py）。维护者姓名不进仓库。只用标准库。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from zuzhibu import local  # noqa: E402

TARGET = REPO / "shared" / "glossary"
TABLES = ("译名表_v1.4_org.csv", "译名表_v1.4_title.csv", "译名表_v1.4_person.csv")
REPLACEMENT = "AI政见"


def converted(data: bytes, name: str) -> bytes:
    """母本的字节换成副本的字节：CRLF 改 LF，维护者姓名换成“AI政见”。"""
    return data.replace(b"\r\n", b"\n").replace(name.encode("utf-8"), REPLACEMENT.encode("utf-8"))


def sync(source: Path, name: str, target: Path = TARGET, check: bool = False) -> list[str]:
    """返回和母本不一致的表名；check 为假时把这些表写成母本换名后的样子。"""
    if not name.strip():
        raise ValueError("要给维护者姓名")
    changed = []
    for table in TABLES:
        new = converted((source / table).read_bytes(), name)
        path = target / table
        if not path.exists() or path.read_bytes() != new:
            changed.append(table)
            if not check:
                path.write_bytes(new)
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把译名表母本同步进 shared/glossary/，维护者姓名换成“AI政见”。母本不改。")
    ap.add_argument("--source", default=local.get("glossary_master"), help="母本目录（放 译名表_v1.4_*.csv 的目录）；默认读本机设置 glossary_master")
    ap.add_argument("--name", default=local.get("maintainer_name"), help="维护者姓名：副本里换成“AI政见”；默认读本机设置 maintainer_name")
    ap.add_argument("--check", action="store_true", help="只比对，不写")
    args = ap.parse_args(argv)
    if not args.source or not args.name:
        ap.error("要给 --source 和 --name，或写进本机设置")
    sys.stdout.reconfigure(encoding="utf-8")
    changed = sync(Path(args.source), args.name, check=args.check)
    if not changed:
        print("三张表和母本一致（换名、换行之外）。")
        return 0
    verb = "和母本不一致" if args.check else "已同步"
    for table in changed:
        print(f"{verb}：{table}")
    return 1 if args.check else 0


if __name__ == "__main__":
    raise SystemExit(main())
