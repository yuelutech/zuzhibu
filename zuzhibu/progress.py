"""立即更新的进度：一局流水线分 6 步上报，本地服务读出来给设置里的进度条（docs/PRODUCT.md 第七、八节）。

进度文件是一份 JSON，由 bureaus/dispatch/run.py --progress 写（先写临时文件再替换），zuzhibu/server.py 读。
停止靠停止文件（run.py --stop-file）：服务端建这个文件，流水线在每次会话、每条核实、每份简历之前看一眼，
停下以后照样把已核实的动态写回，再报 stopped。

提示语照维护者 2026-10-21 审定的文案稿（设置一节），一字不改，集中在本模块：界面直接显示 text、message。

只用标准库。
"""

from __future__ import annotations

import datetime as dt
import threading
from pathlib import Path
from typing import Any

from .store import read_json, write_json

STEPS = ("连接接口", "查询公开信息", "深度思考", "逐条核实", "获取简历", "更新简历")
STATES = ("idle", "running", "stopping", "stopped", "done", "failed")

#: 每一步在进度条上占的区间（百分比）：深度思考、逐条核实最费时。
_SPANS = {1: (0, 4), 2: (4, 10), 3: (10, 55), 4: (55, 85), 5: (85, 94), 6: (94, 99)}


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


class Progress:
    """流水线这一侧：写进度文件、看停止文件。path 为 None 时只在内存里记（命令行跑、测试）。线程安全。"""

    def __init__(self, path: str | Path | None = None, stop_file: str | Path | None = None):
        self.path = Path(path) if path else None
        self.stop_file = Path(stop_file) if stop_file else None
        self.lock = threading.Lock()
        self.data: dict[str, Any] = {"state": "running", "step": 0, "detail": {}, "result": {"events": 0, "resumes": 0},
                                     "error": None, "started": _now(), "finished": None}
        self._save()

    def _save(self) -> None:
        if self.path is not None:
            write_json(self.path, self.data)

    def step(self, n: int, **detail: Any) -> None:
        """进入第 n 步（1–6），detail 是这一步的计数。"""
        with self.lock:
            self.data.update(step=n, detail=detail)
            self._save()

    def detail(self, **detail: Any) -> None:
        with self.lock:
            self.data["detail"] = {**self.data["detail"], **detail}
            self._save()

    def result(self, **counts: int) -> None:
        with self.lock:
            self.data["result"] = {**self.data["result"], **counts}
            self._save()

    def stop_requested(self) -> bool:
        return bool(self.stop_file and self.stop_file.exists())

    def finish(self, state: str, error: str | None = None) -> None:
        """结束：done、stopped、failed。error 是“接口额度不足”一类短语。"""
        with self.lock:
            self.data.update(state=state, error=error, finished=_now())
            self._save()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.data)


def read(path: str | Path) -> dict[str, Any]:
    try:
        data = read_json(Path(path))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def text(status: dict[str, Any]) -> str:
    """进行中这一步的提示语（文案稿：进度条下方逐步显示）。"""
    if status.get("state") not in ("running", "stopping"):
        return ""
    step, d = status.get("step") or 0, status.get("detail") or {}
    if step == 1:
        return "正在连接接口……"
    if step == 2:
        return "正在查询公开信息……（白宫、国会、联邦公报、证券交易委员会等官方来源）"
    if step == 3:
        where = f"　{d['group']} {d['index']}/{d['total']}" if d.get("group") and d.get("total") else ""
        return f"正在深度思考……（逐项研判人事变动{where}）"
    if step == 4:
        return f"正在逐条核实……（第 {d['index']} 条，共 {d['total']} 条）" if d.get("total") else "正在逐条核实……"
    if step == 5:
        return f"正在获取简历……（新任人员 {d.get('people', 0)} 人）"
    if step == 6:
        return f"正在更新简历……（{d.get('people', 0)} 人）"
    return ""


def message(status: dict[str, Any]) -> str:
    """结束后的提示语（文案稿：完成、未完成）。"""
    state, result = status.get("state"), status.get("result") or {}
    events, resumes = int(result.get("events") or 0), int(result.get("resumes") or 0)
    if state == "done":
        return f"更新完成。新增人事动态 {events} 条，更新简历 {resumes} 份。" if events else "更新完成。暂无新的人事动态。"
    if state == "stopped":
        return f"更新已停止。已核实的 {events} 条人事动态已保存。"
    if state == "failed":
        return f"更新未完成：{status.get('error') or '程序出错'}。已核实的结果已保存。"
    return ""


def percent(status: dict[str, Any]) -> int:
    """进度条：按步骤所占区间，再按这一步的计数往前推。"""
    if status.get("state") in ("done", "stopped", "failed"):
        return 100
    step, d = status.get("step") or 0, status.get("detail") or {}
    if step not in _SPANS:
        return 0
    low, high = _SPANS[step]
    total, index = d.get("total") or 0, d.get("index") or 0
    share = (max(0, index - 1) / total) if total else 0.0
    return int(low + (high - low) * min(1.0, share))


def view(status: dict[str, Any]) -> dict[str, Any]:
    """给界面的状态：原样字段，加 text、message、percent。"""
    return {**status, "text": text(status), "message": message(status), "percent": percent(status)}
