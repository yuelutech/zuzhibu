"""Windows 安装包：构建（build.py）、随软件分发的字体（fonts.py）、装好内容的核对（contents.py）、后台试装（smoke.py）、图标（icon.py）。"""

import sys


def utf8_output() -> None:
    """标准输出、错误输出改用 UTF-8，写不了的字换成问号。输出被重定向（管道、文件）时 Windows 默认按 GBK 写，
    打印 ✓✗ 会抛 UnicodeEncodeError，脚本半路退出；真控制台本来就是 UTF-8，不受影响。各脚本的 main 先调它。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def say(text: str) -> None:
    """打印一行；输出写不了（编码不对、管道断了）就算了，不耽误脚本本身，更不能让它半路退出、丢下开着的程序。"""
    try:
        print(text, flush=True)
    except (UnicodeError, OSError, ValueError):
        pass
