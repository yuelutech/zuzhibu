"""数据分发：维护者生成 data/manifest.json，软件联网取 main 上的最新 data/（docs/PRODUCT.md 第七节）。

    python -m zuzhibu.datasync manifest [--data 数据目录]              # 维护者：重算清单，每日更新提交前运行
    python -m zuzhibu.datasync check [--home 本机数据目录] [--url 数据地址] [--bundled 附带数据]
    python -m zuzhibu.datasync status [--home 本机数据目录]

清单列出 data/ 下每个文件的 SHA-256 和字节数，加生成时间（UTC）和数据日期。软件的本机数据目录
（安装版默认 %LOCALAPPDATA%\\zuzhibu\\data）：

    current.json           指向当前在用的快照
    snapshots/<快照>/       一份完整数据（people、events、portraits、meta.json、manifest.json）
    staging/<SHA-256>       已下载并核对过的文件，按内容存；中断后下次接着用
    sync.json              最近一次联网的结果

更新：取清单 → 比对当前快照的清单，列出发布方新增、改动、删除的文件 → 只下载这些文件，逐个核对字节数和 SHA-256 →
全部到齐后另建一份快照（没变的文件用硬链接，不另占空间），试读一遍 → 原子替换 current.json 切换过去 → 删掉旧快照。
任何一步失败或中断，current.json 仍指着原来那份完整快照，本机数据不受影响。
发布方没改的文件不动，本机“立即更新”写进来的改动保留；发布方改过的文件以发布的为准，本机写回过的动态
在新快照试读之前按同一套写回规则重放回去（本机更新目录在数据目录旁的 local\\，见 zuzhibu/localupdates.py）。
联网只发 GET 请求，请求头只有软件名和版本，不带任何用户信息。

只用标准库。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import gzip
import hashlib
import http.client
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Any, Callable, Iterable

from .store import DEFAULT_DATA, Store, dumps, read_json

SCHEMA = "zuzhibu-manifest/1"
MANIFEST = "manifest.json"
DEFAULT_SOURCES = ("https://raw.githubusercontent.com/yuelutech/zuzhibu/main/data/",)
USER_AGENT = "zuzhibu-datasync/1"
OLDEST = "1970-01-01T00:00:00Z"
HOME_ENV = "ZUZHIBU_HOME"

TOP_DIRS = ("people", "events", "portraits", "rosters")
EXTENSIONS = (".json", ".csv", ".jpg", ".jpeg", ".png", ".webp")
MAX_FILE = 16 * 1024 * 1024
MAX_MANIFEST = 8 * 1024 * 1024
MAX_FILES = 100_000
INTERVAL = 24 * 3600

_PART = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
_SHA = re.compile(r"^[0-9a-f]{64}$")
_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(10)), *(f"LPT{i}" for i in range(10))}


class SyncError(Exception):
    """kind：network（网络不通、超时、中断）、http（地址返回错误码）、verify（校验不符）、format（清单不合规）、disk（本机写入失败）。"""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def local_now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# ──────────────────────────────────────────────────────────────── 清单


def safe_path(rel: Any) -> bool:
    """清单里的路径只能是 meta.json 或 people/、events/、portraits/、rosters/ 下的文件，不许 ..、反斜杠、设备名、大小写冲突以外的花样。"""
    if not isinstance(rel, str) or not rel or rel != rel.strip() or "\\" in rel:
        return False
    if rel == "meta.json":
        return True
    parts = rel.split("/")
    if parts[0] not in TOP_DIRS or not 2 <= len(parts) <= 3:
        return False
    for part in parts:
        if not _PART.match(part) or part.split(".")[0].upper() in _RESERVED:
            return False
    return parts[-1].lower().endswith(EXTENSIONS)


def file_digest(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def iter_files(root: Path) -> Iterable[str]:
    """数据目录里的文件（相对路径，/ 分隔），跳过清单本身、临时文件和隐藏文件。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith(".") or name.endswith((".part", ".tmp")):
                continue
            rel = Path(dirpath, name).relative_to(root).as_posix()
            if rel != MANIFEST:
                yield rel


