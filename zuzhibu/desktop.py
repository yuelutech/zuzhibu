"""桌面版：安装信息、单实例、软件窗口、关窗退出、日志、出错提示（docs/INSTALL.md）。

安装版的程序目录里有 dist.json（安装包构建时写），据此判断是安装版：数据放本机目录、联网更新、
同时只开一个实例、窗口关了就退出。从仓库运行（没有 dist.json）时照旧：读仓库 data/，Ctrl+C 退出。
只用标准库。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable

from .store import REPO, write_json

APP_NAME = "AI政见·组织部"
DIST_FILE = "dist.json"


def dist_info(root: Path = REPO) -> dict[str, Any] | None:
    """安装版的构建信息（版本、构建时间、附带数据日期、数据地址）；从仓库运行时没有，返回 None。"""
    try:
        data = json.loads((root / DIST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def free_port(preferred: int) -> int:
    for port in (preferred, *range(preferred + 1, preferred + 20)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return 0


# ──────────────────────────────────────────────────────────────── 窗口


def edge_path() -> str | None:
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base:
            p = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
            if p.is_file():
                return str(p)
    return shutil.which("msedge")


def open_window(url: str, profile: Path) -> subprocess.Popen | None:
    """用 Edge 的应用窗口打开（单独的窗口配置目录）；没有 Edge 用默认浏览器。返回 Edge 进程，没有时返回 None。"""
    edge = edge_path()
    if edge:
        return subprocess.Popen([edge, f"--app={url}", "--window-size=1320,900", f"--user-data-dir={profile}",
                                 "--no-first-run", "--no-default-browser-check"],
                                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    webbrowser.open(url)
    return None


# ──────────────────────────────────────────────────────────────── 单实例


class Instance:
    """本机目录里的 instance.json 记着正在运行的那一份（端口、口令）。再次启动时直接打开它的窗口，不另起一份。"""

    def __init__(self, home: Path):
        self.path = Path(home) / "instance.json"

    def read(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def alive_url(self, timeout: float = 1.5) -> str | None:
        info = self.read()
        port, token = info.get("port"), info.get("token")
        if not isinstance(port, int) or not token:
            return None
        url = f"http://127.0.0.1:{port}/"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(url + "api/health", timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (OSError, ValueError):
            return None
        return url if data.get("ok") and data.get("instance") == token else None

    def claim(self, port: int, token: str) -> None:
        write_json(self.path, {"port": port, "token": token, "pid": os.getpid(), "started": dt.datetime.now().isoformat(timespec="seconds")})

    def release(self, token: str) -> None:
        if self.read().get("token") == token:
            try:
                self.path.unlink()
            except OSError:
                pass


# ──────────────────────────────────────────────────────────────── 关窗退出


class Lifetime:
    """判断什么时候退出：页面关掉时会说一声（bye），所有页面都说过、grace 秒内没有新页面就退出；
    很久没有任何请求（idle_limit）也退出，免得窗口异常关闭后后台一直挂着。"""

    def __init__(self, grace: float = 15.0, idle_limit: float = 6 * 3600.0, clock: Callable[[], float] = time.monotonic):
        self.grace = grace
        self.idle_limit = idle_limit
        self.clock = clock
        self.lock = threading.Lock()
        self.last_seen = clock()
        self.clients: set[str] = set()
        self.bye_at: float | None = None

    def touch(self, client: str | None = None) -> None:
        with self.lock:
            self.last_seen = self.clock()
            if client:
                self.clients.add(client[:64])
                self.bye_at = None

    def bye(self, client: str | None) -> None:
        with self.lock:
            self.last_seen = self.clock()
            self.clients.discard((client or "")[:64])
            if not self.clients:
                self.bye_at = self.clock()

    def reason(self) -> str | None:
        with self.lock:
            now = self.clock()
            if self.bye_at is not None and not self.clients and now - self.bye_at >= self.grace:
                return "窗口已关闭"
            if now - self.last_seen >= self.idle_limit:
                return "长时间没有使用"
            return None


def watch(lifetime: Lifetime, proc: subprocess.Popen | None, stop: Callable[[], None], *,
          poll: float = 1.0, handoff: float = 5.0) -> threading.Thread:
    """后台线程：Edge 窗口进程结束（启动 handoff 秒以后才算，之前结束的是把窗口交给了已开着的 Edge）或 Lifetime 说该退，就调 stop。"""

    def run() -> None:
        started = time.monotonic()
        watching = proc is not None
        while True:
            if watching and proc.poll() is not None:
                if time.monotonic() - started >= handoff:
                    print(f"{stamp()} 软件窗口已关闭，退出", flush=True)
                    break
                watching = False  # 窗口交给了已开着的 Edge，改看页面的 bye
            why = lifetime.reason()
            if why:
                print(f"{stamp()} {why}，退出", flush=True)
                break
            time.sleep(poll)
        stop()

    t = threading.Thread(target=run, name="lifetime", daemon=True)
    t.start()
    return t


def watch_parent(pid: int, stop: Callable[[], None], poll: float = 2.0) -> threading.Thread:
    """后台线程：父进程（桌面外壳）结束就调 stop。外壳正常退出时会先结束本服务；外壳意外崩溃时靠这里收尾，不留孤儿进程。"""

    def wait_windows() -> bool:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return True  # 打不开：父进程已经不在了
        try:
            kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)
        finally:
            kernel32.CloseHandle(handle)
        return True

    def alive() -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def run() -> None:
        if sys.platform == "win32":
            wait_windows()
        else:
            while alive():
                time.sleep(poll)
        print(f"{stamp()} 桌面外壳已退出，本机服务随之退出", flush=True)
        stop()

    t = threading.Thread(target=run, name="parent-watch", daemon=True)
    t.start()
    return t


# ──────────────────────────────────────────────────────────────── 日志与提示


def stamp() -> str:
    return dt.datetime.now().isoformat(sep=" ", timespec="seconds")


def setup_logging(home: Path, max_bytes: int = 1_000_000) -> Path | None:
    """没有控制台（双击图标启动）时把输出写进 <本机目录>/logs/app.log，超过 1 MB 换一份。"""
    if sys.stdout is not None and sys.stderr is not None:
        return None
    logs = Path(home) / "logs"
    try:
        logs.mkdir(parents=True, exist_ok=True)
        path = logs / "app.log"
        if path.exists() and path.stat().st_size > max_bytes:
            os.replace(path, logs / "app.log.1")
        fh = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return None
    sys.stdout = sys.stderr = fh
    print(f"—— {stamp()} 启动 ——", flush=True)
    return path


def alert(text: str, title: str = APP_NAME) -> None:
    """出错提示：Windows 弹对话框，同时写日志。"""
    print(text, file=sys.stderr, flush=True)
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, title, 0x10 | 0x40000)  # 错误图标、置顶
        except Exception:  # noqa: BLE001  弹不出来就只记日志
            pass
