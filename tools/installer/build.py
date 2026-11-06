"""构建 Windows 安装包：python -m tools.installer.build

    python -m tools.installer.build [--python-zip 嵌入版.zip] [--no-test] [--stage-only] [--store]

1. 取官方嵌入版 Python（python.org，版本和 SHA-256 写死在下面；下载一次缓存在 build/cache/python/，每次用前核对）；
2. 组装待装目录 build/installer/stage/：
   runtime\\  嵌入版 Python，python3xx._pth 加一行 ..\\pyapp，不导入 site，不读环境变量里的包；
   pyapp\\    程序、界面、写法检查、一局流水线、译名表，附带数据 data\\（含数据清单），dist.json；
   pyapp\\fonts\\  随软件分发的字体和许可证全文（fonts.py：从官方发布下载、核对 SHA-256，缓存在 build/cache/fonts/）；
3. 用嵌入版 Python 预编译 pyapp 下的 .py（checked-hash，装好后不再写 __pycache__），在嵌入版下做导入自检，
   再用嵌入版 Python 跑一遍仓库的单元测试（--no-test 跳过）；画图标 build/installer/zuzhibu.ico；
4. 桌面外壳（desktop/，Electron）：npm ci 装依赖，electron-builder 按 desktop/electron-builder.yml 打出
   dist/zuzhibu-<版本>-win-x64-setup.exe（一步安装、按用户安装、不弹管理员确认）和同名 .sha256。
   Electron、打包工具和 npm 的下载缓存都放 build/cache/。--stage-only 只做 1–3。
5. --store：再把同一个解包目录打成微软应用商店的商店包 dist/zuzhibu-<版本>-win-x64-store.appx 和同名 .sha256，
   逐项核对（tools/installer/contents.py）。商店包不签名，商店审核通过后由商店用微软的证书签名；
   包的身份三项要和合作伙伴中心的“产品标识”一致（下面的 STORE_IDENTITY_NAME 等）。

只能在 Windows 上构建。试装见 tools/installer/smoke.py，做法见 docs/INSTALL.md。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from zuzhibu import datasync
from zuzhibu.server import APP_NAME, VERSION
from zuzhibu.store import REPO

from . import fonts, icon, say, utf8_output

PYTHON_VERSION = "3.14.8"
PYTHON_ZIP = f"python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_ZIP}"
PYTHON_SHA256 = "a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310"  # python.org 发布的校验值

BUILD = REPO / "build"
CACHE = BUILD / "cache" / "python"
WORK = BUILD / "installer"
DESKTOP = REPO / "desktop"
APP_DIR = "pyapp"  # 外壳的 resources\pyapp；不叫 app，免得和 Electron 自己的 resources\app 混
FONT_DIR = "fonts"  # 随软件分发的字体放 pyapp\fonts\

# 微软应用商店的商店包（--store）。在合作伙伴中心预留了名称以后，到“产品管理 → 产品标识”页一字不差地抄（区分大小写）
# Package/Identity/Name、Package/Identity/Publisher、Package/Properties/PublisherDisplayName 三项。
# 它们是公开信息（装好的商店包里本来就看得到），不是密钥；没填时 --store 停下，也可以用 --store-identity、--store-publisher 临时给。
STORE_IDENTITY_NAME = "YueluTech.AI"
STORE_PUBLISHER = "CN=3A0F8D6F-FD64-4A62-B60A-D9B571E4A4F2"
STORE_PUBLISHER_DISPLAY_NAME = "Yuelu Tech"

# 装进 pyapp\ 的内容：整棵目录，加单个文件。仓库里的测试、样张、各局原料、暂缓的秘书局和宣传教育局不装；
# 写法规范 shared/skills/ 运行时用不到，也不装。
APP_TREES = ("zuzhibu", "web", "shared/backends", "shared/glossary", "shared/render", "shared/schema",
             "tools/lint", "bureaus/dispatch")
APP_FILES = ("LICENSE", "README.md", "SECURITY.md", "shared/README.md",
             "bureaus/personnel-sv/roster/roster.csv",
             "bureaus/personnel-others/roster/roster.csv")  # 两份名册：来源分级要读官网域名
SKIP_DIRS = {"__pycache__", "tests", "examples", ".git", ".pytest_cache"}
SKIP_FILES = {"build_examples.py", "daily.ps1"}
SKIP_SUFFIXES = (".pyc", ".pyo", ".log", ".tmp", ".bak")
FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".woff", ".woff2")


def log(msg: str) -> None:
    say(f"[{dt.datetime.now():%H:%M:%S}] {msg}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ──────────────────────────────────────────────────────────────── 运行环境


def fetch_python(cache: Path = CACHE) -> Path:
    """缓存里有且校验对就用；没有就从 python.org 下载，校验对了才放进缓存。"""
    target = cache / PYTHON_ZIP
    if target.is_file():
        if sha256(target) == PYTHON_SHA256:
            return target
        log(f"缓存的 {PYTHON_ZIP} 校验不对，重新下载")
        target.unlink()
    cache.mkdir(parents=True, exist_ok=True)
    log(f"下载 {PYTHON_URL}")
    fd, part = tempfile.mkstemp(prefix="python-", suffix=".part", dir=cache)
    try:
        req = urllib.request.Request(PYTHON_URL, headers={"User-Agent": f"zuzhibu-build/{VERSION}"})
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(req, timeout=120) as resp:
            shutil.copyfileobj(resp, out)
        got = sha256(Path(part))
        if got != PYTHON_SHA256:
            raise SystemExit(f"{PYTHON_ZIP} 校验不符：{got}，应为 {PYTHON_SHA256}")
        os.replace(part, target)
    finally:
        if os.path.exists(part):
            os.unlink(part)
    return target


def unpack_runtime(archive: Path, runtime: Path, app_dir: str = APP_DIR) -> str:
    """解开嵌入版 Python，改写 ._pth：标准库、运行环境目录、..\\pyapp。返回版本号前缀（如 python314）。"""
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            name = info.filename
            if name.startswith(("/", "\\")) or ".." in Path(name).parts or ":" in name:
                raise SystemExit(f"嵌入版压缩包里有可疑路径：{name}")
        z.extractall(runtime)
    pths = sorted(runtime.glob("python3*._pth"))
    if len(pths) != 1 or not (runtime / "python.exe").is_file():
        raise SystemExit("嵌入版 Python 内容不对：找不到 python3xx._pth 或 python.exe")
    stem = pths[0].stem
    pths[0].write_bytes(f"{stem}.zip\r\n.\r\n..\\{app_dir}\r\n\r\n# no site: ignore user site-packages and PYTHONPATH\r\n".encode("ascii"))
    return stem


# ──────────────────────────────────────────────────────────────── 程序与数据


def app_files(repo: Path = REPO) -> list[str]:
    """要装进 pyapp\\ 的文件（相对仓库根，/ 分隔）。"""
    out = []
    for tree in APP_TREES:
        root = repo / tree
        if not root.is_dir():
            raise SystemExit(f"仓库里没有 {tree}")
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(filenames):
                if name in SKIP_FILES or name.endswith(SKIP_SUFFIXES) or name.startswith("."):
                    continue
                out.append(Path(dirpath, name).relative_to(repo).as_posix())
    for rel in APP_FILES:
        if not (repo / rel).is_file():
            raise SystemExit(f"仓库里没有 {rel}")
        out.append(rel)
    found = [rel for rel in out if rel.lower().endswith(FONT_SUFFIXES)]
    if found:
        raise SystemExit("仓库里的字体文件不进安装包（随软件分发的字体由 fonts.py 从官方发布下载）：" + "、".join(found))
    return sorted(set(out))


def copy_data(src: Path, dst: Path) -> dict:
    """附带数据：复制 data\\，核对数据清单；清单和文件对不上就为安装包重算一份（不改仓库里的）。"""
    for rel in datasync.iter_files(src):
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, target)
    committed = datasync.load_manifest(src / datasync.MANIFEST)
    manifest = datasync.build_manifest(dst, committed)
    if committed is None or manifest["generated_at"] != committed["generated_at"]:
        log("data/manifest.json 和数据文件对不上，为安装包重算了清单（提交数据前请运行 python -m zuzhibu.datasync manifest）")
    (dst / datasync.MANIFEST).write_text(datasync.dumps_manifest(manifest), encoding="utf-8", newline="\n")
    return manifest


def git_commit(repo: Path = REPO) -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short=12", "HEAD"], cwd=repo, capture_output=True, text=True, timeout=20)
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=repo, capture_output=True, text=True, timeout=60)
        return out.stdout.strip() + ("（有未提交改动）" if dirty.stdout.strip() else "") if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def stage_app(stage: Path, repo: Path = REPO) -> dict:
    app = stage / APP_DIR
    files = app_files(repo)
    for rel in files:
        target = app / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / rel, target)
    manifest = copy_data(repo / "data", app / "data")
    fonts.stage(app / FONT_DIR)
    dist = {
        "name": APP_NAME, "version": VERSION, "built_at": dt.datetime.now().isoformat(timespec="seconds"),
        "commit": git_commit(repo), "python": PYTHON_VERSION, "shell": "electron " + shell_versions()["electron"],
        "data_date": manifest.get("data_date", ""), "data_generated_at": manifest["generated_at"],
        "sources": list(datasync.DEFAULT_SOURCES),
        "fonts": [{"name": f.name, "version": f.version, "file": f"{FONT_DIR}/{f.file}", "license": f"{FONT_DIR}/{f.license_file}"}
                  for f in fonts.FONTS],
    }
    (app / "dist.json").write_text(json.dumps(dist, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log(f"程序文件 {len(files)} 个，字体 {len(fonts.FONTS)} 款，附带数据 {len(manifest['files'])} 个文件（数据日期 {manifest.get('data_date')}）")
    return dist


def precompile_and_check(stage: Path, work: Path, run_tests: bool, repo: Path = REPO) -> None:
    py = stage / "runtime" / "python.exe"
    flags = {"cwd": work, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    proc = subprocess.run([str(py), "-I", "-m", "compileall", "-q", "-j", "0", "--invalidation-mode", "checked-hash",
                           str(stage / APP_DIR)], capture_output=True, text=True, errors="replace", **flags)
    if proc.returncode != 0:
        raise SystemExit(f"预编译失败：{proc.stdout[-3000:]}{proc.stderr[-3000:]}")
    modules = ["zuzhibu.__main__", "zuzhibu.server", "zuzhibu.datasync", "zuzhibu.desktop", "zuzhibu.resume",
               "shared.render.docx_redhead", "shared.render.article_html", "shared.render.card_html",
               "tools.lint.cadre_lint", "bureaus.dispatch.run", "ssl", "sqlite3", "ctypes"]
    code = "import importlib, sys\nfor m in sys.argv[1:]: importlib.import_module(m)\nimport ssl; ssl.create_default_context()\nprint('ok', sys.version.split()[0])"
    proc = subprocess.run([str(py), "-I", "-c", code, *modules], capture_output=True, text=True, errors="replace", **flags)
    if proc.returncode != 0 or not proc.stdout.startswith("ok"):
        raise SystemExit(f"嵌入版 Python 导入自检失败：{proc.stdout[-3000:]}{proc.stderr[-3000:]}")
    log(f"嵌入版 Python {proc.stdout.split()[1]} 导入自检通过")
    if run_tests:
        log("用嵌入版 Python 跑单元测试…")
        proc = subprocess.run([str(py), "-m", "unittest", "discover", "-s", str(repo / "tests"), "-t", str(repo)],
                              cwd=repo, capture_output=True, text=True, errors="replace",
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        if proc.returncode != 0:
            raise SystemExit("嵌入版 Python 下单元测试没通过：\n" + (proc.stderr or proc.stdout)[-6000:])
        log("单元测试通过：" + " ".join(tail))


# ──────────────────────────────────────────────────────────────── 桌面外壳


def shell_versions(desktop: Path = DESKTOP) -> dict[str, str]:
    """desktop/package.json 的版本号和 Electron 版本；版本号要和 zuzhibu/server.py 的 VERSION 一致。"""
    pkg = json.loads((desktop / "package.json").read_text(encoding="utf-8"))
    return {"version": pkg["version"], "electron": pkg["devDependencies"]["electron"]}


def tool_env() -> dict[str, str]:
    """npm、Electron、electron-builder 的下载缓存都放 build/cache（仓库已忽略），不写 C 盘的用户目录；不找证书签名。"""
    cache = BUILD / "cache"
    return dict(os.environ, npm_config_cache=str(cache / "npm"), ELECTRON_CACHE=str(cache / "electron"),
                electron_config_cache=str(cache / "electron"), ELECTRON_BUILDER_CACHE=str(cache / "electron-builder"),
                CSC_IDENTITY_AUTO_DISCOVERY="false")


def npm_ci(desktop: Path = DESKTOP) -> None:
    """按锁文件装外壳的依赖；锁文件没变就不重装。"""
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise SystemExit("构建安装包要用 Node.js（npm）：请先安装 Node.js 24 或更新版本")
    lock = desktop / "package-lock.json"
    stamp = desktop / "node_modules" / ".zuzhibu-lock-sha256"
    digest = sha256(lock)
    if stamp.is_file() and stamp.read_text(encoding="ascii").strip() == digest:
        return
    log("npm ci：按锁文件装桌面外壳的依赖（首次要下载 Electron，百兆左右）…")
    proc = subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=desktop, env=tool_env())
    if proc.returncode != 0:
        raise SystemExit("npm ci 失败")
    stamp.write_text(digest, encoding="ascii")


def electron_build(out_dir: Path, version: str = VERSION, desktop: Path = DESKTOP) -> Path:
    """electron-builder 打 NSIS 安装包，返回安装程序路径。out_dir 里还会有解包目录 win-unpacked 等过程文件。"""
    node = shutil.which("node")
    cli = desktop / "node_modules" / "electron-builder" / "cli.js"
    if not node or not cli.is_file():
        raise SystemExit("没有 Node.js 或 electron-builder：先运行 npm ci（在 desktop/ 里）")
    cmd = [node, str(cli), "--win", "nsis", "--x64", "--projectDir", str(desktop),
           f"-c.directories.output={out_dir}", f"-c.extraMetadata.version={version}"]
    proc = subprocess.run(cmd, cwd=desktop, env=tool_env())
    exe = out_dir / f"zuzhibu-{version}-win-x64-setup.exe"
    if proc.returncode != 0 or not exe.is_file():
        raise SystemExit("electron-builder 打包失败")
    return exe


# ──────────────────────────────────────────────────────────────── 商店包


def store_identity(name: str = STORE_IDENTITY_NAME, publisher: str = STORE_PUBLISHER,
                   display_name: str = STORE_PUBLISHER_DISPLAY_NAME) -> dict[str, str]:
    """商店包的身份三项（electron-builder 的 appx.identityName、appx.publisher、appx.publisherDisplayName），先核对写法。"""
    if not name or not publisher:
        raise SystemExit("商店包要合作伙伴中心“产品标识”页的身份三项：填在 tools/installer/build.py 的 STORE_IDENTITY_NAME、"
                         "STORE_PUBLISHER，或用 --store-identity、--store-publisher 给（见 docs/INSTALL.md“微软应用商店”）")
    if not re.fullmatch(r"[A-Za-z0-9.-]{3,50}", name):
        raise SystemExit(f"商店包的 Package/Identity/Name 只能是 3–50 个英文字母、数字、点和连字符：{name}")
    if not re.fullmatch(r"CN=[^,\"]+", publisher):
        raise SystemExit(f"商店包的 Package/Identity/Publisher 应形如 CN=XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX：{publisher}")
    if not display_name.strip():
        raise SystemExit("商店包的 PublisherDisplayName 不能空着，要和合作伙伴中心的发布者显示名一致")
    return {"identityName": name, "publisher": publisher, "publisherDisplayName": display_name.strip()}


def store_command(node: str, cli: Path, unpacked: Path, out_dir: Path, resources: Path, identity: dict[str, str],
                  version: str = VERSION, desktop: Path = DESKTOP) -> list[str]:
    """electron-builder 把已打好的解包目录（--prepackaged，不重新打）做成商店包：appx 目标，图标在 resources\\appx\\。"""
    return [node, str(cli), "--win", "appx", "--x64", "--projectDir", str(desktop), "--prepackaged", str(unpacked),
            f"-c.directories.output={out_dir}", f"-c.directories.buildResources={resources}",
            f"-c.extraMetadata.version={version}", *(f"-c.appx.{key}={value}" for key, value in identity.items())]


def store_build(unpacked: Path, out_dir: Path, identity: dict[str, str], version: str = VERSION,
                desktop: Path = DESKTOP) -> Path:
    """商店包：画好商店图标，打出 .appx（不签名，商店审核通过后由商店签名），返回路径。"""
    node = shutil.which("node")
    cli = desktop / "node_modules" / "electron-builder" / "cli.js"
    if not node or not cli.is_file():
        raise SystemExit("没有 Node.js 或 electron-builder：先运行 npm ci（在 desktop/ 里）")
    if not (unpacked / "resources" / APP_DIR / "dist.json").is_file():
        raise SystemExit(f"没有打好的解包目录：{unpacked}")
    resources = WORK / "store-resources"
    if resources.exists():
        shutil.rmtree(resources)
    (resources / "appx").mkdir(parents=True)
    for name, data in icon.appx_assets().items():
        (resources / "appx" / name).write_bytes(data)
    package = out_dir / f"zuzhibu-{version}-win-x64-store.appx"
    if package.exists():
        package.unlink()
    proc = subprocess.run(store_command(node, cli, unpacked, out_dir, resources, identity, version, desktop),
                          cwd=desktop, env=tool_env())
    if proc.returncode != 0 or not package.is_file():
        raise SystemExit("electron-builder 打商店包失败")
    return package


def publish_copy(built: Path, out_dir: Path) -> tuple[Path, str]:
    """成品复制到输出目录，旁边写同名 .sha256。"""
    out = out_dir / built.name
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built, out)
    digest = sha256(out)
    Path(str(out) + ".sha256").write_text(f"{digest}  {out.name}\n", encoding="ascii")
    return out, digest


# ──────────────────────────────────────────────────────────────── 主流程


def prepare_stage(python_zip: str | None = None, run_tests: bool = True) -> dict:
    """1–3 步：嵌入版 Python、程序与附带数据、预编译与自检、图标。返回 dist.json 的内容。"""
    archive = Path(python_zip) if python_zip else fetch_python()
    if sha256(archive) != PYTHON_SHA256:
        raise SystemExit(f"{archive} 不是 python.org 的 {PYTHON_ZIP}（SHA-256 不符）")
    stage = WORK / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    unpack_runtime(archive, stage / "runtime")
    dist = stage_app(stage)
    precompile_and_check(stage, WORK, run_tests=run_tests)
    (WORK / "zuzhibu.ico").write_bytes(icon.ico())
    return dist


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.installer.build", description=f"构建 {APP_NAME} Windows 安装包")
    ap.add_argument("--python-zip", help=f"已下载的 {PYTHON_ZIP}（核对 SHA-256 后使用）；默认从 python.org 下载并缓存")
    ap.add_argument("--out", default=str(REPO / "dist"), help="安装包输出目录，默认 dist/")
    ap.add_argument("--no-test", action="store_true", help="不用嵌入版 Python 跑单元测试")
    ap.add_argument("--stage-only", action="store_true", help="只备好嵌入版 Python、程序和附带数据，不打安装包")
    ap.add_argument("--store", action="store_true", help="另打微软应用商店的商店包（.appx，不签名，商店审核通过后由商店签名）")
    ap.add_argument("--store-identity", default=STORE_IDENTITY_NAME, help="商店包的 Package/Identity/Name，默认 STORE_IDENTITY_NAME")
    ap.add_argument("--store-publisher", default=STORE_PUBLISHER, help="商店包的 Package/Identity/Publisher（CN=…），默认 STORE_PUBLISHER")
    args = ap.parse_args(argv)
    utf8_output()
    if sys.platform != "win32":
        raise SystemExit("安装包只能在 Windows 上构建")
    shell = shell_versions()
    if shell["version"] != VERSION:
        raise SystemExit(f"desktop/package.json 的版本号 {shell['version']} 和 zuzhibu/server.py 的 {VERSION} 不一致")
    identity = store_identity(args.store_identity, args.store_publisher) if args.store and not args.stage_only else None
    started = time.time()

    dist = prepare_stage(args.python_zip, run_tests=not args.no_test)
    if args.stage_only:
        log(f"待装目录已备好：{WORK / 'stage'}")
        return 0
    npm_ci()
    log(f"electron-builder 打安装包（Electron {shell['electron']}，压缩要几分钟）…")
    out, digest = publish_copy(electron_build(WORK / "electron"), Path(args.out))
    log(f"安装包：{out}（{out.stat().st_size / 1e6:.1f} MB，SHA-256 {digest}），附带数据 {dist['data_date']}")
    if identity:
        from . import contents  # contents 也引用本模块，放到这里再引

        log("electron-builder 打商店包…")
        package = store_build(WORK / "electron" / "win-unpacked", WORK / "electron", identity)
        log("核对商店包：")
        if not contents.report(contents.check_store_package(package, identity)):
            raise SystemExit("商店包核对没通过")
        out, digest = publish_copy(package, Path(args.out))
        log(f"商店包：{out}（{out.stat().st_size / 1e6:.1f} MB，SHA-256 {digest}），没有签名，商店审核通过后由商店签名")
    log(f"完成，用时 {time.time() - started:.0f} 秒")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