def build_manifest(data_dir: str | os.PathLike, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """按数据目录现状算清单。文件和上一份清单完全相同时沿用上一份的生成时间，避免每天无谓地改清单。"""
    root = Path(data_dir)
    files: dict[str, dict[str, Any]] = {}
    skipped = []
    for rel in iter_files(root):
        if not safe_path(rel):
            skipped.append(rel)
            continue
        sha, size = file_digest(root / rel)
        files[rel] = {"sha256": sha, "size": size}
    if skipped:
        print("清单未收（路径不合分发规则）：" + "、".join(skipped), file=sys.stderr)
    if "meta.json" not in files:
        raise SyncError("format", f"{root} 没有 meta.json，不是数据目录")
    meta = read_json(root / "meta.json")
    same = previous is not None and previous.get("files") == files
    return {
        "schema": SCHEMA,
        "generated_at": previous["generated_at"] if same else utc_now(),
        "data_date": meta.get("updated_at") or "",
        "baseline": meta.get("baseline") or "",
        "counts": {"people": sum(1 for rel in files if rel.startswith("people/")),
                   "events": sum(1 for rel in files if rel.startswith("events/")),
                   "portraits": sum(1 for rel in files if rel.startswith("portraits/") and not rel.endswith(".csv"))},
        "files": files,
    }


def dumps_manifest(manifest: dict[str, Any]) -> str:
    """一行一个文件，便于在提交里逐条比对。"""
    head = {k: v for k, v in manifest.items() if k != "files"}
    lines = ["{"]
    lines += [f"  {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}," for k, v in head.items()]
    lines.append('  "files": {')
    items = sorted(manifest["files"].items())
    for i, (rel, entry) in enumerate(items):
        comma = "," if i < len(items) - 1 else ""
        lines.append(f'    {json.dumps(rel)}: {{"sha256": "{entry["sha256"]}", "size": {int(entry["size"])}}}{comma}')
    lines += ["  }", "}"]
    return "\n".join(lines) + "\n"


def write_manifest(data_dir: str | os.PathLike) -> tuple[dict[str, Any], bool]:
    """重算并写 data_dir/manifest.json；返回 (清单, 是否有变化)。"""
    root = Path(data_dir)
    path = root / MANIFEST
    previous = None
    if path.exists():
        try:
            previous = parse_manifest(path.read_bytes())
        except SyncError:
            previous = None
    manifest = build_manifest(root, previous)
    text = dumps_manifest(manifest)
    changed = not path.exists() or path.read_text(encoding="utf-8") != text
    if changed:
        _write_text(path, text)
    return manifest, changed


def parse_manifest(body: bytes) -> dict[str, Any]:
    if len(body) > MAX_MANIFEST:
        raise SyncError("format", "清单过大")
    try:
        data = json.loads(body.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SyncError("format", f"清单不是合法 JSON：{exc}") from None
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise SyncError("format", "清单格式不认识")
    if not isinstance(data.get("generated_at"), str) or not _STAMP.match(data["generated_at"]):
        raise SyncError("format", "清单缺生成时间")
    files = data.get("files")
    if not isinstance(files, dict) or not files or len(files) > MAX_FILES:
        raise SyncError("format", "清单没有文件表")
    seen: set[str] = set()
    for rel, entry in files.items():
        if not safe_path(rel):
            raise SyncError("format", f"清单里有不合规的路径：{rel!r}")
        if rel.lower() in seen:
            raise SyncError("format", f"清单里有重名文件：{rel}")
        seen.add(rel.lower())
        if not (isinstance(entry, dict) and isinstance(entry.get("sha256"), str) and _SHA.match(entry["sha256"])
                and isinstance(entry.get("size"), int) and 0 <= entry["size"] <= MAX_FILE):
            raise SyncError("format", f"清单里 {rel} 的校验值不合规")
    if "meta.json" not in files:
        raise SyncError("format", "清单里没有 meta.json")
    return data


def load_manifest(path: Path) -> dict[str, Any] | None:
    try:
        return parse_manifest(path.read_bytes())
    except (OSError, SyncError):
        return None


def manifest_id(manifest: dict[str, Any]) -> str:
    canon = json.dumps(manifest["files"], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def is_newer(a: dict[str, Any], b: dict[str, Any] | None) -> bool:
    """a 比 b 新：生成时间更晚（同一时间但文件不同的，也算新）。"""
    if b is None:
        return True
    if a["generated_at"] != b["generated_at"]:
        return a["generated_at"] > b["generated_at"]
    return manifest_id(a) != manifest_id(b)


def plan(new: dict[str, Any], cur: dict[str, Any] | None, base: Path | None) -> tuple[list[str], list[str]]:
    """要下载的文件（发布方新增或改动的，以及本机缺的）和要删除的文件（发布方删掉的）。"""
    old = (cur or {}).get("files") or {}
    puts = [rel for rel, entry in sorted(new["files"].items())
            if (old.get(rel) or {}).get("sha256") != entry["sha256"] or base is None or not (base / rel).is_file()]
    deletes = sorted(rel for rel in old if rel not in new["files"])
    return puts, deletes


# ──────────────────────────────────────────────────────────────── 来源


def replace(src: str | os.PathLike, dst: str | os.PathLike, attempts: int = 40) -> None:
    """os.replace。Windows 上目标正被别的线程读、或刚建的文件正被杀毒软件扫描时会被拒绝，稍等重试。"""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.02 * (1 + i // 10))


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class HttpSource:
    """一个数据地址：<地址>manifest.json、<地址>people/x.json……地址以 / 结尾。"""

    def __init__(self, base: str, timeout: float = 20, user_agent: str = USER_AGENT):
        self.base = normalize_url(base)
        self.timeout = timeout
        self.user_agent = user_agent
        # 本机地址不走系统代理；其余照系统代理设置
        local = urllib.parse.urlsplit(self.base).hostname in ("127.0.0.1", "localhost", "::1")
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()

    def __repr__(self) -> str:
        return self.base

    def _open(self, rel: str, headers: dict[str, str] | None = None):
        url = self.base + urllib.parse.quote(rel)
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip",
                                                   "Cache-Control": "no-cache", **(headers or {})})
        try:
            return self.opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            exc.close()
            if exc.code == 304:
                return None
            hint = "：仓库公开前属正常" if exc.code in (401, 403, 404) else ""
            raise SyncError("http", f"数据地址返回 {exc.code}{hint}") from None
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise SyncError("network", _reason(exc)) from None

    def manifest(self, etag: str | None = None) -> tuple[bytes | None, str | None]:
        """返回 (清单原文, ETag)；地址没变化时（304）原文是 None。"""
        resp = self._open(MANIFEST, {"If-None-Match": etag} if etag else None)
        if resp is None:
            return None, etag
        with resp:
            body = _read_all(resp, MAX_MANIFEST)
            return body, resp.headers.get("ETag")

    def fetch(self, rel: str, out, size: int) -> None:
        resp = self._open(rel)
        if resp is None:
            raise SyncError("http", "数据地址返回 304")
        with resp:
            _copy_stream(resp, out, size)


class LocalSource:
    """随软件附带的数据目录（安装包里的 app/data），首次运行和升级后用它铺底。"""

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)

    def __repr__(self) -> str:
        return str(self.root)

    def manifest(self, etag: str | None = None) -> tuple[bytes | None, str | None]:
        path = self.root / MANIFEST
        if path.exists():
            return path.read_bytes(), None
        return dumps_manifest({**build_manifest(self.root), "generated_at": OLDEST}).encode("utf-8"), None

    def fetch(self, rel: str, out, size: int) -> None:
        try:
            fh = open(self.root / rel, "rb")
        except OSError as exc:
            raise SyncError("disk", f"读不到附带数据 {rel}：{exc}") from None
        with fh:
            _copy_stream(fh, out, size, short="verify")


def _reason(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.URLError):
        exc = exc.reason if isinstance(exc.reason, BaseException) else exc
    if isinstance(exc, TimeoutError):
        return "连接超时"
    if isinstance(exc, ConnectionRefusedError):
        return "连不上数据地址"
    if isinstance(exc, (ConnectionError, http.client.IncompleteRead, EOFError, zlib.error)):
        return "连接中断"
    if (isinstance(exc, OSError) and getattr(exc, "winerror", None) == 11001) or "getaddrinfo" in str(exc):
        return "网络不通（找不到服务器）"
    return f"网络不通（{type(exc).__name__}：{exc}）"[:200]


def _decoded(resp):
    if (resp.headers.get("Content-Encoding") or "").lower() == "gzip":
        return gzip.GzipFile(fileobj=resp)
    return resp


def _read_all(resp, limit: int) -> bytes:
    stream = _decoded(resp)
    try:
        body = stream.read(limit + 1)
    except (OSError, http.client.HTTPException, EOFError, zlib.error) as exc:
        raise SyncError("network", _reason(exc)) from None
    if len(body) > limit:
        raise SyncError("format", "内容超过上限")
    return body


def _copy_stream(resp, out, size: int, short: str = "network") -> None:
    """读到 out，应当正好 size 字节。连接中断、超时、提前断开算 network（附带数据读短了算 short 给的类别），
    写盘失败由 out 报 disk，比清单大算 verify。"""
    stream = _decoded(resp) if hasattr(resp, "headers") else resp
    total = 0
    while True:
        try:
            chunk = stream.read(1 << 16)
        except (OSError, http.client.HTTPException, EOFError, zlib.error) as exc:
            raise SyncError("network", _reason(exc)) from None
        if not chunk:
            break
        total += len(chunk)
        if total > size:
            raise SyncError("verify", "文件比清单登记的大")
        out.write(chunk)
    if total < size:
        raise SyncError(short, f"连接中断（收到 {total}/{size} 字节）" if short == "network" else f"文件比清单登记的小（{total}/{size} 字节）")


def normalize_url(url: str) -> str:
    """数据地址：https，或本机测试用的 http://127.0.0.1、http://localhost；补齐结尾的 /。"""
    url = (url or "").strip()
    parts = urllib.parse.urlsplit(url)
    local = parts.hostname in ("127.0.0.1", "localhost", "::1")
    if parts.scheme not in ("https", "http") or not parts.netloc or parts.scheme == "http" and not local:
        raise ValueError("数据地址要用 https://（本机测试可用 http://127.0.0.1）")
    if parts.query or parts.fragment:
        raise ValueError("数据地址不能带 ? 或 #")
    return url if url.endswith("/") else url + "/"


# ──────────────────────────────────────────────────────────────── 本机数据目录


class DataHome:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.snapshots = self.root / "snapshots"
        self.staging = self.root / "staging"
        self.pointer = self.root / "current.json"
        self.state_file = self.root / "sync.json"
        self._links = True

    # 当前快照

    def pointer_data(self) -> dict[str, Any]:
        try:
            data = read_json(self.pointer)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def current(self) -> Path | None:
        """当前快照目录；指针坏了就找最新一份完整快照。"""
        name = self.pointer_data().get("snapshot")
        if isinstance(name, str) and _PART.match(name):
            path = self.snapshots / name
            if self._complete(path):
                return path
        best = None
        if self.snapshots.is_dir():
            for path in sorted(self.snapshots.iterdir()):
                if path.is_dir() and not path.name.endswith(".partial") and self._complete(path):
                    best = path
        return best

    def current_manifest(self) -> dict[str, Any] | None:
        path = self.current()
        return load_manifest(path / MANIFEST) if path else None

    @staticmethod
    def _complete(path: Path) -> bool:
        return (path / "meta.json").is_file() and (path / MANIFEST).is_file()

    def stamp(self) -> tuple[str, float]:
        """服务用来判断要不要重读：(当前快照, 指针修改时间)。"""
        path = self.current()
        try:
            mtime = self.pointer.stat().st_mtime
        except OSError:
            mtime = 0.0
        return (str(path) if path else "", mtime)

    # 状态

    def state(self) -> dict[str, Any]:
        try:
            data = read_json(self.state_file)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_state(self, **changes: Any) -> dict[str, Any]:
        state = self.state()
        state.update(changes)
        try:
            _write_text(self.state_file, dumps(state))
        except OSError:
            pass
        return state

    # 整理

    def recover(self, older_than: float = 300) -> None:
        """清掉中断留下的半成品：没建完的快照、没下完的文件、临时文件、不再用的旧快照。"""
        now = time.time()
        current = self.current()
        if current and self.pointer_data().get("snapshot") != current.name:
            self._point(current, self.pointer_data() | {"snapshot": current.name, "repaired_at": local_now()})
        for folder, pattern in ((self.root, ".tmp-*"), (self.staging, "*.part"), (self.snapshots, "*.partial")):
            if not folder.is_dir():
                continue
            for path in folder.glob(pattern):
                try:
                    if now - path.stat().st_mtime >= older_than:
                        _remove(path)
                except OSError:
                    pass
        if self.snapshots.is_dir() and current:
            for path in self.snapshots.iterdir():
                if path.is_dir() and path != current and not path.name.endswith(".partial"):
                    _remove(path)

    def _point(self, snapshot: Path, info: dict[str, Any]) -> None:
        _write_text(self.pointer, dumps({**info, "snapshot": snapshot.name}))

    # 快照

    def link_or_copy(self, src: Path, dst: Path) -> None:
        if self._links:
            try:
                os.link(src, dst)
                return
            except OSError:
                self._links = False  # 文件系统不支持硬链接，以后都复制
        shutil.copy2(src, dst)


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink()
        except OSError:
            pass


# ──────────────────────────────────────────────────────────────── 下载与切换


class Progress:
    """更新进度，界面读。"""

    def __init__(self):
        self.lock = threading.Lock()
        self.data: dict[str, Any] = {}

    def set(self, **kw: Any) -> None:
        with self.lock:
            self.data.update(kw)

    def add(self, key: str, n: int = 1) -> None:
        with self.lock:
            self.data[key] = self.data.get(key, 0) + n

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.data)


