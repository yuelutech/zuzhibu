"""逐项核对装好的程序：该有的都在，不该有的没有。python -m tools.installer.contents [目录或商店包]

<目录> 可以是安装目录（含 resources\\）、electron-builder 的解包目录 win-unpacked，或待装目录 build/installer/stage；
不给就用这台电脑“已安装”里登记的位置。给商店包（.appx、.msix）时先核对包的清单（身份、显示名、语言、只要完全信任一项权限、
图标齐全、没有签名），再解到 build/installer/ 下的临时目录，对里面的 app\\ 照下面逐项核对，核完删掉。逐项：
1. 程序和界面：pyapp 里的文件和仓库里的逐个一致（build.py 的 APP_TREES、APP_FILES）；
2. 附带数据：数据清单里的文件逐个在，大小和 SHA-256 对；
3. 标准像：名单人物的照片都是 tools/import_portraits.py 规定的尺寸，名单外人物的旧照允许 300×400（2026-10-22 定：名单外的照片不重做），
   和 index.csv 一一对应；另报名单里有标准像的人数；人事任免横图 750×230（两倍图 1500×460）；
4. 字体：fonts.py 列的字体逐个在、SHA-256 对，许可证全文和 FONTS.txt 都在；别处没有字体文件（Times New Roman 用系统的）；
5. 不带账号和密钥：没有设置、登录凭据一类的文件和目录，文本文件里没有像凭据的串（规则同 tools/guard.py）。
有一项不对退出码 1。只用标准库。
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import struct
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Iterable

from tools import guard
from tools.import_portraits import SIZE as PORTRAIT_SIZE
from zuzhibu import datasync
from zuzhibu.store import REPO

from . import build, fonts, say, utf8_output

Item = tuple[bool | None, str]  # (通过/不通过/只报告, 说明)

SECRET_NAMES = {"settings.json", "local.json", "auth.json", "credentials.json", ".credentials.json", ".env", ".netrc"}
SECRET_SUFFIXES = (".env", ".pem", ".key", ".p12", ".pfx")
SECRET_DIRS = {".codex", ".ssh", ".aws", ".config"}
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png")
LEGACY_SIZE = (300, 400)   # 名单外人物的旧照尺寸（不在界面显示，2026-10-22 定不重做）
BANNERS = {"web/img/renmian.png": (750, 230), "web/img/renmian@2x.png": (1500, 460)}  # 人事任免横图（自制）和两倍图
_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
PACKAGE_SUFFIXES = (".appx", ".msix")
APPX_NS = {"m": "http://schemas.microsoft.com/appx/manifest/foundation/windows10",
           "uap": "http://schemas.microsoft.com/appx/manifest/uap/windows10",
           "rescap": "http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities"}
STORE_LANGUAGE = "zh-CN"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def locate(root: Path) -> Path:
    """root 下的 pyapp：安装目录和解包目录在 resources\\pyapp，待装目录在 pyapp，解开的商店包在 app\\resources\\pyapp。"""
    for cand in (root / "resources" / build.APP_DIR, root / build.APP_DIR, root / "app" / "resources" / build.APP_DIR):
        if (cand / "dist.json").is_file():
            return cand
    raise SystemExit(f"{root} 里没有 {build.APP_DIR}\\dist.json：不是安装目录、解包目录、待装目录或解开的商店包")


def image_size(path: Path) -> tuple[int, int] | None:
    """JPEG、PNG 的（宽, 高），只看文件头；认不出返回 None。"""
    data = path.read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return struct.unpack(">II", data[16:24])
    if data[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 <= len(data):
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # 填充
            i += 1
            continue
        if marker in _SOF:
            height, width = struct.unpack(">HH", data[i + 5:i + 9])
            return width, height
        if marker in (0xD9, 0xDA):  # 图像结束、扫描开始：前面没有尺寸
            return None
        i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


def roster_people(rosters: Path) -> set[str]:
    """名单文件（data/rosters/*.json）里出现的人物编号。"""
    found: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("person"), str):
                found.add(node["person"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for path in sorted(rosters.glob("*.json")) if rosters.is_dir() else ():
        walk(json.loads(path.read_text(encoding="utf-8")))
    return found


def _some(names: Iterable[str], n: int = 5) -> str:
    names = sorted(names)
    return "、".join(names[:n]) + (f" 等 {len(names)} 个" if len(names) > n else "")


# ──────────────────────────────────────────────────────────────── 各项


def check_program(pyapp: Path, repo: Path = REPO, files: list[str] | None = None) -> list[Item]:
    dist = json.loads((pyapp / "dist.json").read_text(encoding="utf-8"))
    items: list[Item] = [(None, f"{dist.get('name')} {dist.get('version')}，构建于 {dist.get('built_at')}，"
                                f"提交 {dist.get('commit') or '不明'}，{dist.get('shell')}，Python {dist.get('python')}")]
    now = build.git_commit(repo)
    if now and dist.get("commit") and now != dist["commit"]:
        items.append((None, f"仓库现在是 {now}，和构建时不同：下一项按仓库现在的文件比"))
    files = build.app_files(repo) if files is None else files
    differ = [rel for rel in files if not (pyapp / rel).is_file() or sha256(pyapp / rel) != sha256(repo / rel)]
    web = sum(1 for rel in files if rel.startswith("web/"))
    items.append((not differ, f"程序和界面文件 {len(files)} 个（界面 {web} 个）和仓库一致" if not differ
                  else f"程序和界面文件有 {len(differ)} 个缺失或和仓库不一致：{_some(differ)}"))
    return items


def check_data(pyapp: Path) -> list[Item]:
    data = pyapp / "data"
    manifest = datasync.load_manifest(data / datasync.MANIFEST)
    if manifest is None:
        return [(False, "附带数据没有数据清单 data/manifest.json")]
    bad = [rel for rel, entry in manifest["files"].items()
           if not (data / rel).is_file() or datasync.file_digest(data / rel) != (entry["sha256"], entry["size"])]
    extra = set(datasync.iter_files(data)) - set(manifest["files"]) - {datasync.MANIFEST}
    counts = "，".join(f"{k} {v}" for k, v in (manifest.get("counts") or {}).items())
    items: list[Item] = [(not bad, f"附带数据 {len(manifest['files'])} 个文件和数据清单一致（{counts}；数据日期 {manifest.get('data_date')}）"
                          if not bad else f"附带数据有 {len(bad)} 个文件缺失或校验不符：{_some(bad)}")]
    if extra:  # 如内容许可说明：随安装包带，不随联网更新
        items.append((None, f"附带数据另有清单外的文件：{_some(extra)}"))
    return items


def check_portraits(pyapp: Path, size: tuple[int, int] = PORTRAIT_SIZE) -> list[Item]:
    folder = pyapp / "data" / "portraits"
    images = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES) if folder.is_dir() else []
    sizes = {p.name: image_size(p) for p in images}
    people = roster_people(pyapp / "data" / "rosters")
    wrong = [name for name, got in sizes.items() if got != size and (Path(name).stem in people or got != LEGACY_SIZE)]
    seen = "、".join(f"{w}×{h} {n} 张" if (w, h) != (0, 0) else f"认不出 {n} 张"
                    for (w, h), n in Counter(got or (0, 0) for got in sizes.values()).most_common())
    items: list[Item] = [(bool(images) and not wrong,
                          f"标准像 {len(images)} 张：名单人物都是 {size[0]}×{size[1]}（共 {seen}）" if images and not wrong
                          else f"名单人物的标准像应为 {size[0]}×{size[1]}，名单外的旧照可为 {LEGACY_SIZE[0]}×{LEGACY_SIZE[1]}："
                          f"{seen or '一张也没有'}" + (f"（不符的有 {_some(wrong)}）" if wrong else ""))]
    index = folder / "index.csv"
    if not index.is_file():
        items.append((False, "没有标准像登记 data/portraits/index.csv"))
    else:
        with open(index, encoding="utf-8-sig", newline="") as fh:
            listed = [row.get("file") or "" for row in csv.DictReader(fh)]
        names = {p.name for p in images}
        missing, unlisted = set(listed) - names, names - set(listed)
        dup = [name for name, n in Counter(listed).items() if n > 1]
        ok = not missing and not unlisted and not dup
        items.append((ok, f"index.csv 登记 {len(listed)} 条，和照片一一对应" if ok else
                      "index.csv 和照片对不上：" + "；".join(part for part in (
                          f"登记了没有照片的 {_some(missing)}" if missing else "",
                          f"有照片没登记的 {_some(unlisted)}" if unlisted else "",
                          f"重复登记的 {_some(dup)}" if dup else "") if part)))
    without = {pid for pid in people if not any((folder / f"{pid}{ext}").is_file() for ext in IMAGE_SUFFIXES)}
    if people:
        items.append((None, f"名单 {len(people)} 人，有标准像 {len(people) - len(without)} 人"
                      + (f"，没有的：{_some(without, 8)}" if without else "")))
    return items


def check_fonts(top: Path, pyapp: Path, font_list: tuple[fonts.Font, ...] = fonts.FONTS,
                font_dir: str = build.FONT_DIR) -> list[Item]:
    folder = pyapp / font_dir
    items: list[Item] = []
    for font in font_list:
        f, lic = folder / font.file, folder / font.license_file
        problems = [what for what, ok in (
            ("字体文件缺失", f.is_file()), ("字体校验不符", f.is_file() and sha256(f) == font.sha256),
            ("许可证全文缺失", lic.is_file()), ("许可证校验不符", lic.is_file() and sha256(lic) == font.license_sha256)) if not ok]
        items.append((not problems, f"字体 {font.name} {font.version}：{font_dir}/{font.file}，许可证全文 {font.license_file}"
                      + (f"（{'、'.join(dict.fromkeys(problems))}）" if problems else "")))
    items.append(((folder / "FONTS.txt").is_file(), f"字体说明 {font_dir}/FONTS.txt"))
    expected = {os.path.normcase(str(folder / font.file)) for font in font_list}
    others = [p.relative_to(top).as_posix() for p in top.rglob("*")
              if p.suffix.lower() in guard.FONT_SUFFIXES and os.path.normcase(str(p)) not in expected]
    items.append((not others, "别处没有字体文件（Times New Roman 用系统的，不随软件分发）" if not others
                  else f"多出字体文件：{_some(others)}"))
    return items


def check_secrets(top: Path, pyapp: Path) -> list[Item]:
    named = [p.relative_to(top).as_posix() for p in top.rglob("*")
             if p.name.lower() in SECRET_NAMES or (p.is_file() and p.suffix.lower() in SECRET_SUFFIXES)
             or (p.is_dir() and p.name.lower() in SECRET_DIRS)]
    texts = [p.relative_to(pyapp).as_posix() for p in pyapp.rglob("*")
             if p.is_file() and "__pycache__" not in p.parts]
    hits = guard.credential_files(texts, root=pyapp)
    return [(not named, "没有设置、登录凭据一类的文件（settings.json、auth.json、.codex 等）" if not named
             else f"带了不该带的文件：{_some(named)}"),
            (not hits, f"程序和数据的 {len(texts)} 个文件里没有像凭据的串" if not hits
             else "有像凭据的串：" + _some(f"{f}（{kind}）" for f, kind, _head in hits))]


def check_banners(pyapp: Path, banners: dict[str, tuple[int, int]] = BANNERS) -> list[Item]:
    items: list[Item] = []
    for rel, size in banners.items():
        got = image_size(pyapp / rel) if (pyapp / rel).is_file() else None
        items.append((got == size, f"人事任免横图 {rel}：{size[0]}×{size[1]}" if got == size
                      else f"人事任免横图 {rel} 应为 {size[0]}×{size[1]}：{'没有' if got is None else f'{got[0]}×{got[1]}'}"))
    return items


def check(root: Path, repo: Path = REPO) -> list[Item]:
    root = Path(root)
    pyapp = locate(root)
    return [*check_program(pyapp, repo), *check_data(pyapp), *check_portraits(pyapp), *check_banners(pyapp),
            *check_fonts(root, pyapp), *check_secrets(root, pyapp)]


# ──────────────────────────────────────────────────────────────── 商店包


def package_names(z: zipfile.ZipFile) -> list[str]:
    """商店包里的文件名：包内按网址的写法存（非英文字符、空格转成 %XX），还原成原名；有越出包外的名字就停下。"""
    names = []
    for info in z.infolist():
        name = urllib.parse.unquote(info.filename)
        parts = PurePosixPath(name).parts
        if name.startswith(("/", "\\")) or ".." in parts or any(":" in p or "\\" in p for p in parts):
            raise SystemExit(f"商店包里有可疑的文件名：{info.filename}")
        names.append(name)
    return names


def extract_package(package: Path, dest: Path) -> list[str]:
    """把商店包解到 dest（文件名还原成原名），返回包里的文件名。"""
    with zipfile.ZipFile(package) as z:
        names = package_names(z)
        for info, name in zip(z.infolist(), names):
            if info.is_dir():
                continue
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                while chunk := src.read(1 << 20):
                    out.write(chunk)
    return names


def _asset_present(ref: str, names: set[str]) -> bool:
    """清单里引用的图标（assets\\X.png）：本名在包里，或有带限定的同名文件（X.scale-200.png、X.targetsize-48.png）。"""
    ref = ref.replace("\\", "/")
    stem, dot, ext = ref.rpartition(".")
    return ref in names or any(n.startswith(stem + ".") and n.endswith(dot + ext) for n in names)


def check_store_manifest(manifest: str, names: Iterable[str], identity: dict[str, str] | None = None,
                         version: str = build.VERSION) -> list[Item]:
    """商店包的清单 AppxManifest.xml：身份和合作伙伴中心一致、显示名、语言、只要完全信任一项权限、程序和图标在包里、没有签名。"""
    names = set(names)
    root = ET.fromstring(manifest)
    ident = root.find("m:Identity", APPX_NS)
    props = root.find("m:Properties", APPX_NS)
    app = root.find("m:Applications/m:Application", APPX_NS)
    if ident is None or props is None or app is None:
        return [(False, "商店包的清单缺 Identity、Properties 或 Application")]
    want_version = ".".join((version.split(".") + ["0", "0", "0"])[:3] + ["0"])
    got = {"identityName": ident.get("Name"), "publisher": ident.get("Publisher"),
           "publisherDisplayName": props.findtext("m:PublisherDisplayName", namespaces=APPX_NS)}
    wrong = [key for key, value in (identity or {}).items() if got.get(key) != value]
    items: list[Item] = [
        (not wrong and ident.get("Version") == want_version and ident.get("ProcessorArchitecture") == "x64",
         f"包标识 {got['identityName']}，发布者 {got['publisher']}（显示名 {got['publisherDisplayName']}），"
         f"版本 {ident.get('Version')}，{ident.get('ProcessorArchitecture')}"
         + (f"（和给定的不一致：{'、'.join(wrong)}）" if wrong else "")
         + ("" if ident.get("Version") == want_version else f"（版本应为 {want_version}：第四段留给商店，必须是 0）")),
    ]
    display = props.findtext("m:DisplayName", namespaces=APPX_NS)
    langs = [r.get("Language") for r in root.findall("m:Resources/m:Resource", APPX_NS)]
    items.append((display == build.APP_NAME and langs == [STORE_LANGUAGE],
                  f"显示名 {display}，语言 {'、'.join(filter(None, langs)) or '没写'}"
                  + ("" if display == build.APP_NAME else f"（显示名应为 {build.APP_NAME}，和商店里预留的名称一致）")
                  + ("" if langs == [STORE_LANGUAGE] else f"（语言应只有 {STORE_LANGUAGE}）")))
    family = root.find("m:Dependencies/m:TargetDeviceFamily", APPX_NS)
    items.append((None, "适用 " + (f"{family.get('Name')}，最低 {family.get('MinVersion')}，测到 {family.get('MaxVersionTested')}"
                                   if family is not None else "没写")))
    exe = (app.get("Executable") or "").replace("\\", "/")
    capabilities = root.find("m:Capabilities", APPX_NS)
    caps = [c.get("Name") for c in capabilities] if capabilities is not None else []
    items.append((exe in names and app.get("EntryPoint") == "Windows.FullTrustApplication" and caps == ["runFullTrust"],
                  f"程序 {exe}{'' if exe in names else '（包里没有）'}，桌面程序（完全信任），权限只有 {'、'.join(caps) or '没有'}"
                  + ("" if caps == ["runFullTrust"] else "（应只有 runFullTrust）")))
    visual = app.find("uap:VisualElements", APPX_NS)
    refs = [props.findtext("m:Logo", namespaces=APPX_NS)]
    if visual is not None:
        tile = visual.find("uap:DefaultTile", APPX_NS)
        refs += [visual.get("Square150x150Logo"), visual.get("Square44x44Logo"),
                 tile.get("Wide310x150Logo") if tile is not None else None]
    missing = [ref or "（没写）" for ref in refs if not ref or not _asset_present(ref, names)]
    items.append((not missing, f"图标 {len(refs)} 种都在包里" if not missing else f"图标缺：{_some(missing)}"))
    items.append(("AppxBlockMap.xml" in names, "有块映射 AppxBlockMap.xml" if "AppxBlockMap.xml" in names
                  else "没有块映射 AppxBlockMap.xml：不是完整的商店包"))
    signed = "AppxSignature.p7x" in names
    items.append((None, "包已签名（上传商店后由商店换成微软的签名）" if signed else "包没有签名：商店审核通过后由商店用微软的证书签名"))
    return items


def check_store_package(package: Path, identity: dict[str, str] | None = None, repo: Path = REPO) -> list[Item]:
    """商店包：先核对清单，再解到 build/installer/ 下的临时目录，对里面的 app\\ 逐项核对，核完删掉。"""
    package = Path(package)
    with zipfile.ZipFile(package) as z:
        names = package_names(z)
        if "AppxManifest.xml" not in names:
            return [(False, f"{package.name} 里没有 AppxManifest.xml：不是商店包")]
        manifest = z.read(z.infolist()[names.index("AppxManifest.xml")]).decode("utf-8")
    items = [(None, f"商店包 {package.name}，{package.stat().st_size / 1e6:.1f} MB，{len(names)} 个文件"),
             *check_store_manifest(manifest, names, identity)]
    build.WORK.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="store-check-", dir=build.WORK) as tmp:
        extract_package(package, Path(tmp))
        items += check(Path(tmp) / "app", repo)
    return items


def report(items: list[Item], out=say) -> bool:
    for ok, msg in items:
        out(("  ✓ " if ok else "  ✗ " if ok is False else "  · ") + msg)
    return all(ok is not False for ok, _ in items)


def main(argv: list[str] | None = None) -> int:
    utf8_output()
    args = sys.argv[1:] if argv is None else argv
    if args:
        root = Path(args[0])
    else:
        from . import smoke

        entries = smoke.installed()
        if len(entries) != 1 or not entries[0]["InstallLocation"]:
            raise SystemExit(f"“已安装”里没有唯一一项本软件（{len(entries)} 项）：请给出目录")
        root = Path(entries[0]["InstallLocation"])
    say(f"核对 {root}")
    ok = report(check_store_package(root) if root.suffix.lower() in PACKAGE_SUFFIXES else check(root))
    say("全部通过" if ok else "有不对的项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
