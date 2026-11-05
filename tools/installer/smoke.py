"""试装安装包：python -m tools.installer.smoke dist/zuzhibu-<版本>-win-x64-setup.exe [--work 目录] [--keep] [--quick]

全程在后台：软件窗口不显示、不进任务栏，不放开始菜单和桌面快捷方式（安装时带 --no-shortcuts），
装在 --work 目录（默认 build/smoke/<时间>），软件的本机目录和设置也放在这里，不碰这台电脑上真实的安装和数据。
1. 静默安装（/S --no-shortcuts /D=目录），核对“已安装”登记，逐项核对装好的内容（contents.py：程序和界面、附带数据、标准像、字体、不带账号密钥）；
2. 启动装好的软件（ZUZHIBU_SHELL_TEST：窗口不显示，就绪后写 ready.json），核对本地服务答应、附带数据铺底、窗口打开的是本地服务；
   像使用者那样在设置里填一个 AI 接口（假密钥，地址不存在）；
3. 再启动一次：应立即退出，原来那份收到“又被打开”（只开一个实例）；
4. 让软件退出：本地服务随之结束；强行结束外壳：本地服务也自己退出，不留进程；
5. 启动时崩溃的自救（ZUZHIBU_SHELL_TEST_CRASH 在启动阶段弄崩页面进程、显卡进程）：崩一次清掉显卡缓存自动重开；
   连崩两次关闭显卡加速重开，记进本机设置 local.json，下次直接关着开；关了还崩就停下，不留进程；
6. 升级与降级（--quick 跳过）：用同一待装目录打一个更高版本的包装上去，旧版自动卸掉、装在原处，本机数据、设置
   （含填的 AI 接口）、窗口记录原样保留，新版读得到填的接口；再装回原包应被拦下；
7. 静默卸载，核对程序目录、登记都清掉，本机数据和设置保留。
--keep 不卸载、不删工作目录。--shell <win-unpacked 目录> 不安装，只做第 5 步；只核对、只结束这次开的进程，
别的试装或手动从同一目录开着的程序不算、不碰。只能在 Windows 上跑。
"""

from __future__ import annotations

import argparse
import ctypes
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import winreg
from pathlib import Path
from typing import Any

from zuzhibu import datasync
from zuzhibu.server import VERSION, mask

from . import build, contents, fonts, say, utf8_output

PRODUCT = "AI政见·组织部"
EXE = f"{PRODUCT}.exe"
UNINSTALLER = f"Uninstall {PRODUCT}.exe"
UNINSTALL_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
SHELL_FOLDERS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# 试装时在设置里填的 AI 接口：假密钥，地址用保留的 .invalid 域名，什么也连不上
TEST_API = {"provider": "openai", "base_url": "https://example.invalid/v1", "model": "smoke-test-model",
            "api_key": "fake-key-for-smoke-test"}


def log(msg: str) -> None:
    say(f"[{dt.datetime.now():%H:%M:%S}] {msg}")


class Failed(AssertionError):
    pass


def expect(cond: Any, msg: str) -> None:
    if not cond:
        raise Failed(msg)
    log("  ✓ " + msg)


def wait_for(pred, timeout: float, step: float = 0.3):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = pred()
        if value:
            return value
        time.sleep(step)
    return pred()


# ──────────────────────────────────────────────────────────────── 系统信息


def installed() -> list[dict[str, str]]:
    """“已安装”里本软件的登记（当前用户）。"""
    out = []
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_ROOT)
    except OSError:
        return out
    with root:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(root, name) as key:
                    values = {}
                    for field in ("DisplayName", "DisplayVersion", "Publisher", "InstallLocation", "UninstallString"):
                        try:
                            values[field] = str(winreg.QueryValueEx(key, field)[0])
                        except OSError:
                            values[field] = ""
            except OSError:
                continue
            if values["DisplayName"] == PRODUCT:
                out.append({"key": name, **values})
    return out