def _stage_one(home: DataHome, source, rel: str, entry: dict[str, Any], progress: Progress | None) -> bool:
    """下载一个文件到 staging/<SHA-256>，核对字节数和 SHA-256。已经下好的直接用；返回是否真的下载了。"""
    target = home.staging / entry["sha256"]
    if target.is_file():
        try:
            if file_digest(target) == (entry["sha256"], entry["size"]):
                return False
        except OSError:
            pass
        _remove(target)
    try:
        fd, part = tempfile.mkstemp(prefix=entry["sha256"][:16] + "-", suffix=".part", dir=home.staging)
    except OSError as exc:
        raise SyncError("disk", f"写入下载目录失败：{exc}") from None
    ok = False
    try:
        with os.fdopen(fd, "wb") as out:
            writer = _HashingWriter(out)
            source.fetch(rel, writer, entry["size"])
        digest, size = writer.h.hexdigest(), writer.n
        if size != entry["size"] or digest != entry["sha256"]:
            raise SyncError("verify", f"{rel} 校验不符（字节数 {size}，清单 {entry['size']}）")
        if rel.endswith(".json"):
            try:
                json.loads(Path(part).read_bytes().decode("utf-8-sig"))
            except (UnicodeDecodeError, ValueError):
                raise SyncError("verify", f"{rel} 不是合法 JSON") from None
        replace(part, target)
        ok = True
        if progress:
            progress.add("done")
            progress.add("bytes", size)
        return True
    except OSError as exc:
        raise SyncError("disk", f"写入下载目录失败：{exc}") from None
    finally:
        if not ok:
            try:
                os.unlink(part)
            except OSError:
                pass


