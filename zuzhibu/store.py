"""人物库与动态库（docs/PRODUCT.md 第六节）。

    data/people/<人物编号>.json          一人一份人物记录
    data/events/<年-月>/<动态编号>.json   一条一份人事动态
    data/meta.json                       数据版本、各局实际资料截止日期、最近一次一局更新（字段名 daily）

JSON 一律 UTF-8、LF、两格缩进，先写临时文件再替换，写到一半断电也不会留下半个文件。
只用标准库。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parent.parent
DEFAULT_DATA = REPO / "data"
SCHEMA_PATH = REPO / "shared" / "schema" / "bundle.schema.json"
DATA_ENV = "ZUZHIBU_DATA"

_ID_RX = re.compile(r"^[a-z0-9][a-z0-9_-]{0,95}$")
_NAME_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|phd|md|esq)\b\.?", re.I)


def data_dir(explicit: str | os.PathLike | None = None) -> Path:
    """数据目录：参数优先，其次环境变量 ZUZHIBU_DATA，再次仓库的 data/。"""
    if explicit:
        return Path(explicit)
    env = os.environ.get(DATA_ENV)
    return Path(env) if env else DEFAULT_DATA


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(dumps(obj))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def slugify(text: str, fallback: str = "x") -> str:
    """英文名转编号：去掉重音符号，小写，非字母数字换连字符。"""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    text = re.sub(r"-{2,}", "-", text)[:80].strip("-")
    return text or fallback


def name_key(name_en: str) -> str:
    """英文名比对键：去掉后缀、中间名缩写和标点，只留名和姓。"""
    text = unicodedata.normalize("NFKD", name_en or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = _NAME_SUFFIX.sub(" ", text)
    text = re.sub(r"\(.*?\)|\".*?\"", " ", text)
    tokens = [t for t in re.split(r"[^a-z]+", text) if t]
    tokens = [t for t in tokens if len(t) > 1] or tokens
    if len(tokens) >= 2:
        return f"{tokens[0]} {tokens[-1]}"
    return " ".join(tokens)


class Store:
    """读写一个数据目录。load() 把全部人物和动态读进内存；写操作同时更新内存和文件。"""

    def __init__(self, root: str | os.PathLike | None = None):
        self.root = data_dir(root)
        self.people_dir = self.root / "people"
        self.events_dir = self.root / "events"
        self.meta_path = self.root / "meta.json"
        self._people: dict[str, dict[str, Any]] = {}
        self._events: dict[str, dict[str, Any]] = {}
        self._event_paths: dict[str, Path] = {}
        self.loaded = False

    # ─────────────────────────────── 读

    def load(self) -> "Store":
        self._people.clear()
        self._events.clear()
        self._event_paths.clear()
        if self.people_dir.is_dir():
            for path in sorted(self.people_dir.glob("*.json")):
                rec = read_json(path)
                self._people[rec["id"]] = rec
        if self.events_dir.is_dir():
            for path in sorted(self.events_dir.glob("*/*.json")):
                ev = read_json(path)
                self._events[ev["id"]] = ev
                self._event_paths[ev["id"]] = path
        self.loaded = True
        return self

    def person(self, pid: str) -> dict[str, Any] | None:
        return self._people.get(pid)

    def people(self) -> list[dict[str, Any]]:
        return list(self._people.values())

    def event(self, eid: str) -> dict[str, Any] | None:
        return self._events.get(eid)

    def events(self) -> list[dict[str, Any]]:
        return sorted(self._events.values(), key=lambda e: (e.get("date") or "", e["id"]), reverse=True)

    def meta(self) -> dict[str, Any]:
        return read_json(self.meta_path) if self.meta_path.exists() else {}

    # ─────────────────────────────── 查

    def find_by_external(self, kind: str, value: str) -> dict[str, Any] | None:
        value = (value or "").lower()
        for rec in self._people.values():
            if str((rec.get("ids") or {}).get(kind, "")).lower() == value and value:
                return rec
        return None

    def find_by_name(self, name_en: str | None = None, name_zh: str | None = None) -> list[dict[str, Any]]:
        """按英文名比对键或中文名找人，可能多于一人（同名）。"""
        hits: list[dict[str, Any]] = []
        key = name_key(name_en) if name_en else ""
        for rec in self._people.values():
            if key and name_key(rec.get("name_en") or "") == key:
                hits.append(rec)
            elif name_zh and rec.get("name_zh") == name_zh:
                hits.append(rec)
        return hits

    def new_person_id(self, name_en: str, name_zh: str = "") -> str:
        base = slugify(name_en, fallback="person")
        pid, n = base, 2
        while pid in self._people or (self.people_dir / f"{pid}.json").exists():
            pid, n = f"{base}-{n}", n + 1
        return pid

    def new_event_id(self, date: str, person_id: str, kind: str) -> str:
        base = f"{date}-{person_id}-{kind}"[:90].strip("-")
        eid, n = base, 2
        while eid in self._events:
            eid, n = f"{base}-{n}", n + 1
        return eid

    # ─────────────────────────────── 写

    def save_person(self, rec: dict[str, Any]) -> Path:
        pid = rec.get("id", "")
        if not _ID_RX.match(pid):
            raise ValueError(f"人物编号不合规：{pid!r}")
        path = self.people_dir / f"{pid}.json"
        write_json(path, rec)
        self._people[pid] = rec
        return path

    def save_event(self, ev: dict[str, Any]) -> Path:
        eid = ev.get("id", "")
        if not _ID_RX.match(eid):
            raise ValueError(f"动态编号不合规：{eid!r}")
        month = (ev.get("date") or "0000-00")[:7]
        path = self.events_dir / month / f"{eid}.json"
        old = self._event_paths.get(eid)
        write_json(path, ev)
        if old and old != path and old.exists():
            old.unlink()
        self._events[eid] = ev
        self._event_paths[eid] = path
        return path

    def save_meta(self, meta: dict[str, Any]) -> None:
        write_json(self.meta_path, meta)


def events_of(store: Store, pid: str) -> list[dict[str, Any]]:
    return [e for e in store.events() if e.get("person_id") == pid]


def iter_text(rec: dict[str, Any]) -> Iterable[str]:
    """检索用文字：姓名、现任职务。"""
    yield rec.get("name_zh") or ""
    yield rec.get("name_en") or ""
    for role in (rec.get("resume") or {}).get("current_roles") or []:
        yield role.get("organization") or ""
        yield role.get("role") or ""
    for role in rec.get("roster_roles") or []:
        yield role.get("organization_en") or ""
        yield role.get("role_en") or ""
