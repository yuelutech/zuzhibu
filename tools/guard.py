"""守护检查（docs/公开前检查.md）：字体、凭据、第三方 App 名称每次跑测试都查；转公开前再查全部历史、要移出的目录、照片许可、长摘录、本机路径。

    python tools/guard.py                           # 当前文件：字体、凭据、第三方 App 名称
    python tools/guard.py --history                 # 再查全部 git 历史（所有分支、标签、stash）的凭据和提交署名
    python tools/guard.py --release [--name 姓名]    # 转公开前的全部检查；维护者姓名（--name，或本机设置 maintainer_name）出现几处也查

GitHub 上的 PR 引用不在本地，查历史前先取下来：git fetch origin "+refs/pull/*:refs/remotes/origin/pull/*"。
有问题退出码 1。只用标准库。
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from zuzhibu import local  # noqa: E402

FONT_SUFFIXES = (".ttf", ".otf", ".ttc", ".woff", ".woff2", ".eot")
BINARY_SUFFIXES = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".docx", ".pptx", ".xlsx", ".zip", ".pdf") + FONT_SUFFIXES

#: 第三方 App 名称（第 2 条）：最早那份网页禁用名单，加即时通讯软件、网页测试里的另一个名称和聊天 App 英文名的小写（原公众号渲染器的标识）。
#: 用 base64 存，仓库里不出现明文。人物资料、译名表、来源标题里的公司和产品名是事实和引用，不查（is_data）。
BANNED_NAMES = tuple(base64.b64decode(b).decode("utf-8") for b in (
    "5b6u5L+h", "V2VDaGF0", "5oqW6Z+z", "5a2m5Lmg5by65Zu9", "5LuK5pel5aS05p2h", "VG91dGlhbw==",
    "5bCP57qi5Lmm", "V2VGbG93", "6ZKJ6ZKJ", "RGluZ1RhbGs=", "V1BT", "TWljcm9zb2Z0IFdvcmQ=", "d2VjaGF0",
))

#: 公开前要整目录移出的真实人物资料（第 3 条）。
MOVE_OUT = ("shared/skills/cadre-resume/rules", "bureaus/personnel-congress/import")

#: 凭据（第 4 条）。密钥要求大小写字母和数字都有，网址里 “…sk-…” 一类的英文片段不算。
_KEY = r"(?=[A-Za-z0-9_-]*[A-Z])(?=[A-Za-z0-9_-]*[a-z])(?=[A-Za-z0-9_-]*[0-9])[A-Za-z0-9_-]"
CREDENTIALS = {kind: re.compile(rx) for kind, rx in {
    "AI 接口密钥": rf"(?<![A-Za-z0-9_-])sk-(?:ant-|proj-|svcacct-|admin-)?{_KEY}{{32,}}",
    "代码托管令牌": r"\b(?:gh[pousr]_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{60,})\b",
    "云服务密钥": r"\b(?:(?:AKIA|ASIA)[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|AKID[0-9A-Za-z]{32}|LTAI[0-9A-Za-z]{12,24})\b",
    "机器人令牌": r"\bxox[abprs]-[0-9A-Za-z-]{10,}|(?:robot/send\?access_token=|/bot/v2/hook/|webhook/send\?key=|hooks\.slack\.com/services/)[A-Za-z0-9]|\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b",
    "私钥": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "JWT": r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    "Bearer 令牌": rf"(?i:bearer)\s+{_KEY}{{20,}}",
    "带密码的网址": r"\b[a-z][a-z0-9+.-]*://[^/\s:@'\"]+:[^/\s@'\"]{3,}@[^\s'\"]+",
    "密钥赋值": r"(?i:\b(?:api[_-]?key|apikey|secret(?:[_-]?key)?|access[_-]?token|refresh[_-]?token|id[_-]?token"
              r"|client[_-]?secret|passw(?:or)?d|pwd|auth[_-]?token|private[_-]?key))\b[\"']?\s*[:=]\s*[\"'][^\"'\s]{8,}[\"']",
}.items()}
_PLACEHOLDER = re.compile(r"(?i)example|placeholder|dummy|fake|test|your[_-]|xxxx|\*\*\*\*|<[^>]*>")
NOREPLY = ("@users.noreply.github.com", "noreply@github.com", "noreply@anthropic.com")

LOCAL_PATH = re.compile(r"(?<![A-Za-z0-9])[C-Z]:[\\/]+[^\s\"'`|<>]+")
EXCERPT_KEYS = ("excerpt", "quote", "text")
EXCERPT_MAX = 300  # 字符；超过的不算“短句”（第 6 条）
PUBLIC_LICENSES = ("公有领域", "CC0", "CC BY")


def tracked_files(root: Path = REPO) -> list[str]:
    """git 跟踪的文件（相对路径，/ 分隔）；不在 git 里时走目录树，跳过 .git、output。"""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True).stdout
        return [p for p in out.decode("utf-8").split("\0") if p]
    except (OSError, subprocess.CalledProcessError):
        skip = {".git", "output", "__pycache__"}
        return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                      if p.is_file() and not skip.intersection(p.relative_to(root).parts))


def is_data(path: str) -> bool:
    """人物资料、名册、来源和译名数据：里面的公司和产品名是事实和引用，不查第三方 App 名称。"""
    return (path.startswith(("data/", "bureaus/personnel-congress/import/"))
            or re.match(r"bureaus/[^/]+/(?:data|roster)/", path) is not None
            or (path.endswith(".csv") and path.startswith(("shared/glossary/", "tests/fixtures/"))))


def read_text(root: Path, path: str) -> str | None:
    """按 UTF-8 读文本；图片、docx 等二进制和读不了的返回 None。"""
    if path.lower().endswith(BINARY_SUFFIXES):
        return None
    try:
        data = (root / path).read_bytes()
    except OSError:
        return None
    if b"\0" in data[:8000]:
        return None
    return data.decode("utf-8", "replace")


def fonts(files: list[str]) -> list[str]:
    return [f for f in files if f.lower().endswith(FONT_SUFFIXES)]


def third_party_names(files: list[str], root: Path = REPO, names: tuple[str, ...] = BANNED_NAMES) -> list[tuple[str, str]]:
    """非数据文件里的第三方 App 名称：(文件, 名称)。"""
    found = []
    for f in files:
        if is_data(f):
            continue
        text = read_text(root, f)
        found += [(f, n) for n in names if text and n in text]
    return found


def credentials(text: str) -> list[tuple[str, str]]:
    """文本里像凭据的串：(类别, 打码后的开头)。示例、占位一类的不算。"""
    hits = []
    for kind, rx in CREDENTIALS.items():
        for m in rx.finditer(text):
            if not _PLACEHOLDER.search(m.group(0)):
                hits.append((kind, m.group(0)[:10] + "…"))
    return hits


def credential_files(files: list[str], root: Path = REPO) -> list[tuple[str, str, str]]:
    found = []
    for f in files:
        text = read_text(root, f)
        found += [(f, kind, head) for kind, head in credentials(text or "")]
    return found


def history(root: Path = REPO) -> tuple[list[tuple[str, str, str, str]], Counter]:
    """全部引用可达的文件版本和提交说明里的凭据 (对象, 路径, 类别, 开头)；不是无回复地址的提交署名及次数。"""
    def git(*args: str, inp: bytes | None = None) -> bytes:
        return subprocess.run(["git", "-C", str(root), *args], input=inp, capture_output=True, check=True).stdout

    paths: dict[str, str] = {}
    for line in git("rev-list", "--all", "--objects").decode("utf-8", "replace").splitlines():
        sha, _, path = line.partition(" ")
        paths.setdefault(sha, path)
    kinds = git("cat-file", "--batch-check", inp=("\n".join(paths) + "\n").encode()).decode().split("\n")
    by_type: dict[str, list[str]] = {"blob": [], "commit": []}
    for line in filter(None, kinds):
        sha, typ, _size = line.split()
        if typ in by_type:
            by_type[typ].append(sha)
    found, emails = [], Counter()
    for typ, shas in by_type.items():
        data = git("cat-file", "--batch", inp=("\n".join(shas) + "\n").encode())
        pos = 0
        while pos < len(data):
            nl = data.index(b"\n", pos)
            sha, _typ, size = data[pos:nl].decode().split()
            body = data[nl + 1:nl + 1 + int(size)]
            pos = nl + 1 + int(size) + 1
            if b"\0" in body[:8000]:
                continue
            text = body.decode("utf-8", "replace")
            if typ == "commit":
                head, _, text = text.partition("\n\n")
                for email in set(re.findall(r"^(?:author|committer) .*<([^>]*)>", head, re.M)):
                    if not email.endswith(NOREPLY):
                        emails[email] += 1
            found += [(sha[:10], paths.get(sha) or "（提交说明）", kind, h) for kind, h in credentials(text)]
    return found, emails


def portrait_licenses(root: Path = REPO) -> list[tuple[str, str]]:
    """照片登记表里许可不是公有领域、CC0 或 CC BY（不含 NC、ND）的：(人物编号, 许可)。"""
    path = root / "data" / "portraits" / "index.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    out = []
    for r in rows:
        lic = r.get("license") or ""
        token = re.split(r"[：:；;，,（(]", lic, maxsplit=1)[0]
        if not token.startswith(PUBLIC_LICENSES) or re.search(r"NC|ND", token):
            out.append((r["person_id"], lic))
    return out


def long_excerpts(files: list[str], root: Path = REPO) -> list[tuple[str, str, int]]:
    """人物资料里超过 EXCERPT_MAX 字符的来源摘录：(文件, 来源网站, 字符数)。"""
    found = []

    def walk(node, url, f):
        if isinstance(node, dict):
            url = node.get("url") or node.get("source_url") or url
            for k, v in node.items():
                if k in EXCERPT_KEYS and isinstance(v, str) and len(v) > EXCERPT_MAX:
                    found.append((f, urlparse(url or "").hostname or "（无链接）", len(v)))
                else:
                    walk(v, url, f)
        elif isinstance(node, list):
            for v in node:
                walk(v, url, f)

    for f in files:
        if f.endswith(".json") and is_data(f):
            try:
                walk(json.loads((root / f).read_text(encoding="utf-8-sig")), None, f)
            except (OSError, ValueError):
                continue
    return found


def is_federal(host: str) -> bool:
    return host.endswith((".gov", ".mil"))


def count_in_files(files: list[str], pattern: re.Pattern[str], root: Path = REPO) -> Counter:
    """每个文件里匹配几处。"""
    counts = Counter()
    for f in files:
        text = read_text(root, f)
        if text:
            n = len(pattern.findall(text))
            if n:
                counts[f] = n
    return counts


def area(path: str) -> str:
    parts = path.split("/")
    return "/".join(parts[:3]) if parts[0] == "bureaus" else "/".join(parts[:2]) if len(parts) > 2 else path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="守护检查：字体、凭据、第三方 App 名称；--history 查全部历史，--release 加查转公开前的各项。")
    ap.add_argument("--history", action="store_true", help="再查全部 git 历史的凭据和提交署名")
    ap.add_argument("--release", action="store_true", help="转公开前的全部检查（含 --history）")
    ap.add_argument("--name", default=local.get("maintainer_name"), help="维护者姓名：数它在当前文件里出现几处；默认读本机设置 maintainer_name，不进仓库")
    args = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    files = tracked_files()
    problems = 0

    def say(title: str, items: list[str], fail: bool = True) -> None:
        nonlocal problems
        if not items:
            print(f"{title}：通过")
            return
        problems += fail
        print(f"{title}：{len(items)} 处{'' if fail else '（提示）'}")
        for item in items[:30]:
            print(f"  {item}")
        if len(items) > 30:
            print(f"  ……另有 {len(items) - 30} 处")

    say("字体文件", fonts(files))
    say("凭据（当前文件）", [f"{f}：{kind} {head}" for f, kind, head in credential_files(files)])
    say("第三方 App 名称", sorted({f for f, _ in third_party_names(files)}))

    if args.history or args.release:
        found, emails = history()
        say("凭据（全部历史）", [f"{sha} {path}：{kind} {head}" for sha, path, kind, head in found])
        say("提交署名不是无回复地址", [f"{e}：{n} 个提交" for e, n in emails.most_common()])

    if args.release:
        say("要整目录移出的真实人物资料", [d for d in MOVE_OUT if any(f.startswith(d + "/") for f in files)])
        say("照片许可不是公有领域或 CC BY", [f"{pid}：{lic}" for pid, lic in portrait_licenses()])
        excerpts = long_excerpts(files)
        say(f"超过 {EXCERPT_MAX} 字符的摘录（非联邦政府来源）", [f"{f}：{h} {n} 字符" for f, h, n in excerpts if not is_federal(h)])
        say(f"超过 {EXCERPT_MAX} 字符的摘录（联邦政府来源，公有领域）", [f"{f}：{h} {n} 字符" for f, h, n in excerpts if is_federal(h)], fail=False)
        by_area = Counter()
        for f, n in count_in_files(files, LOCAL_PATH).items():
            by_area[area(f)] += n
        say("维护者本机路径", [f"{a}：{n} 处" for a, n in by_area.most_common()])
        if args.name:
            hits = count_in_files(files, re.compile(re.escape(args.name)))
            by_name = Counter()
            for f, n in hits.items():
                by_name[area(f)] += n
            say("维护者姓名", [f"{a}：{n} 处" for a, n in by_name.most_common()])

    print("有问题，见上。" if problems else "全部通过。")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