def process_gone(pid: int, timeout: float) -> bool:
    """进程是否在 timeout 秒内结束（打不开说明已经不在）。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.OpenProcess(0x00100000, False, pid)
    if not handle:
        return True
    try:
        return kernel32.WaitForSingleObject(handle, int(timeout * 1000)) == 0
    finally:
        kernel32.CloseHandle(handle)


def processes_under(folder: Path, pids: set[int] | None = None) -> list[str]:
    """还在跑的、程序文件在 folder 下的进程（用系统自带的 PowerShell 查）。给了 pids 就只算这些进程和由它们开出来的
    （本次试装开的外壳、它的子进程和本地服务；父进程退了，子进程登记的父进程号不变），别人从同一目录开的不算。"""
    if pids is not None and not pids:
        return []
    mine = (" -and ($ids -contains [string]$_.ProcessId -or $ids -contains [string]$_.ParentProcessId)" if pids else "")
    cmd = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; $ids = \"$env:ZZB_PIDS\" -split ','; "
           "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and "
           f"$_.ExecutablePath.StartsWith($env:ZZB_DIR, 'CurrentCultureIgnoreCase'){mine} }} | "
           "ForEach-Object { \"$($_.ProcessId) $($_.Name)\" }")
    env = dict(os.environ, ZZB_DIR=str(folder), ZZB_PIDS=",".join(str(p) for p in sorted(pids or ())))
    proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", cmd], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, creationflags=NO_WINDOW)
    return [line for line in proc.stdout.splitlines() if line.strip()]


def shell_folder(name: str, default: Path) -> Path:
    """当前用户的桌面（Desktop）、开始菜单程序（Programs）等目录：可能被挪到别处，按系统登记的取。"""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, SHELL_FOLDERS) as key:
            return Path(os.path.expandvars(winreg.QueryValueEx(key, name)[0]))
    except OSError:
        return default


def strays() -> set[str]:
    """不该出现的目录：安装程序的副本（自动更新缓存）、Electron 的默认数据目录、这台电脑真实的本机目录。"""
    local, roaming = Path(os.environ.get("LOCALAPPDATA", "")), Path(os.environ.get("APPDATA", ""))
    candidates = [local / "zuzhibu-desktop-updater", roaming / PRODUCT, local / PRODUCT, roaming / "zuzhibu-desktop",
                  local / "zuzhibu"]
    return {str(p) for p in candidates if p.exists()}


def get_json(url: str) -> dict:
    with OPENER.open(url, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def tree(root: Path) -> dict[str, str]:
    """目录里每个文件的 SHA-256（相对路径为键）。"""
    return {p.relative_to(root).as_posix(): contents.sha256(p) for p in sorted(root.rglob("*")) if p.is_file()}


def post_json(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    with OPENER.open(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ──────────────────────────────────────────────────────────────── 软件


LAUNCHED: set[int] = set()  # 这次试装开过的外壳进程号（失败时只结束这些）


class Shell:
    """装好的软件：试装模式启动（窗口不显示），本机目录和设置都在工作目录里。"""

    def __init__(self, exe: Path, work: Path):
        self.exe, self.work = exe, work
        self.home, self.appdata, self.test = work / "home", work / "appdata", work / "test"
        self.env = dict(os.environ, ZUZHIBU_HOME=str(self.home), APPDATA=str(self.appdata), ZUZHIBU_SHELL_TEST=str(self.test))
        for key in ("ZUZHIBU_REPO", "ZUZHIBU_PYTHON", "ZUZHIBU_DATA", "ZUZHIBU_DEVTOOLS", "ZUZHIBU_GPU", "ZUZHIBU_SHELL_TEST_CRASH"):
            self.env.pop(key, None)
        self.proc: subprocess.Popen | None = None
        self.report: dict = {}

    def launch(self) -> None:
        LAUNCHED.update(self.pids())  # 上一次启动（含自己重开的）记下来再清掉试装目录
        shutil.rmtree(self.test, ignore_errors=True)
        self.test.mkdir(parents=True)
        self.appdata.mkdir(parents=True, exist_ok=True)
        self.report = {}
        self.proc = subprocess.Popen([str(self.exe)], env=self.env, cwd=str(self.work))
        LAUNCHED.add(self.proc.pid)

    def pids(self) -> set[int]:
        """这个试装目录这次启动开过的外壳进程号：最初启动的那个，加每次自己重开的（launches.log 的 pid=）。"""
        found = {int(n) for n in re.findall(r"\bpid=(\d+)", "\n".join(self.launches()))}
        return found | ({self.proc.pid} if self.proc else set())

    def start(self, timeout: float = 240, launch: int = 0) -> dict:
        """启动并等就绪；launch：等第几次启动（从 0 数，外壳崩了会自己重开）写的就绪报告。"""
        self.launch()
        ready = self.test / "ready.json"

        def reported() -> dict | None:
            try:
                data = json.loads(ready.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return None
            return data if data.get("launch", 0) >= launch else None

        self.report = wait_for(reported, timeout, 0.5) or {}
        if not self.report:
            raise Failed(f"{timeout:.0f} 秒内没有就绪（等第 {launch + 1} 次启动）；日志：{self.home / 'logs'}")
        return self.report

    def quit(self, timeout: float = 60) -> bool:
        """让软件退出：等写就绪报告的那个进程（自己重开过的，就不是最初启动的那个）结束。"""
        (self.test / "quit").write_text("1", encoding="utf-8")
        gone = process_gone(self.report.get("pid") or self.proc.pid, timeout)
        try:
            self.proc.wait(10)
        except subprocess.TimeoutExpired:
            return False
        return gone

    def launches(self) -> list[str]:
        """外壳每次启动记一行（试装模式）：pid=进程号、prior=连着几次没打开、gpu=显卡加速、clear=清没清显卡缓存。"""
        log = self.test / "launches.log"
        return log.read_text(encoding="utf-8").splitlines() if log.is_file() else []


# ──────────────────────────────────────────────────────────────── 主流程


def recovery_checks(exe: Path, work: Path) -> None:
    """启动时崩溃的自救：试装模式下让外壳在启动阶段弄崩页面进程、显卡进程（ZUZHIBU_SHELL_TEST_CRASH），看它怎么收场。
    每种情形用自己的本机目录和设置目录，不影响别的步骤。"""
    once = Shell(exe, work / "crash-once")
    once.env["ZUZHIBU_SHELL_TEST_CRASH"] = "renderer"
    marker = once.home / "shell" / "GPUCache" / "smoke-marker"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("x", encoding="utf-8")
    report = once.start(launch=1)
    runs = once.launches()
    expect(len(runs) == 2 and "clear=yes" in runs[1] and report["launch"] == 1,
           f"启动时页面进程崩溃：清掉显卡缓存，自动重开一次（启动 {len(runs)} 次）")
    expect(not marker.exists(), "显卡缓存清掉了")
    local = once.appdata / "zuzhibu" / "local.json"
    expect(not report["gpuOff"] and not local.exists(), f"只崩一次：没有关显卡加速，本机设置没动（这次的显卡合成：{report['gpu']}）")
    expect(get_json(report["url"] + "api/health")["ok"], "重开后本地服务答应")
    expect(once.quit(), "重开的那份按要求退出")

    twice = Shell(exe, work / "crash-twice")
    twice.env["ZUZHIBU_SHELL_TEST_CRASH"] = "gpu,renderer"
    local = twice.appdata / "zuzhibu" / "local.json"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(json.dumps({"library": "D:\\素材"}, ensure_ascii=False), encoding="utf-8")
    report = twice.start(launch=2)
    runs = twice.launches()
    expect(len(runs) == 3 and "gpu=off" in runs[2], f"显卡进程崩、清掉缓存后页面进程又崩：关闭显卡加速重开（启动 {len(runs)} 次）")
    saved = json.loads(local.read_text(encoding="utf-8"))
    expect(saved == {"library": "D:\\素材", "gpu": "off"}, f"“关闭显卡加速”记进本机设置，原有的项保留：{saved}")
    expect(report["gpuOff"] and report["gpu"] != "enabled", f"这次关着显卡加速（{report['gpu']}）")
    expect(twice.quit(), "按要求退出")
    del twice.env["ZUZHIBU_SHELL_TEST_CRASH"]
    report = twice.start()
    expect(report["gpuOff"] and report["prior"] == 0 and len(twice.launches()) == 1, "下次打开直接关着显卡加速，不再先崩")
    expect(twice.quit(), "按要求退出")

    always = Shell(exe, work / "crash-always")
    always.env["ZUZHIBU_SHELL_TEST_CRASH"] = "renderer,renderer,renderer"
    always.launch()
    expect(wait_for((always.test / "gave-up").is_file, 180), "关了显卡加速还崩：不再自动重开，给出说明")
    expect(len(always.launches()) == 3, f"一共只开了 3 次（{len(always.launches())}）")
    # 刚退出的进程要一会儿才没：每秒查一次，最多等 10 秒；只认这一步开的，别的试装或手动从同一目录开的不算
    mine = LAUNCHED | once.pids() | twice.pids() | always.pids()
    expect(wait_for(lambda: not processes_under(exe.parent, mine), 10, 1),
           f"没有留下进程（这一步开过 {len(mine)} 次外壳）：{processes_under(exe.parent, mine)}")
    expect(not (always.home / "shell" / "startup.json").exists(), "启动记录清掉了，使用者再打开时从头来")


def install(setup: Path, target: Path | None, logname: str, work: Path) -> int:
    """静默安装；target 给了就装到那里（/D= 要放最后、不加引号）。"""
    line = f'"{setup}" /S --no-shortcuts' + (f" /D={target}" if target else "")
    proc = subprocess.run(line, cwd=str(work), creationflags=NO_WINDOW)
    (work / logname).write_text(f"{line}\n退出码 {proc.returncode}\n", encoding="utf-8")
    return proc.returncode


def run(setup: Path, work: Path, keep: bool, quick: bool) -> None:
    inst = work / "inst"
    home = work / "home"
    settings_file = work / "appdata" / "zuzhibu" / "settings.json"
    window_state = home / "shell" / "window-state.json"
    roaming = Path(os.environ.get("APPDATA", ""))
    start_menu = shell_folder("Programs", roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs") / f"{PRODUCT}.lnk"
    desktop_link = shell_folder("Desktop", Path(os.environ.get("USERPROFILE", "")) / "Desktop") / f"{PRODUCT}.lnk"

    before = strays()
    log(f"1. 静默安装 {setup.name} → {inst}")
    code = install(setup, inst, "install.log", work)
    expect(code == 0, f"安装程序返回 0（实际 {code}）")
    res = inst / "resources"
    for rel in (EXE, UNINSTALLER, "resources/app.asar", "resources/runtime/python.exe", "resources/runtime/python314._pth",
                "resources/pyapp/zuzhibu/server.py", "resources/pyapp/web/index.html", "resources/pyapp/data/manifest.json",
                "resources/pyapp/dist.json"):
        expect((inst / rel).is_file(), f"装好了 {rel}")
    log("  逐项核对装好的内容：")
    expect(contents.report(contents.check(inst), log), "装好的内容逐项核对通过")
    entries = installed()
    expect(len(entries) == 1 and entries[0]["DisplayVersion"] == VERSION, f"“已安装”里有一项 {VERSION}：{entries}")
    expect(entries[0]["Publisher"] == "AI政见", f"发布者是 AI政见（{entries[0]['Publisher']}）")
    expect(not start_menu.exists() and not desktop_link.exists(), "带 --no-shortcuts 时没有放快捷方式")
    expect(strays() == before, f"没有在用户目录留下安装程序副本等：{strays() - before}")
    bundled = json.loads((res / "pyapp" / "data" / "manifest.json").read_text(encoding="utf-8"))

    log("2. 后台启动（窗口不显示）")
    shell = Shell(inst / EXE, work)
    report = shell.start()
    expect(not report["visible"], "窗口没有显示")
    expect(report["url"].startswith("http://127.0.0.1:"), f"窗口已打开本地服务的页面：{report['url']}（{report['title']}）")
    health = get_json(report["url"] + "api/health")
    expect(health["ok"] and health["installed"] and health["datasync"], f"本地服务答应：{report['url']}，安装版、联网更新已开")
    expect(health["people"] == bundled["counts"]["people"], f"附带数据铺底：{health['people']} 人")
    expect(Path(health["data_dir"]).is_relative_to(home), "数据读工作目录里的本机快照，不碰真实的本机目录")
    expect((home / "logs" / "service.log").is_file() and (home / "logs" / "shell.log").is_file(), "外壳和本地服务都写了日志")
    saved = post_json(report["url"] + "api/settings", {"api": TEST_API})
    shown = get_json(report["url"] + "api/settings")["api"]
    expect(saved.get("ok") and settings_file.is_file(), f"设置里填的 AI 接口存进了本机设置 {settings_file.relative_to(work)}")
    expect(shown.get("model") == TEST_API["model"] and shown.get("api_key") == mask(TEST_API["api_key"]), "设置页读回填的接口，密钥打码显示")
    for font in fonts.FONTS:  # 界面经本地服务的 /fonts/ 用随软件分发的字体
        with OPENER.open(report["url"] + "fonts/" + urllib.parse.quote(font.file), timeout=60) as resp:
            body = resp.read()
        expect(hashlib.sha256(body).hexdigest() == font.sha256, f"本地服务供给字体 /fonts/{font.file}（{len(body) / 1e6:.1f} MB）")
    saved_settings = settings_file.read_bytes()

    log("3. 再启动一次")
    second = subprocess.Popen([str(inst / EXE)], env=shell.env, cwd=str(work))
    expect(second.wait(60) == 0, "第二份立即退出")
    expect(wait_for((shell.test / "second-instance").is_file, 10), "原来那份收到“又被打开”")

    log("4. 退出与收尾")
    service_pid = report["servicePid"]
    expect(shell.quit(), "软件按要求退出")
    expect(process_gone(service_pid, 20), "本地服务随之结束")
    shell.start()
    service_pid = shell.report["servicePid"]
    shell.proc.kill()  # 模拟外壳崩溃
    expect(process_gone(service_pid, 30), "强行结束外壳后，本地服务自己退出")
    expect(wait_for(lambda: not processes_under(inst), 30, 1), f"安装目录里没有残留进程：{processes_under(inst)}")
    expect(strays() == before, f"运行没有碰真实的本机目录、没建默认数据目录：{strays() - before}")

    log("5. 启动时崩溃的自救")
    recovery_checks(inst / EXE, work)
    expect(strays() == before, f"自救时也没有碰真实的本机目录：{strays() - before}")

    final = setup
    if not quick:
        log("6. 升级与降级")
        newer = build.electron_build(work / "variant", version="99.0.0")
        kept = {"本机目录": home, "设置目录": settings_file.parent}
        kept_before = {name: tree(path) for name, path in kept.items()}
        code = install(newer, None, "upgrade.log", work)
        expect(code == 0, f"装新版返回 0（实际 {code}）")
        entries = installed()
        expect(len(entries) == 1 and entries[0]["DisplayVersion"] == "99.0.0", f"旧版已卸掉，只剩新版：{entries}")
        expect((inst / EXE).is_file() and (inst / "resources" / "runtime" / "python.exe").is_file(), "新版装在原处")
        for name, path in kept.items():
            expect(tree(path) == kept_before[name], f"升级没动{name}里的任何文件（{len(kept_before[name])} 个）")
        expect(datasync.DataHome(home / "data").current() is not None, "本机数据保留（当前快照还在）")
        expect(settings_file.read_bytes() == saved_settings, "本机设置原样保留，含自己填的 AI 接口")
        expect(window_state.is_file(), "窗口大小和位置的记录保留")
        expect(strays() == before, f"升级后也没有留下安装程序副本：{strays() - before}")
        code = install(setup, None, "downgrade.log", work)
        expect(code == 2 and installed()[0]["DisplayVersion"] == "99.0.0", f"装回旧包被拦下（返回 {code}）")
        report = shell.start()
        expect(get_json(report["url"] + "api/health")["ok"], "升级后软件照常运行")
        shown = get_json(report["url"] + "api/settings")["api"]
        expect(shown.get("model") == TEST_API["model"] and shown.get("api_key") == mask(TEST_API["api_key"]),
               "升级后的软件读到原来填的 AI 接口")
        shell.quit()
        final = newer

    if keep:
        log(f"保留安装：{inst}")
        return
    log("7. 静默卸载")
    uninstaller = inst / UNINSTALLER
    subprocess.run(f'"{uninstaller}" /S', cwd=str(work), creationflags=NO_WINDOW)
    gone = wait_for(lambda: not installed() and not (inst / EXE).exists(), 120, 1)
    expect(gone, "“已安装”里已没有，程序已删")
    leftover = [p for p in inst.rglob("*")] if inst.exists() else []
    expect(not leftover, f"程序目录已清空：{leftover[:5]}")
    expect(datasync.DataHome(home / "data").current() is not None, "本机数据保留（卸载不删）")
    expect(settings_file.read_bytes() == saved_settings, "本机设置保留（卸载不删）")
    expect(not start_menu.exists() and not desktop_link.exists(), "没有留下快捷方式")
    expect(strays() == before, f"用户目录里没有留下东西：{strays() - before}")
    log(f"试装用的安装包：{final}")


def cleanup(work: Path, folder: Path, existing: list[dict[str, str]], uninstall: bool) -> None:
    """失败或中断时收拾：结束这次开的外壳（含自己重开的）、它们的子进程和本地服务，再卸掉这次装上的。
    每一步单独兜住，前一步出错不耽误后一步；别人从同一目录开的、这台电脑原来装的都不碰。"""
    try:
        for log_file in work.rglob("launches.log"):
            LAUNCHED.update(int(n) for n in re.findall(r"\bpid=(\d+)", log_file.read_text(encoding="utf-8", errors="replace")))
    except OSError:
        pass
    try:
        for line in processes_under(folder, LAUNCHED):
            subprocess.run(["taskkill", "/PID", line.split()[0], "/T", "/F"], capture_output=True, creationflags=NO_WINDOW, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass
    if not uninstall:
        return
    try:
        for entry in installed():
            if entry not in existing and entry["UninstallString"]:
                subprocess.run(entry["UninstallString"] + " /S", creationflags=NO_WINDOW, timeout=300)
    except (OSError, subprocess.SubprocessError):
        pass


def main(argv: list[str] | None = None) -> int:
    utf8_output()
    ap = argparse.ArgumentParser(prog="python -m tools.installer.smoke", description="后台试装安装包")
    ap.add_argument("setup", nargs="?", help="要试装的 zuzhibu-<版本>-win-x64-setup.exe")
    ap.add_argument("--shell", metavar="目录", help="不安装，只试外壳的启动自救：electron-builder 的解包目录（win-unpacked）")
    ap.add_argument("--work", help="工作目录（安装位置、本机目录、日志），默认 build/smoke/<时间>")
    ap.add_argument("--keep", action="store_true", help="不卸载、不删工作目录")
    ap.add_argument("--quick", action="store_true", help="跳过升级与降级（不另打高版本的包）")
    ap.add_argument("--force", action="store_true", help="这台电脑已装有本软件时也试装")
    args = ap.parse_args(argv)
    if sys.platform != "win32":
        raise SystemExit("只能在 Windows 上试装")
    if bool(args.setup) == bool(args.shell):
        ap.error("给安装程序，或者用 --shell 给解包目录，二选一")
    existing = [] if args.shell else installed()
    if existing and not args.force:
        raise SystemExit(f"这台电脑已装有本软件：{existing}。试装会动到它；确实要试，加 --force")
    work = Path(args.work) if args.work else build.BUILD / "smoke" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    work.mkdir(parents=True, exist_ok=True)
    log(f"工作目录：{work}")
    try:
        if args.shell:
            exe = Path(args.shell).resolve() / EXE
            if not exe.is_file():
                raise Failed(f"{args.shell} 里没有 {EXE}")
            log("启动时崩溃的自救（不安装）")
            others = processes_under(exe.parent)
            if others:
                log(f"  · 解包目录里已有 {len(others)} 个进程在跑（别的试装或手动打开的），只核对这次开的：{'、'.join(others[:6])}")
            recovery_checks(exe, work)
        else:
            run(Path(args.setup).resolve(), work, args.keep, args.quick)
    except BaseException as exc:  # 任何失败、异常或中断：先收掉这次开的程序、卸掉装上的，再报原因，不留在这台电脑上
        cleanup(work, Path(args.shell).resolve() if args.shell else work / "inst", existing, uninstall=not args.shell)
        log(f"✗ 试装没通过：{type(exc).__name__}：{exc}")
        log(f"日志在 {work}（install.log、home\\logs\\）")
        if not isinstance(exc, (Failed, KeyboardInterrupt)):
            raise
        return 1
    log("试装全部通过")
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
