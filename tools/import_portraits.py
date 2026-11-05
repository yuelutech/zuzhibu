"""把本机已有的人物照片裁成 3:4、缩到 600×800，放进 data/portraits/，并登记来源（shared/portraits 规则）。

名单里的人要用蓝白渐变底的头肩标准像（docs/PRODUCT.md 第三节）：抠图、换底在仓库外用维护者本机的工具做，
做好的成品直接放进 data/portraits/ 并登记 index.csv；本工具只裁切缩放、不换底，适合名单外的人。

    python tools/import_portraits.py [--library <素材库目录>] [--dry-run]   # 不给 --library 就读本机设置 library（zuzhibu/local.py）
    python tools/import_portraits.py --from-csv inbox/codex/<任务>/portraits.csv   # 补采的照片链接：下载后做小图

来源（都在维护者本机，不进仓库）：
- us_political_portraits_证件照/*.png：国会议员和总统、副总统的证件照（蓝底，已抠图），原图在 us_political_portraits/，
  下载索引 roster_index.csv 记了国会编号和维基百科页面名；
- Care 历史项目 CEO 雷达网站的人物照片（apps/web/public/images/people/*.jpg）和白宫内阁照片索引（官方来源链接）；
- AI_Key_People_Portraits_20：20 位 AI 人物照片，source_manifest.csv 记了来源页面和下载链接。
生成图（如“科技人物标准头像_生成版_20”、文件名带 Gemini 的）一律不用。

输出：data/portraits/<人物编号>.jpg（600×800，3:4，JPEG）和 data/portraits/index.csv（人物编号、文件、来源类别、来源、来源链接、许可、处理方式、日期）。
同一人有多个来源时，取顺序：AI 关键人物 20（有来源清单）→ 国会证件照 → 雷达网站照片。已有小图的不重做。
需要 Pillow（本机 aipolis 运行环境里有）。
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from zuzhibu import local  # noqa: E402
from zuzhibu.store import Store, name_key, slugify  # noqa: E402

OUT = REPO / "data" / "portraits"
INDEX = OUT / "index.csv"
FIELDS = ["person_id", "file", "source_kind", "source", "source_url", "license", "processing", "date"]
SIZE = (600, 800)   # 照片资源规格（docs/PRODUCT.md 第三节）；界面显示框另有尺寸，等比缩放
CARE = "Care历史项目"
RADAR_IMAGES = f"{CARE}/解包_2026-10-16/资料/D/Claude/ceo-radar-build/repo/apps/web/public/images/people"
CABINET = f"{CARE}/补要解包_2026-10-16/资料/D/ChatGPT/ai-ceo-radar/research/white-house-cabinet/cabinet-candidates.json"
KEY20 = f"{CARE}/解包_2026-10-16/资料/D/ChatGPT/AI_Key_People_Portraits_20"


def public_license(text: str) -> bool:
    """许可一栏开头是公有领域、CC0 或 CC BY（不含 NC、ND）的照片才进仓库（docs/公开前检查.md 第十节）。"""
    token = re.split(r"[：:；;，,（(]", text or "", maxsplit=1)[0]
    return token.startswith(("公有领域", "CC0", "CC BY")) and not re.search(r"NC|ND", token)


def fit(img):
    """居中裁成 3:4，再缩到 SIZE；带透明通道的铺白底。"""
    from PIL import Image
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        base = Image.new("RGB", img.size, (255, 255, 255))
        base.paste(img, mask=img.split()[-1])
        img = base
    else:
        img = img.convert("RGB")
    w, h = img.size
    target = 3 / 4
    if w / h > target:            # 偏宽：两侧等裁
        nw = int(h * target)
        img = img.crop(((w - nw) // 2, 0, (w - nw) // 2 + nw, h))
    elif w / h < target:          # 偏高：裁底部（cadre.md 第 5 节）
        nh = int(w / target)
        img = img.crop((0, 0, w, nh))
    return img.resize(SIZE, Image.LANCZOS)


def candidates(library: Path, store: Store) -> dict[str, dict]:
    """人物编号 → 选中的照片来源。"""
    people = store.people()
    by_bid = {(r.get("ids") or {}).get("bioguide", "").upper(): r for r in people if (r.get("ids") or {}).get("bioguide")}
    by_key = {}
    for r in people:
        by_key.setdefault(name_key(r.get("name_en") or ""), r)
    by_slug = {slugify(r.get("name_en") or ""): r for r in people}
    special = {"president": "Donald Trump", "vice-president": "JD Vance"}
    out: dict[str, dict] = {}

    key20 = library / KEY20 / "source_manifest.csv"
    if key20.exists():
        for row in csv.DictReader(key20.open(encoding="utf-8-sig")):
            name_en = re.sub(r"[\u4e00-\u9fff]+", "", row["person"]).strip()
            rec = by_key.get(name_key(name_en))
            path = library / KEY20 / Path(row["file"]).name
            if rec and path.exists():
                commons = "commons" in (row.get("source_kind") or "").lower()
                out.setdefault(rec["id"], {"path": path, "source_kind": "AI关键人物20（" + ("维基共享资源" if commons else "官方") + "）",
                                           "source": row.get("source_page", ""), "source_url": row.get("download_url", ""),
                                           "license": "待核：维基共享资源照片，按原图许可写明许可和作者" if commons else "版权保留：公司官网官方照片，未见开放许可"})

    index = library / "us_political_portraits" / "roster_index.csv"
    if index.exists():
        for row in csv.DictReader(index.open(encoding="utf-8-sig")):
            if row.get("status") != "downloaded":
                continue
            bid = row["bioguide"]
            rec = by_bid.get(bid.upper()) or by_key.get(name_key(special.get(bid, row["full"])))
            if not rec:
                continue
            stem = Path(row["filename"]).stem
            processed = library / "us_political_portraits_证件照" / f"{stem}.png"
            original = library / "us_political_portraits" / row["filename"]
            path = processed if processed.exists() else original
            # 国会议员的原图就是 unitedstates/images 的 450×550 图（2026-10-20 逐张比对一致，docs/公开前检查.md 第十节）；
            # 总统、副总统是特例键，照片另有来源，按原图 EXIF 核实后再写许可。
            gpo = bid[:1].isalpha() and bid[1:].isdigit()
            if path.exists():
                out.setdefault(rec["id"], {"path": path, "source_kind": "国会照片库（证件照）" if path == processed else "国会照片库",
                                           "source": "美国政府出版局国会议员指南官方照片（unitedstates/images 收录）" if gpo else f"维基百科：{row.get('wikipedia', '')}",
                                           "source_url": f"https://unitedstates.github.io/images/congress/450x550/{bid}.jpg" if gpo else "",
                                           "license": "公有领域：美国政府出版局发布的国会议员官方照片" if gpo else "待核：按原图 EXIF 和来源核实"})

    cabinet = {}
    cab_path = library / CABINET
    if cab_path.exists():
        for c in json.loads(cab_path.read_text(encoding="utf-8")):
            cabinet[c.get("slug", "")] = (c.get("photo") or {})
    images = library / RADAR_IMAGES
    if images.is_dir():
        for path in sorted(images.glob("*.jpg")):
            rec = by_slug.get(path.stem) or by_key.get(name_key(path.stem.replace("-", " ")))
            if not rec:
                continue
            photo = cabinet.get(path.stem) or {}
            out.setdefault(rec["id"], {"path": path, "source_kind": "CEO雷达网站照片",
                                       "source": photo.get("sourcePage") or "CEO雷达网站图片（Care 历史项目，2026-06）",
                                       "source_url": photo.get("sourceUrl", ""),
                                       "license": "待核：官方来源，核对使用条款" if photo else "来源不明：来源待补"})
    return out


DOWNLOADS = REPO / "output" / "portraits_download"
#: 下载照片时如实报程序名，不冒充浏览器；网站拒绝程序下载的就跳过（docs/公开前检查.md 第十二节第 8 项）。
USER_AGENT = "zuzhibu-portraits/1.0 (+https://github.com/yuelutech/zuzhibu)"


def from_csv(path: Path) -> dict[str, dict]:
    """补采的照片链接表（person_id,image_url,source_page,license）：下载原图到 output/portraits_download/。"""
    out = {}
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            pid, url = row["person_id"].strip(), row["image_url"].strip()
            ext = Path(url.split("?")[0]).suffix.lower()
            target = DOWNLOADS / f"{pid}{ext if ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif') else '.img'}"
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    target.write_bytes(resp.read())
            except Exception as exc:  # noqa: BLE001 —— 下载失败只跳过这一张
                print(f"{pid}：下载失败 {exc}")
                continue
            out[pid] = {"path": target, "source_kind": "官方照片（补采）", "source": row.get("source_page", "").strip(),
                        "source_url": url, "license": row.get("license", "").strip(),
                        "processing": f"由 {url} 下载，裁成 3:4 缩至 {SIZE[0]}×{SIZE[1]}"}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="人物照片小图入库")
    ap.add_argument("--library", default=local.get("library"), help="素材库目录；默认读本机设置 library")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-csv", help="补采的照片链接表：下载后做小图")
    ap.add_argument("--replace", action="store_true", help="已有小图的也换（只对 --from-csv）")
    args = ap.parse_args(argv)
    from PIL import Image
    if not args.library and not args.from_csv:
        ap.error("要给 --library，或在本机设置写 library")
    library = Path(args.library) if args.library else None
    store = Store().load()
    chosen = from_csv(Path(args.from_csv)) if args.from_csv else candidates(library, store)
    rows = {}
    if INDEX.exists():
        rows = {r["person_id"]: r for r in csv.DictReader(INDEX.open(encoding="utf-8"))}
    today = dt.date.today().isoformat()
    made = 0
    for pid, c in sorted(chosen.items()):
        if pid in rows and (OUT / rows[pid]["file"]).exists() and not (args.from_csv and args.replace):
            continue
        if not public_license(c["license"]):
            print(f"{pid}：许可不是公有领域或 CC BY，不进仓库（{c['license']}）")
            continue
        if args.dry_run:
            made += 1
            continue
        OUT.mkdir(parents=True, exist_ok=True)
        try:
            with Image.open(c["path"]) as img:
                fit(img).save(OUT / f"{pid}.jpg", "JPEG", quality=85, optimize=True)
        except OSError as exc:
            print(f"{pid}：不是能用的图片 {exc}")
            continue
        rows[pid] = {"person_id": pid, "file": f"{pid}.jpg", "source_kind": c["source_kind"], "source": c["source"],
                     "source_url": c["source_url"], "license": c["license"],
                     "processing": c.get("processing") or f"由本机 {c['path'].relative_to(library).as_posix()} 裁成 3:4 缩至 {SIZE[0]}×{SIZE[1]}",
                     "date": today}
        made += 1
    if not args.dry_run:
        with INDEX.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            for pid in sorted(rows):
                w.writerow(rows[pid])
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"候选 {len(chosen)} 人，新做 {made} 张，索引共 {len(rows)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