class _HashingWriter:
    """边写边算 SHA-256 和字节数。"""

    def __init__(self, out):
        self.out = out
        self.h = hashlib.sha256()
        self.n = 0

    def write(self, chunk: bytes) -> int:
        self.h.update(chunk)
        self.n += len(chunk)
        try:
            return self.out.write(chunk)
        except OSError as exc:
            raise SyncError("disk", f"写入下载目录失败：{exc}") from None


def apply(home: DataHome, manifest: dict[str, Any], source, *, origin: str, progress: Progress | None = None,
          workers: int = 4, hook: Callable[[str], None] | None = None) -> dict[str, Any]:
    """按清单更新到新快照；成功返回结果，失败抛 SyncError，当前快照不动。hook 供测试在各步模拟中断。"""
    hook = hook or (lambda step: None)
    base = home.current()
    cur = load_manifest(base / MANIFEST) if base else None
    puts, deletes = plan(manifest, cur, base)
    if progress:
        progress.set(total=len(puts), done=0, bytes=0, step="download")
    try:
        home.staging.mkdir(parents=True, exist_ok=True)
        home.snapshots.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SyncError("disk", f"建不了本机数据目录：{exc}") from None
    need = sum(manifest["files"][rel]["size"] for rel in puts)
    try:
        free = shutil.disk_usage(home.root).free
    except OSError:
        free = need * 3
    if free < need * 2 + (20 << 20):
        raise SyncError("disk", "磁盘空间不够")

    downloaded = 0
    if puts:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = [pool.submit(_stage_one, home, source, rel, manifest["files"][rel], progress) for rel in puts]
            try:
                for fut in concurrent.futures.as_completed(futures):
                    downloaded += bool(fut.result())
                    hook("download")
            except BaseException:
                for f in futures:
                    f.cancel()
                raise
    hook("downloaded")

    if progress:
        progress.set(step="snapshot")
    mid = manifest_id(manifest)
    name = manifest["generated_at"].replace("-", "").replace(":", "").replace("T", "-").rstrip("Z") + "-" + mid[:8]
    final = home.snapshots / name
    n = 2
    while final.exists():
        final, n = home.snapshots / f"{name}-{n}", n + 1
    tmp = home.snapshots / (final.name + ".partial")
    try:
        _remove(tmp)
        tmp.mkdir(parents=True)
        skip = set(puts) | set(deletes) | {MANIFEST}
        if base:
            for rel in iter_files(base):
                if rel not in skip:
                    dst = tmp / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    home.link_or_copy(base / rel, dst)
        hook("copy")
        for rel in puts:
            dst = tmp / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            home.link_or_copy(home.staging / manifest["files"][rel]["sha256"], dst)
        _write_text(tmp / MANIFEST, dumps_manifest(manifest))
        hook("built")
        from . import localupdates  # 用到时才导入：维护者重算清单用不着写回规则

        # 本机“立即更新”写回过的动态，按同一套写回规则重放到维护者的新数据上；出错不抛，原件留着下次再放
        merged = localupdates.after_sync(tmp, home.root.parent / "local")
        try:
            Store(tmp).load()  # 试读一遍：读不了的数据不切换
        except Exception as exc:  # noqa: BLE001
            raise SyncError("verify", f"新数据读不了：{type(exc).__name__}：{exc}") from None
        replace(tmp, final)
        hook("renamed")
        home._point(final, {"manifest": mid, "generated_at": manifest["generated_at"], "data_date": manifest.get("data_date", ""),
                            "origin": origin, "source": str(source), "switched_at": local_now()})
    except SyncError:
        _remove(tmp)
        raise
    except OSError as exc:
        _remove(tmp)
        raise SyncError("disk", f"建快照失败：{exc}") from None
    hook("switched")

    for path in home.snapshots.iterdir():
        if path.is_dir() and path != final:
            _remove(path)
    _remove(home.staging)
    if progress:
        progress.set(step="done")
    return {"status": "updated", "files": len(puts), "downloaded": downloaded, "deleted": len(deletes),
            "bytes": need, "data_date": manifest.get("data_date", ""), "generated_at": manifest["generated_at"],
            "snapshot": final.name, "local": merged}


