"""本地接口：给 web/ 界面提供五栏名单、人物简历、人事动态、设置和关于（docs/PRODUCT.md 第八节）；七局版的名册、检索、导出接口保留。

    python -m zuzhibu.server [--port 8765] [--data 数据目录]

只监听 127.0.0.1。静态文件从 web/ 提供，/api/… 从人物库读。数据目录有更新（一局写回后 meta.json 会变）时自动重读。
界面字体不进仓库，/fonts/… 从字体目录供给（fonts_dir：环境变量 ZUZHIBU_FONTS、本机设置 local.json 的 fonts，否则程序目录下的 fonts/）。
安装版的数据在本机数据目录（zuzhibu/datasync.py），联网更新切换快照后也自动重读。
用户自带 AI API 的设置和数据地址存在本机用户目录（Windows 是 %APPDATA%\\zuzhibu\\settings.json），不进仓库。
只认 Host 是 127.0.0.1、localhost 的请求；POST 只收同源的 JSON，别的网站不能借浏览器改设置、触发更新。
“立即更新”用使用者自己的 AI 接口（shared/backends/ai_api.py）跑一局流水线，结果只写本机数据目录，写进的动态另记在
本机更新目录（zuzhibu/localupdates.py），维护者数据换新后据此重放；没填接口而本机有已登录的 Codex 时用 Codex。
进度和停止：流水线写进度文件（zuzhibu/progress.py），本服务读出来给设置里的进度条；“停止更新”建停止文件，流水线停下后
照样写回已核实的结果。

只用标准库。
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from shared.backends import ai_api

from . import bureaus, datasync, headline, local, localupdates, personnel, portraits, progress, resume, rosters
from .store import REPO, Store, events_of, iter_text

WEB = REPO / "web"
APP_NAME = "AI政见·组织部"
VERSION = "1.0.0"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
EXPORT_FORMATS = {"docx": ".docx", "article": ".html", "card": ".card.html", "md": ".md", "txt": ".txt"}
FONT_TYPES = {".ttf": "font/ttf", ".otf": "font/otf", ".woff": "font/woff", ".woff2": "font/woff2"}
HOME_SECTION = "whitehouse"


def fonts_dir() -> Path:
    """界面字体的目录：环境变量 ZUZHIBU_FONTS、本机设置 local.json 的 fonts，都没有就用程序目录下的 fonts/
    （安装版是 resources\\pyapp\\fonts，由安装包放进去）。字体文件不进仓库。"""
    return local.get_path("fonts") or REPO / "fonts"


def settings_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "zuzhibu" / "settings.json"


def load_settings() -> dict[str, Any]:
    path = settings_path()
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data: dict[str, Any]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def mask(key: str) -> str:
    return (key[:3] + "…" + key[-4:]) if key and len(key) > 8 else ("已填" if key else "")


API_FIELDS = ("provider", "base_url", "model", "effort")


def api_fingerprint(api: dict[str, Any]) -> str:
    """接口设置的指纹：检测结果只对检测时的接口、地址、模型、密钥有效（密钥只取摘要，不存原文）。"""
    raw = "|".join(str(api.get(k) or "") for k in (*API_FIELDS, "api_key"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def api_view(settings: dict[str, Any]) -> dict[str, Any]:
    """“我的”页的 AI 接口一节：密钥打码；能不能开一局更新；上次检测联网检索的结果（设置改过就作废）。"""
    api = dict(settings.get("api") or {})
    ok, why = ai_api.capability(api)
    check = api.pop("check", None)
    if check and check.get("fingerprint") != api_fingerprint(api):
        check = None
    api["api_key"] = mask(api.get("api_key", ""))
    providers = [{"id": k, **{f: v[f] for f in ("label", "base_url", "model", "web")}}
                 for k, v in ai_api.PROVIDERS.items() if v["web"]]  # 设置里只列能联网检索的接口类型
    return {"api": api, "web": {"ok": ok, "reason": "" if ok else why, "label": why if ok else ""},
            "check": {k: v for k, v in (check or {}).items() if k != "fingerprint"} or None, "providers": providers}


def update_plan(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """立即更新用什么：填了 AI 接口（选了接口类型并填了密钥）→ 使用者自己的接口；没填而本机有已登录的 Codex → Codex；
    都没有，或填的接口没有联网检索 → 不能更新，说明原因。"""
    settings = load_settings() if settings is None else settings
    api = settings.get("api") or {}
    no_web = not ai_api.PROVIDERS.get(api.get("provider") or "openai", {"web": True})["web"]
    if str(api.get("api_key") or "").strip() or no_web:
        ok, why = ai_api.capability(api)
        return {"backend": "api", "ok": ok, "reason": "" if ok else why, "label": why if ok else ""}
    if codex_available():
        return {"backend": "codex", "ok": True, "reason": "", "label": "本机已登录的 Codex"}
    return {"backend": None, "ok": False, "label": "", "reason": "请先在‘设置’中填写大模型接口"}


def _endpoint(api: dict[str, Any]) -> tuple[str, str]:
    """密钥发往哪里：(接口类型, 实际地址)。"""
    provider = api.get("provider") or "openai"
    try:
        base = ai_api.normalize_base(api.get("base_url") or (ai_api.PROVIDERS.get(provider) or {}).get("base_url") or "")
    except ValueError:
        base = str(api.get("base_url") or "")
    return provider, base


def save_api_settings(body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """保存“我的”页的 AI 接口设置。换了接口类型或地址而没有重填密钥的，清掉旧密钥：密钥只发往填它时的那个地址。"""
    if not isinstance(body, dict):
        return 400, {"ok": False, "error": "设置格式不对"}
    current = load_settings()
    api = dict(current.get("api") or {})
    before = _endpoint(api)
    for key in API_FIELDS:
        if key in body:
            api[key] = str(body[key] or "").strip()
    if api.get("provider") and api["provider"] not in ai_api.PROVIDERS:
        return 400, {"ok": False, "error": f"不认识的接口类型：{api['provider']}"}
    if api.get("base_url"):
        try:
            api["base_url"] = ai_api.normalize_base(api["base_url"])
        except ValueError as exc:
            return 400, {"ok": False, "error": str(exc)}
    key, cleared = body.get("api_key"), False
    if key is not None and key != mask(api.get("api_key", "")):
        api["api_key"] = str(key).strip()
    elif api.get("api_key") and _endpoint(api) != before:
        api["api_key"], cleared = "", True
    current["api"] = api
    save_settings(current)
    return 200, {"ok": True, "cleared_key": cleared}


def check_api(settings: dict[str, Any]) -> dict[str, Any]:
    """检测填的接口能不能联网检索（检索约 1 次，用使用者的额度），结果存进设置（不含密钥）。
    返回 {ok, code, message, at}：message 照审定的文案（ai_api.CHECK_MESSAGES），文案没写到的照同一句式。"""
    api = settings.get("api") or {}
    try:
        config = ai_api.config_from_settings(api, retries=1)
    except (ai_api.ApiError, ValueError) as exc:
        code, why = ai_api.reason_of(exc)
        return {"ok": False, "code": code, "message": ai_api.CHECK_MESSAGES.get(code) or f"接口不可用：{why}，请核对后重试。"}
    result = ai_api.probe(config)
    out = {"ok": result["ok"], "code": result["code"], "message": result["message"],
           "at": dt.datetime.now().isoformat(timespec="seconds")}
    settings.setdefault("api", {})["check"] = {**out, "fingerprint": api_fingerprint(api)}
    save_settings(settings)
    return out


# ──────────────────────────────────────────────────────────────── 数据视图


class App:
    """data_dir：固定读一份数据（从仓库运行时是 data/）。home：安装版的本机数据目录，读当前快照、联网更新。
    lifetime：桌面版判断关窗退出；dist：安装版的构建信息；runs_dir：立即更新的运行记录目录。"""

    def __init__(self, data_dir: str | None = None, *, home: datasync.DataHome | None = None, lifetime: Any = None,
                 dist: dict[str, Any] | None = None, runs_dir: str | os.PathLike | None = None):
        self.home = home
        self.lifetime = lifetime
        self.dist = dist
        self.runs_dir = Path(runs_dir) if runs_dir else None
        self.instance = secrets.token_hex(8)
        current = home.current() if home else None
        if home and not current:
            raise RuntimeError(f"本机数据目录 {home.root} 还没有数据")
        self.store = Store(current or data_dir).load()
        self.lock = threading.Lock()
        self._stamp = self._mtime()
        self._pointer = home.stamp()[1] if home else 0.0
        self.update_state: dict[str, Any] = {"running": False, "started": None, "finished": None, "code": None, "log": ""}  # 本服务开的那次
        self.syncer = datasync.Syncer(home, self.data_sources, auto=self.data_auto, user_agent=f"zuzhibu/{VERSION}") if home else None
        self._views: dict[str, tuple[Any, Any]] = {}

    def _mtime(self) -> float:
        try:
            return self.store.meta_path.stat().st_mtime
        except OSError:
            return 0.0

    def fresh(self) -> Store:
        with self.lock:
            if self.home:
                current, pointer = self.home.stamp()
                if pointer != self._pointer:  # 联网更新切换了快照
                    self._pointer = pointer
                    if current and Path(current) != self.store.root:
                        self.store = Store(current).load()
                        self._stamp = self._mtime()
                        return self.store
            stamp = self._mtime()
            if stamp != self._stamp:
                self.store.load()
                self._stamp = stamp
            return self.store

    def _cached(self, name: str, key: Any, build: Any) -> Any:
        """按数据和名单的指纹缓存算好的视图：数据、名单没变就不重算。"""
        hit = self._views.get(name)
        if hit and hit[0] == key:
            return hit[1]
        value = build()
        self._views[name] = (key, value)
        return value

    # 五栏名单、人物页、人事动态、关于（第二版界面）

    def rosters(self) -> tuple[Store, dict[str, dict[str, Any]]]:
        store = self.fresh()
        return store, self._cached("rosters", (store.root, rosters.stamp(store.root)), lambda: rosters.load(store.root))

    def _key(self, store: Store) -> tuple:
        return (store.root, self._stamp, rosters.stamp(store.root))

    def app_view(self) -> dict[str, Any]:
        _store, ros = self.rosters()
        sections = [{"id": sid, "title": ros[sid]["title"]} for sid, _t in rosters.SECTIONS] + [{"id": "news", "title": "人事动态"}]
        return {"name": APP_NAME, "version": VERSION, "home": HOME_SECTION, "sections": sections}

    def roster_view(self, sid: str) -> dict[str, Any] | None:
        store, ros = self.rosters()
        if sid not in ros:
            return None
        return self._cached("roster:" + sid, self._key(store), lambda: rosters.view(store, ros[sid]))

    def index_view(self) -> list[dict[str, Any]]:
        store, ros = self.rosters()
        return self._cached("index", self._key(store), lambda: rosters.index(store, ros))

    def profile_view(self, pid: str) -> dict[str, Any] | None:
        store, ros = self.rosters()
        return rosters.person(store, ros, pid)

    def articles(self) -> list[dict[str, Any]]:
        """人事动态稿件。公开什么、怎么去重合篇、标题和正文怎么写，都由 zuzhibu/renmian.py 生成，本服务只转给界面；
        还没有这个模块时为空。"""
        store = self.fresh()
        return self._cached("articles", self._key(store), lambda: news_articles(store))

    def article(self, aid: str) -> dict[str, Any] | None:
        store = self.fresh()
        return self._cached("article:" + aid, self._key(store), lambda: news_article(store, aid))

    def about_view(self) -> dict[str, Any]:
        store, ros = self.rosters()
        return {"name": APP_NAME, "version": VERSION, "credits": self._cached("credits", self._key(store), lambda: rosters.credits(store, ros))}

    # 数据分发

    def default_sources(self) -> list[str]:
        return list((self.dist or {}).get("sources") or datasync.DEFAULT_SOURCES)

    def data_sources(self) -> list[str]:
        url = ((load_settings().get("data") or {}).get("url") or "").strip()
        return [url] if url else self.default_sources()

    def data_auto(self) -> bool:
        return (load_settings().get("data") or {}).get("auto", True) is not False

    def data_view(self) -> dict[str, Any]:
        store = self.fresh()
        meta = store.meta()
        custom = ((load_settings().get("data") or {}).get("url") or "").strip()
        out: dict[str, Any] = {
            "mode": "installed" if self.dist else "dev", "enabled": bool(self.home), "data_dir": str(store.root),
            "data_date": meta.get("updated_at") or "", "baseline": meta.get("baseline") or "", "daily": meta.get("daily") or {},
            "url": custom or self.default_sources()[0], "default_url": self.default_sources()[0], "custom": bool(custom),
            "auto": self.data_auto(), "build": {k: (self.dist or {}).get(k) for k in ("version", "built_at", "data_date", "python")},
        }
        if self.syncer:
            out.update(current=self.home.pointer_data(), **self.syncer.status())
        return out

    # 名册行
    def summary(self, rec: dict[str, Any]) -> dict[str, Any]:
        item = rec.get("resume") or {}
        role = bureaus.primary_role(item.get("current_roles") or [])
        role_label = resume.stated(role.get("organization", ""), resume.role_text(role), role.get("appointment_state")) if role else ""
        if role and not role_label.strip():
            role_label = bureaus.role_text(role)
        return {
            "id": rec["id"], "name_zh": rec.get("name_zh") or "", "name_en": rec.get("name_en") or "",
            "bureau": rec.get("bureau"), "also_in": rec.get("also_in") or [], "tier": rec.get("tier"),
            "role": role_label, "roles": len(item.get("current_roles") or []),
            "status": (item.get("person") or {}).get("status"),
            "updated_at": rec.get("updated_at"), "data_cutoff": rec.get("data_cutoff"),
            "roles_checked_at": rec.get("roles_checked_at"), "events": len(rec.get("events") or []),
            "portrait": portraits.display_src(rec),
            "portrait_credit": portraits.credit(rec, self.store.root),
            "name_pending": bool(rec.get("name_zh_pending")),
        }

    def bureaus_view(self) -> dict[str, Any]:
        store = self.fresh()
        meta = store.meta()
        counts: dict[str, int] = {}
        for rec in store.people():
            for b in [rec.get("bureau"), *(rec.get("also_in") or [])]:
                counts[b or "未定"] = counts.get(b or "未定", 0) + 1
        out = []
        for b in bureaus.BUREAUS:
            d = bureaus.as_dict(b)
            info = (meta.get("bureaus") or {}).get(b.id, {})
            d.update(count=len(store.events()) if b.id == bureaus.DISPATCH else counts.get(b.id, 0),
                     cutoff_min=info.get("cutoff_min"), cutoff_max=info.get("cutoff_max"), tiers=info.get("tiers") or {})
            out.append(d)
        return {"app": {"name": APP_NAME, "version": VERSION}, "bureaus": out, "meta": meta,
                "unassigned": counts.get("未定", 0), "kinds": personnel.KIND_LABEL, "status_labels": personnel.STATUS_LABEL}

    def people_view(self, bureau: str | None, q: str, tier: str | None) -> list[dict[str, Any]]:
        store = self.fresh()
        rows = []
        for rec in store.people():
            if bureau and rec.get("bureau") != bureau and bureau not in (rec.get("also_in") or []):
                continue
            if tier and rec.get("tier") != tier:
                continue
            if q and not _match(rec, q):
                continue
            rows.append(self.summary(rec))
        tier_rank = {"gold": 0, "reverified": 1, "seed": 2, "draft": 3, "held": 4}
        rows.sort(key=lambda r: (-r["events"], tier_rank.get(r["tier"], 9), r["name_zh"] or r["name_en"]))
        return rows

    def person_view(self, pid: str) -> dict[str, Any] | None:
        store = self.fresh()
        rec = store.person(pid)
        if not rec:
            return None
        composed = resume.compose(rec)
        lint = []
        try:
            from tools.lint import cadre_lint
            lint = [v.to_dict() for v in cadre_lint.lint_text(composed["text"], glossary=_glossary(), country=composed["country"])]
        except Exception as exc:  # lint 出错不影响查看
            lint = [{"code": "LINT", "level": "note", "message": f"写法检查没有运行：{exc}", "line": 0, "col": 0, "text": ""}]
        events = [event_summary(e, store) for e in events_of(store, pid)]
        return {"record": rec, "summary": self.summary(rec), "composed": composed, "lint": lint, "events": events,
                "bureau_label": bureaus.label(rec.get("bureau"))}

    def events_view(self, since: str | None, until: str | None, bureau: str | None, kind: str | None, q: str) -> list[dict[str, Any]]:
        store = self.fresh()
        out = []
        for e in store.events():
            d = e.get("date") or ""
            if since and d < since or until and d > until:
                continue
            if bureau and bureau not in (e.get("bureaus") or []):
                continue
            if kind and e.get("kind") != kind:
                continue
            summary = event_summary(e, store)
            if q and q.lower() not in json.dumps([summary["headline"], e.get("person")], ensure_ascii=False).lower():
                continue
            out.append(summary)
        return out

    def search(self, q: str) -> dict[str, Any]:
        people = self.people_view(None, q, None)[:30]
        events = self.events_view(None, None, None, None, q)[:20]
        return {"q": q, "people": people, "events": events}


_GLOSSARY = None


def _glossary():
    global _GLOSSARY
    if _GLOSSARY is None:
        from tools.lint import cadre_lint
        _GLOSSARY = cadre_lint.load_glossary()
    return _GLOSSARY


def _match(rec: dict[str, Any], q: str) -> bool:
    q = q.strip().lower()
    return any(q in (t or "").lower() for t in iter_text(rec)) or q == rec["id"]


ARTICLE_FIELDS = ("id", "title", "date", "date_cn", "paragraphs", "source_line")


def _renmian():
    """任免结果模块（公开什么、怎么去重合篇、标题和正文都在这里生成）；按模块名取，取不到返回 None。"""
    try:
        return importlib.import_module(__package__ + ".renmian")
    except ImportError:
        return None


def _article_view(a: Any) -> dict[str, Any] | None:
    """界面要的一篇：标题、事件日期（到日或到月，date_cn 是中文写法）、正文段落、来源一栏（发布机构中文名）。"""
    if not (isinstance(a, dict) and a.get("id") and a.get("title")):
        return None
    view = {k: a.get(k) for k in ARTICLE_FIELDS}
    view["id"] = str(view["id"])
    view["paragraphs"] = [str(x) for x in view.get("paragraphs") or [] if str(x).strip()]
    for k in ("date", "date_cn", "source_line"):
        view[k] = str(view.get(k) or "")
    return view


def news_articles(store: Store) -> list[dict[str, Any]]:
    """人事动态稿件，新的在前：zuzhibu/renmian.py 的 articles(store)。还没有这个模块时返回空。"""
    module = _renmian()
    return [v for v in (_article_view(a) for a in (module.articles(store) if module else []) or []) if v]


def news_article(store: Store, aid: str) -> dict[str, Any] | None:
    """单篇：zuzhibu/renmian.py 的 article(store, id)；没有这篇返回 None。"""
    module = _renmian()
    return _article_view(module.article(store, aid)) if module else None


def license_texts() -> dict[str, str]:
    """关于里的“许可全文”：源代码的 Apache License 2.0（LICENSE），人物资料库的 CC BY-SA 4.0 说明（data/LICENSE-CONTENT.md）。"""
    def read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return ""
    return {"code": read(REPO / "LICENSE"), "content": read(REPO / "data" / "LICENSE-CONTENT.md")}


def event_summary(e: dict[str, Any], store: Store) -> dict[str, Any]:
    """界面用的一条动态：标题由程序按字段生成（zuzhibu/headline.py）；数据文件里研究会话写的标题、摘要只作内部记录，不给界面。"""
    applied = e.get("applied") or {}
    return {
        "id": e["id"], "date": e.get("date"), "kind": e.get("kind"), "kind_label": e.get("kind_label") or personnel.KIND_LABEL.get(e.get("kind"), ""),
        "headline": headline.event_headline(e, store), "person_id": e.get("person_id"), "person": e.get("person"),
        "bureaus": e.get("bureaus") or [], "announced_at": e.get("announced_at"), "effective_at": e.get("effective_at"),
        "ended_roles": e.get("ended_roles") or [], "new_roles": e.get("new_roles") or [],
        "sources": [{"url": s.get("url"), "title": s.get("title"), "publisher": s.get("publisher"), "published_at": s.get("published_at"),
                     "excerpt": s.get("excerpt"), "excerpt_check": s.get("excerpt_check")} for s in e.get("sources") or []],
        "verification": e.get("verification") or {}, "origin": e.get("origin"),
        "applied": {"status": applied.get("status"), "status_label": personnel.STATUS_LABEL.get(applied.get("status"), ""),
                    "changes": applied.get("changes") or [], "notes": applied.get("notes") or [],
                    "bureau_before": applied.get("bureau_before"), "bureau_after": applied.get("bureau_after"),
                    "reason": applied.get("reason", "")},
    }


# ──────────────────────────────────────────────────────────────── 导出


def export_bytes(rec: dict[str, Any], fmt: str, root: str | Path | None = None) -> tuple[bytes, str, str]:
    """返回 (内容, 文件名, MIME)。过了完整简历卡门槛的才能输出，不看分级（docs/PRODUCT.md 第六节）。"""
    composed = resume.compose(rec)
    if not composed["exportable"]:
        raise PermissionError(composed["reason"])
    doc = dict(composed["doc"])
    photo = portraits.data_uri(rec, root)
    if photo:
        doc["photo"] = photo
        credit = portraits.credit(rec, root)
        if credit:
            doc["photo_credit"] = credit  # CC BY 照片导出时带署名（docs/公开前检查.md 第十节）
    stem = doc["title"]
    if fmt == "md":
        return resume.markdown(rec).encode("utf-8"), stem + ".md", "text/markdown; charset=utf-8"
    if fmt == "txt":
        caption = doc["title"] + "\n\n" + doc["lead"] + "\n\n" + " ".join("#" + t for t in ["AI政见", doc["name"], *doc.get("tags", []), "干部简历"]) + "\n"
        return caption.encode("utf-8"), stem + "_配文.txt", "text/plain; charset=utf-8"
    if fmt == "article":
        from shared.render.article_html import render_article_page
        return render_article_page(doc).encode("utf-8"), stem + "_公众号.html", "text/html; charset=utf-8"
    if fmt == "card":
        from shared.render.card_html import render_card_html
        return render_card_html(doc).encode("utf-8"), stem + "_图文卡.html", "text/html; charset=utf-8"
    if fmt == "docx":
        from shared.render.docx_redhead import LOCAL_FONTS, render_docx
        with tempfile.TemporaryDirectory() as tmp:
            path = render_docx(doc, Path(tmp) / "out.docx", fonts=dict(LOCAL_FONTS), plain=True)
            data = path.read_bytes()
        return data, stem + ".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    raise ValueError(f"不认识的导出格式：{fmt}")


# ──────────────────────────────────────────────────────────────── 立即更新


def codex_available() -> bool:
    if shutil.which("codex.exe") or shutil.which("codex"):
        return True
    local = os.environ.get("LOCALAPPDATA")
    return bool(local and list((Path(local) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe")))


def update_files(home: Path | None = None) -> tuple[Path, Path]:
    """立即更新的进度文件和停止文件：软件本机目录下的 update\\。"""
    folder = (home or localupdates.app_home()) / "update"
    return folder / "progress.json", folder / "stop"


def update_command(plan: dict[str, Any], data_dir: Path, home: Path | None = None, runs: Path | None = None) -> list[str]:
    """立即更新的命令行：窗口从上次截止日到今天（一局流水线 --since-last 自己算，与 bureaus/dispatch/daily.ps1 同算法）；
    结果只写本机数据目录，写进的动态另记在本机更新目录；运行记录放软件本机目录。密钥不在命令行里，由一局流水线自己从设置文件读。
    home：软件本机目录（安装版是数据目录的上一级），默认 %LOCALAPPDATA%\\zuzhibu；runs：运行记录目录，默认 home/runs。"""
    home = home or localupdates.app_home()
    progress_file, stop_file = update_files(home)
    cmd = [sys.executable, "-m", "bureaus.dispatch.run", "--since-last", "--data", str(data_dir),
           "--runs", str(runs or home / "runs"), "--journal", str(home / "local"),
           "--progress", str(progress_file), "--stop-file", str(stop_file)]
    if plan["backend"] == "api":
        return cmd + ["--backend", "api", "--settings", str(settings_path()), "--jobs", "1"]  # 一组一组跑，少碰接口限流
    return cmd + ["--origin", "user-codex"]


def start_update(app: App) -> dict[str, Any]:
    """“立即更新”按钮的入口（POST /api/update）。只用使用者自己的额度：使用者在设置里填的大模型接口（shared/backends/ai_api.py），
    没填时用这台电脑上使用者自己登录的 Codex；软件不带、也不用维护者的任何账号、密钥或 Codex 登录。
    返回 {ok: true}，不能开始的 {ok: false, message}。进度由 GET /api/update 读。"""
    state = app.update_state
    if state["running"]:
        return {"ok": False, "message": "更新正在进行"}
    plan = update_plan()
    if not plan["ok"]:
        return {"ok": False, "message": plan["reason"]}
    # 联网取数和立即更新不同时改同一份数据
    if app.syncer and not app.syncer.lock.acquire(blocking=False):
        return {"ok": False, "message": "正在联网更新数据，稍后再试"}
    store = app.fresh()
    home = app.home.root.parent if app.home else None
    cmd = update_command(plan, store.root, home, app.runs_dir)
    progress_file, stop_file = update_files(home)
    stop_file.unlink(missing_ok=True)
    progress.Progress(progress_file).step(1)  # 先写一份“正在连接接口”，界面马上有进度；流水线起来后接着写
    state.update(running=True, started=dt.datetime.now().isoformat(timespec="seconds"), finished=None, code=None, log="",
                 backend=plan["backend"], progress=str(progress_file), stop=str(stop_file))

    def worker():
        try:
            proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            state.update(code=proc.returncode, log=(proc.stdout or "")[-4000:] + (proc.stderr or "")[-2000:])
        except OSError as exc:
            state.update(code=-1, log=f"没能启动更新：{exc}")
        finally:
            state.update(running=False, finished=dt.datetime.now().isoformat(timespec="seconds"))
            stop_file.unlink(missing_ok=True)
            if app.syncer:
                app.syncer.lock.release()

    threading.Thread(target=worker, daemon=True).start()
    return {"ok": True}


def update_view(app: App) -> dict[str, Any]:
    """GET /api/update：{state, running, step, percent, text, message, detail, result, error, started, finished, plan}。
    state：idle（没跑过）、running、stopping（点了停止，流水线正在收尾）、stopped、done、failed；text、message 照审定的文案。"""
    progress_file, stop_file = update_files(app.home.root.parent if app.home else None)
    status = progress.read(progress_file)
    running = bool(app.update_state["running"])
    if not status:
        status = {"state": "running" if running else "idle", "step": 0, "detail": {}, "result": {"events": 0, "resumes": 0},
                  "error": None, "started": app.update_state.get("started"), "finished": None}
    elif status.get("state") in ("running", "stopping") and not running:
        status = {**status, "state": "failed", "error": "程序中断"}  # 流水线已经不在了却没报结束：被关掉或出了意外
    elif running and status.get("state") in ("done", "stopped", "failed"):
        status = {**status, "state": "running"}  # 流水线报了结束、还在收尾退出
    if status.get("state") == "running" and stop_file.exists():
        status = {**status, "state": "stopping"}
    out = progress.view(status)
    out["running"] = out["state"] in ("running", "stopping")
    out["plan"] = update_plan()
    return out


def stop_update(app: App) -> dict[str, Any]:
    """“停止更新”（POST /api/update/stop）：建停止文件；流水线不再开新会话，接口上正在跑的会话取消，
    已核实的照样写回，再报 stopped。"""
    if not app.update_state["running"]:
        return {"ok": False, "message": "没有正在进行的更新"}
    _, stop_file = update_files(app.home.root.parent if app.home else None)
    stop_file.parent.mkdir(parents=True, exist_ok=True)
    stop_file.write_text(dt.datetime.now().isoformat(timespec="seconds"), encoding="utf-8")
    return {"ok": True, "state": "stopping"}


def data_post(app: App, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """我的页的数据设置：{"action": "check"} 立即检查；{"url": 地址} 改数据地址（空串恢复默认）；{"auto": true/false} 自动联网更新。"""
    if body.get("action") == "check":
        if not app.syncer:
            return 400, {"ok": False, "error": "从仓库运行时数据就是仓库的 data/，随 git 更新，不联网取数"}
        if app.update_state["running"]:
            return 409, {"ok": False, "error": "立即更新正在进行，完成后再检查"}
        started = app.syncer.start("manual")
        return 200, {"ok": True, "started": started, "message": "正在检查联网数据" if started else "联网更新已在进行"}
    settings = load_settings()
    data = dict(settings.get("data") or {})
    if "url" in body:
        url = str(body.get("url") or "").strip()
        if url:
            try:
                url = datasync.normalize_url(url)
            except ValueError as exc:
                return 400, {"ok": False, "error": str(exc)}
        data["url"] = url
    if "auto" in body:
        data["auto"] = bool(body["auto"])
    settings["data"] = data
    save_settings(settings)
    return 200, {"ok": True}


# ──────────────────────────────────────────────────────────────── HTTP


class Handler(BaseHTTPRequestHandler):
    app: App
    server_version = "zuzhibu/" + VERSION

    def log_message(self, fmt, *args):  # 静默，避免控制台刷屏
        pass

    def _send(self, status: int, body: bytes, ctype: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if "Cache-Control" not in (headers or {}):
            self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any, status: int = 200) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"ok": False, "error": message}, status)

    def _local_host(self) -> bool:
        """只认本机地址：挡住借域名重绑定从别的网站读本机接口。"""
        host = urllib.parse.urlsplit("//" + (self.headers.get("Host") or "")).hostname or ""
        return host.lower() in LOCAL_HOSTS

    def _same_origin_json(self) -> bool:
        """POST 只收同源的 JSON：跨站表单和简单请求带不了 application/json，带了浏览器会先预检，本服务不放行。"""
        origin = self.headers.get("Origin")
        if origin is not None and urllib.parse.urlsplit(origin).netloc != self.headers.get("Host"):
            return False
        return (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() == "application/json"

    def do_GET(self) -> None:  # noqa: N802
        url = urllib.parse.urlsplit(self.path)
        qs = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
        path = url.path
        if not self._local_host():
            return self._error(403, "只接受本机地址的请求")
        if self.app.lifetime:
            self.app.lifetime.touch(qs.get("client") if path == "/api/ping" else None)
        try:
            if path.startswith("/api/"):
                return self._api_get(path[5:], qs)
            return self._static(path)
        except Exception as exc:  # 接口出错返回 500 和原因，界面显示
            self._error(500, f"{type(exc).__name__}：{exc}")

    def do_POST(self) -> None:  # noqa: N802
        url = urllib.parse.urlsplit(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if not 0 <= length <= 1 << 20:
            self.close_connection = True
            return self._error(413, "请求太大")
        raw = self.rfile.read(length) if length else b""  # 先读完再答，拒绝时连接也不被重置
        if not self._local_host() or not self._same_origin_json():
            return self._error(403, "只接受本软件页面发来的请求")
        try:
            body = json.loads(raw.decode("utf-8") or "{}") if raw else {}
        except ValueError:
            return self._error(400, "请求不是合法 JSON")
        if not isinstance(body, dict):
            return self._error(400, "请求不是 JSON 对象")
        try:
            if url.path == "/api/bye":
                if self.app.lifetime:
                    self.app.lifetime.bye(str(body.get("client") or ""))
                return self._json({"ok": True})
            if self.app.lifetime:
                self.app.lifetime.touch()
            if url.path == "/api/datasync":
                status, out = data_post(self.app, body)
                return self._json(out, status)
            if url.path == "/api/settings":
                status, out = save_api_settings(body.get("api") or {})
                return self._json(out, status)
            if url.path == "/api/settings/check":
                return self._json(check_api(load_settings()))
            if url.path == "/api/update":
                return self._json(start_update(self.app))
            if url.path == "/api/update/stop":
                return self._json(stop_update(self.app))
            return self._error(404, "没有这个接口")
        except Exception as exc:
            self._error(500, f"{type(exc).__name__}：{exc}")

    def _api_get(self, route: str, qs: dict[str, str]) -> None:
        app = self.app
        if route == "health":
            store = app.fresh()
            return self._json({"ok": True, "app": APP_NAME, "version": VERSION, "mode": "local", "data_dir": str(store.root),
                               "people": len(store.people()), "events": len(store.events()), "meta": store.meta(),
                               "codex": codex_available(), "settings_path": str(settings_path()), "instance": app.instance,
                               "installed": bool(app.dist), "datasync": bool(app.syncer)})
        if route == "ping":
            store = app.fresh()
            return self._json({"ok": True, "data": str(store.root.name) + "|" + str(store.meta().get("updated_at") or ""),
                               "syncing": bool(app.syncer and app.syncer.running)})
        if route == "datasync":
            return self._json(app.data_view())
        if route == "bureaus":
            return self._json(app.bureaus_view())
        if route == "people":
            return self._json({"items": app.people_view(qs.get("bureau"), qs.get("q", ""), qs.get("tier"))})
        m = re.fullmatch(r"people/([a-z0-9][a-z0-9_-]*)", route)
        if m:
            view = app.person_view(m.group(1))
            return self._json(view) if view else self._error(404, "没有这个人物")
        m = re.fullmatch(r"people/([a-z0-9][a-z0-9_-]*)/export", route)
        if m:
            rec = app.fresh().person(m.group(1))
            if not rec:
                return self._error(404, "没有这个人物")
            fmt = qs.get("format", "docx")
            try:
                data, name, ctype = export_bytes(rec, fmt, app.store.root)
            except PermissionError as exc:
                return self._error(403, str(exc))
            except ValueError as exc:
                return self._error(400, str(exc))
            quoted = urllib.parse.quote(name)
            return self._send(200, data, ctype, {"Content-Disposition": f"attachment; filename=\"export{EXPORT_FORMATS.get(fmt, '')}\"; filename*=UTF-8''{quoted}"})
        if route == "events":
            return self._json({"items": app.events_view(qs.get("from"), qs.get("to"), qs.get("bureau"), qs.get("kind"), qs.get("q", ""))})
        if route == "search":
            return self._json(app.search(qs.get("q", "")))
        if route == "settings":
            settings = load_settings()
            return self._json({**api_view(settings), "path": str(settings_path()), "codex": codex_available(),
                               "update": update_plan(settings)})
        if route == "update":
            return self._json(update_view(app))
        # 第二版界面
        if route == "app":
            return self._json(app.app_view())
        m = re.fullmatch(r"rosters/([a-z]+)", route)
        if m:
            view = app.roster_view(m.group(1))
            return self._json(view) if view else self._error(404, "没有这个栏目")
        if route == "index":
            return self._json({"items": app.index_view()})
        m = re.fullmatch(r"profile/([a-z0-9][a-z0-9_-]*)", route)
        if m:
            view = app.profile_view(m.group(1))
            return self._json(view) if view else self._error(404, "没有这个人物")
        if route == "news":
            return self._json({"items": [{k: a[k] for k in ("id", "title", "date", "date_cn")} for a in app.articles()]})
        m = re.fullmatch(r"news/([A-Za-z0-9][A-Za-z0-9_.-]*)", route)
        if m:
            article = app.article(m.group(1))
            return self._json(article) if article else self._error(404, "没有这篇稿件")
        if route == "about":
            return self._json(app.about_view())
        if route == "license":
            return self._json(license_texts())
        return self._error(404, "没有这个接口")

    def _static(self, path: str) -> None:
        rel = urllib.parse.unquote(path).lstrip("/") or "index.html"
        if rel.startswith("portraits/"):
            base = portraits.folder(self.app.fresh().root).resolve()
            target = (base / rel[len("portraits/"):]).resolve()
            if base not in target.parents or not target.is_file():
                return self._error(404, "没有这张照片")
            return self._send(200, target.read_bytes(), "image/jpeg")
        if rel.startswith("fonts/"):  # 界面字体，不进仓库（fonts_dir）
            base = fonts_dir().resolve()
            target = (base / rel[len("fonts/"):]).resolve()
            if base not in target.parents or not target.is_file() or target.suffix.lower() not in FONT_TYPES:
                return self._error(404, "没有这个字体")
            return self._send(200, target.read_bytes(), FONT_TYPES[target.suffix.lower()], {"Cache-Control": "max-age=604800"})
        target = (WEB / rel).resolve()
        if WEB.resolve() not in target.parents and target != WEB.resolve() / "index.html":
            return self._error(403, "不在 web/ 目录内")
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            return self._error(404, "没有这个文件")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)


def make_server(port: int = 8765, data_dir: str | None = None, **app_options: Any) -> ThreadingHTTPServer:
    """app_options 传给 App：home、lifetime、dist、runs_dir。"""
    app = App(data_dir, **app_options)
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.app = app  # type: ignore[attr-defined]
    return server


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AI政见·组织部 本地接口")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data")
    args = ap.parse_args(argv)
    server = make_server(args.port, args.data)
    print(f"{APP_NAME} 本地接口：http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
