"""桌面启动：python -m zuzhibu

启动本地接口（只监听 127.0.0.1），再用浏览器的应用窗口打开界面。正式的桌面版由 desktop/ 的外壳启动：
外壳以 --no-window --port 0 --parent-pid <外壳进程号> 起本服务，读输出里的地址后在自己的窗口里打开，外壳退出本服务随之退出。
- 从仓库运行：读仓库 data/，不联网取数；关掉窗口后在命令行按 Ctrl+C 退出；--no-window 只启动服务。
- 安装版（程序目录有 dist.json）：数据放本机目录（%LOCALAPPDATA%\\zuzhibu\\data），首次用随软件附带的数据铺底，
  启动时和每天一次联网检查最新数据（维护者每月更新一次）（zuzhibu/datasync.py）；已在运行就只打开窗口；窗口关了就退出；输出写 logs\\app.log。
"""

from __future__ import annotations

import argparse
import sys
import threading
import traceback
from pathlib import Path

from . import datasync, desktop
from .server import APP_NAME, make_server
from .store import REPO


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m zuzhibu", description=f"{APP_NAME} 桌面版")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--data", help="只读这一份数据目录（默认仓库 data/），不联网取数")
    ap.add_argument("--home", help="本机目录：联网更新的数据、日志、窗口配置，默认 %%LOCALAPPDATA%%\\zuzhibu")
    ap.add_argument("--no-window", action="store_true", help="只启动本地服务，不打开窗口")
    ap.add_argument("--parent-pid", type=int, help="桌面外壳的进程号：外壳结束（含意外崩溃）本服务随之退出")
    sync = ap.add_mutually_exclusive_group()
    sync.add_argument("--sync", dest="sync", action="store_true", default=None, help="联网取最新数据（安装版默认开）")
    sync.add_argument("--no-sync", dest="sync", action="store_false", help="不联网取数")
    args = ap.parse_args(argv)
    if args.sync and args.data:
        ap.error("--data 指定了数据目录，不能同时 --sync")

    dist = desktop.dist_info()
    home = Path(args.home) if args.home else datasync.app_home()
    if dist:
        desktop.setup_logging(home)
    try:
        return run(args, dist, home)
    except Exception as exc:  # noqa: BLE001  双击启动没有控制台，出错要弹窗
        traceback.print_exc()
        if dist:
            desktop.alert(f"{APP_NAME} 启动失败：{exc}\n\n日志：{home / 'logs' / 'app.log'}")
        return 1


def run(args: argparse.Namespace, dist: dict | None, home: Path) -> int:
    packaged = dist is not None
    window = not args.no_window
    profile = home / "edge-profile"
    instance = desktop.Instance(home) if packaged else None
    if instance and window:
        url = instance.alive_url()
        if url:  # 已经开着：只打开窗口
            desktop.open_window(url, profile)
            return 0

    data_home = None
    if (packaged if args.sync is None else args.sync) and not args.data:
        data_home = prepare_data(home / "data", REPO / "data")

    lifetime = desktop.Lifetime() if packaged and window else None
    port = desktop.free_port(args.port)
    server = make_server(port, args.data, home=data_home, lifetime=lifetime, dist=dist,
                         runs_dir=home / "runs" if packaged else None)
    app = server.app
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    if instance:
        instance.claim(server.server_address[1], app.instance)
    if app.syncer:
        app.syncer.run_in_background()
    if args.parent_pid:
        desktop.watch_parent(args.parent_pid, server.shutdown)
    print(f"{APP_NAME}：{url}（{'关掉窗口即退出' if lifetime else 'Ctrl+C 退出'}）", flush=True)

    if window:
        def launch() -> None:
            proc = desktop.open_window(url, profile)
            if lifetime:
                desktop.watch(lifetime, proc, server.shutdown)

        threading.Timer(0.3, launch).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if app.syncer:
            app.syncer.stop()
        if instance:
            instance.release(app.instance)
        server.server_close()
    return 0


def prepare_data(root: Path, bundled: Path) -> datasync.DataHome | None:
    """本机数据目录：清掉上次中断留下的半成品，没有数据或附带数据更新时铺底。不可用时返回 None，改读附带数据。"""
    data_home = datasync.DataHome(root)
    try:
        data_home.recover()
        seeded = datasync.ensure_seed(data_home, bundled)
        if seeded:
            print(f"{desktop.stamp()} 已用附带数据铺底：{seeded['files']} 个文件，数据日期 {seeded['data_date']}", flush=True)
    except (datasync.SyncError, OSError) as exc:
        print(f"{desktop.stamp()} 本机数据目录不可用（{exc}），改读随软件附带的数据", flush=True)
    return data_home if data_home.current() else None


if __name__ == "__main__":
    sys.exit(main())