def ensure_seed(home: DataHome, bundled: str | os.PathLike) -> dict[str, Any] | None:
    """本机还没有数据，或附带数据比本机的新（刚装了新版），就用附带数据铺底；返回结果，不需要时返回 None。"""
    source = LocalSource(bundled)
    body, _ = source.manifest()
    manifest = parse_manifest(body)
    if not is_newer(manifest, home.current_manifest()):
        return None
    result = apply(home, manifest, source, origin="bundled", workers=1)
    home.save_state(seeded_at=local_now(), seeded_from=manifest.get("data_date", ""))
    return result


def check(home: DataHome, sources: Iterable[str], *, progress: Progress | None = None, timeout: float = 20,
          workers: int = 4, user_agent: str = USER_AGENT, hook: Callable[[str], None] | None = None) -> dict[str, Any]:
    """联网取一次。不抛异常：结果写进 sync.json 并返回（status：updated、current、unreachable、failed）。"""
    started = local_now()
    state = home.state()
    etags = dict(state.get("etags") or {}) if home.current() else {}
    errors: list[tuple[str, SyncError]] = []
    for url in sources:
        try:
            source = HttpSource(url, timeout, user_agent)
        except ValueError as exc:
            errors.append((str(url), SyncError("format", str(exc))))
            continue
        try:
            if progress:
                progress.set(step="manifest", source=source.base, total=0, done=0, bytes=0)
            body, etag = source.manifest(etags.get(source.base))
            if body is None:
                result = {"status": "current", "message": "已是最新"}
            else:
                manifest = parse_manifest(body)
                if is_newer(manifest, home.current_manifest()):
                    result = apply(home, manifest, source, origin="online", progress=progress, workers=workers, hook=hook)
                    result["message"] = f"已更新：{result['files']} 个文件，删去 {result['deleted']} 个；数据日期 {result['data_date']}"
                else:
                    result = {"status": "current", "message": "已是最新"}
            if etag:
                etags[source.base] = etag
            result.update(source=source.base, checked_at=started)
            home.save_state(last_check=started, last_status=result["status"], last_message=result["message"],
                            last_source=source.base, last_success=local_now(), etags=etags, last_error=None,
                            **({"last_update": local_now(), "last_files": result["files"]} if result["status"] == "updated" else {}))
            return result
        except SyncError as exc:
            errors.append((source.base, exc))
    kinds = {e.kind for _, e in errors}
    status = "failed" if kinds & {"verify", "format", "disk"} else "unreachable"
    first = errors[0][1] if errors else SyncError("format", "没有数据地址")
    if status == "failed":
        message = f"联网数据没有用上（{first.message}），本机数据未改动，下次再试"
    else:
        message = f"未取到联网数据（{first.message}），继续用本机数据"
    result = {"status": status, "message": message, "checked_at": started,
              "errors": [{"source": s, "kind": e.kind, "message": e.message} for s, e in errors]}
    home.save_state(last_check=started, last_status=status, last_message=message, last_error=result["errors"])
    return result


