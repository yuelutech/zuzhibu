"""人物照片：data/portraits/<人物编号>.jpg 与来源登记 data/portraits/index.csv。

名单里的人是蓝白渐变底 3:4 头肩标准像，600×800（docs/PRODUCT.md 第三节；抠图、换底在仓库外做，仓库只放成品）；
名单外的人保留早先的 300×400 小图。界面按显示框等比缩放，不依赖图片像素尺寸。
人物记录的 portrait 字段：file 是 data/portraits 下的照片；url 是名册登记的官方照片链接（可能没有）；source、license 是来源与许可。
界面优先显示本地照片，没有就用官方链接。只用标准库。
"""

from __future__ import annotations

import base64
import csv
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .store import data_dir


def folder(root: str | Path | None = None) -> Path:
    return data_dir(root) / "portraits"


@lru_cache(maxsize=4)
def _index(path: str, mtime: float) -> dict[str, dict[str, str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        return {row["person_id"]: row for row in csv.DictReader(fh)}


def index(root: str | Path | None = None) -> dict[str, dict[str, str]]:
    path = folder(root) / "index.csv"
    if not path.exists():
        return {}
    return _index(str(path), path.stat().st_mtime)


def attach(rec: dict[str, Any], root: str | Path | None = None) -> dict[str, Any]:
    """按登记表给人物记录挂上本地小图；名册里的官方链接保留在 url。"""
    row = index(root).get(rec["id"])
    if not row:
        return rec
    portrait = dict(rec.get("portrait") or {})
    portrait.update(file=row["file"], source=row.get("source") or portrait.get("source", ""),
                    source_kind=row.get("source_kind", ""), license=row.get("license", ""))
    if row.get("source_url") and not portrait.get("url"):
        portrait["url"] = row["source_url"]
    rec["portrait"] = portrait
    return rec


def credit(rec: dict[str, Any], root: str | Path | None = None) -> str | None:
    """CC BY（含 CC BY-SA）照片的署名，如“照片：某某，CC BY 2.0，有裁切”；换过蓝白渐变底的写“已抠图换底、裁切”。
    公有领域等不用署名的返回 None。依据登记表的许可一栏（开头是许可，冒号后第一项是作者）和处理方式。"""
    row = index(root).get(rec.get("id") or "") or {}
    if not (rec.get("portrait") or {}).get("file"):
        return None
    m = re.match(r"(CC BY[^：:；;，,]*)[：:]\s*([^，,；;]+)", row.get("license") or "")
    if not m:
        return None
    change = "已抠图换底、裁切" if "蓝白渐变底" in (row.get("license", "") + row.get("processing", "")) else "有裁切"
    return f"照片：{m.group(2).strip()}，{m.group(1).strip()}，{change}"


def display_src(rec: dict[str, Any]) -> str | None:
    """界面用的图片地址：本地小图走 /portraits/，没有就用官方链接。"""
    portrait = rec.get("portrait") or {}
    if portrait.get("file"):
        return "portraits/" + portrait["file"]
    return portrait.get("url") or None


def data_uri(rec: dict[str, Any], root: str | Path | None = None) -> str | None:
    """导出用：本地小图转成 data URI 内嵌，导出的 HTML 单独打开也有照片。

    只用仓库里的照片（公有领域或 CC BY）；没有本地小图的不放照片，不引用官网原图——
    版权不在我们手里的图不进导出物（docs/公开前检查.md 第十节）。界面上仍可显示官网原图链接。
    """
    portrait = rec.get("portrait") or {}
    path = folder(root) / (portrait.get("file") or "")
    if portrait.get("file") and path.is_file():
        return "data:image/jpeg;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    return None