# ──────────────────────────────────────────────────────────────── 后台定时


class Syncer:
    """软件运行时的联网更新：启动后取一次，之后每 24 小时一次；同一时间只跑一个。"""

    def __init__(self, home: DataHome, sources: Callable[[], list[str]], *, auto: Callable[[], bool] = lambda: True,
                 interval: float = INTERVAL, user_agent: str = USER_AGENT, timeout: float = 20):
        self.home = home
        self.sources = sources
        self.auto = auto
        self.interval = interval
        self.user_agent = user_agent
        self.timeout = timeout
        self.lock = threading.Lock()
        self.progress = Progress()
        self.running = False
        self.last: dict[str, Any] | None = None
        self._stop = threading.Event()

    def start(self, reason: str = "manual") -> bool:
        if not self.lock.acquire(blocking=False):
            return False
        self.running = True
        self.progress = Progress()
        self.progress.set(reason=reason, started=local_now())
        threading.Thread(target=self._run, name="datasync", daemon=True).start()
        return True

    def _run(self) -> None:
        try:
            self.last = check(self.home, self.sources(), progress=self.progress, timeout=self.timeout, user_agent=self.user_agent)
        except Exception as exc:  # noqa: BLE001  后台线程不让异常冒出去
            self.last = {"status": "failed", "message": f"联网更新出错：{type(exc).__name__}：{exc}"}
            self.home.save_state(last_check=local_now(), last_status="failed", last_message=self.last["message"])
        finally:
            self.running = False
            self.lock.release()

    def due(self, now: float | None = None) -> bool:
        last = self.home.state().get("last_check")
        try:
            then = dt.datetime.fromisoformat(last).timestamp() if last else 0.0
        except ValueError:
            then = 0.0
        return (now or time.time()) - then >= self.interval

    def loop(self, first_delay: float = 3, poll: float = 600) -> None:
        """后台线程：启动后 first_delay 秒取一次；之后每 poll 秒看一眼，距上次联网满 24 小时就再取（睡眠、休眠后也照算）。"""
        if self._stop.wait(first_delay):
            return
        if self.auto():
            self.start("startup")
        while not self._stop.wait(poll):
            if self.auto() and self.due():
                self.start("daily")

    def run_in_background(self, first_delay: float = 3, poll: float = 600) -> threading.Thread:
        t = threading.Thread(target=self.loop, args=(first_delay, poll), name="datasync-loop", daemon=True)
        t.start()
        return t

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict[str, Any]:
        return {"running": self.running, "progress": self.progress.snapshot() if self.running else {}, "state": self.home.state()}


# ──────────────────────────────────────────────────────────────── 命令行


def app_home() -> Path:
    """软件的本机目录：环境变量 ZUZHIBU_HOME，默认 %LOCALAPPDATA%\\zuzhibu（联网更新的数据、日志、窗口配置都在这里）。"""
    env = os.environ.get(HOME_ENV)
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "zuzhibu"


def default_home() -> Path:
    return app_home() / "data"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m zuzhibu.datasync", description="AI政见·组织部 数据分发")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("manifest", help="维护者：重算 data/manifest.json")
    m.add_argument("--data", default=str(DEFAULT_DATA))
    c = sub.add_parser("check", help="联网取一次最新数据")
    c.add_argument("--home", default=str(default_home()))
    c.add_argument("--url", action="append", help="数据地址，可给多个，按顺序试；默认仓库 main 分支的 data/")
    c.add_argument("--bundled", help="先用这份附带数据铺底（安装包里的 app/data）")
    s = sub.add_parser("status", help="本机数据目录的现状")
    s.add_argument("--home", default=str(default_home()))
    args = ap.parse_args(argv)

    if args.cmd == "manifest":
        manifest, changed = write_manifest(args.data)
        print(f"{'已更新' if changed else '没有变化'}：{Path(args.data) / MANIFEST}（{len(manifest['files'])} 个文件，数据日期 {manifest['data_date']}）")
        return 0
    home = DataHome(args.home)
    if args.cmd == "status":
        print(json.dumps({"current": str(home.current() or ""), "pointer": home.pointer_data(), "state": home.state()},
                         ensure_ascii=False, indent=2))
        return 0
    home.recover()
    if args.bundled:
        seeded = ensure_seed(home, args.bundled)
        if seeded:
            print(f"已用附带数据铺底：{seeded['files']} 个文件，数据日期 {seeded['data_date']}")
    result = check(home, args.url or list(DEFAULT_SOURCES))
    print(result["message"])
    return 0 if result["status"] in ("updated", "current") else 1


if __name__ == "__main__":
    raise SystemExit(main())
