"""随安装包分发的字体（B 套，都是 SIL Open Font License 1.1）：构建时从官方发布处下载，核对 SHA-256，附许可证全文，不进仓库。

    python -m tools.installer.fonts [目标目录]      # 下载并核对（缓存在 build/cache/fonts/），给了目录就放进去

| 字体 | 文件 | 官方发布 |
|---|---|---|
| 思源宋体（Noto Serif SC 可变字重） | NotoSerifSC-VF.ttf | notofonts/noto-cjk Serif2.003（只按范围取 zip 里这一个文件，不下整包） |
| 朱雀仿宋 | ZhuqueFangsong-Regular.ttf | TrionesType/zhuque v0.212 |
| 霞鹜文楷 GB | LXGWWenKaiGB-Regular.ttf | lxgw/LxgwWenkaiGB v1.530 |

Times New Roman 用系统已装的，不进安装包。放进目标目录的有：三个字体文件、各自的许可证全文（LICENSE-<字体>.txt）、FONTS.txt（名称、版本、来源、许可）。
升级字体：改下面的版本和地址，再填两个 SHA-256（字体文件、许可证）：发布页给了校验值的照抄，没给的（如 zip 里的文件）
取出后自己算一次并核对 zip 内 CRC。只用标准库。
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from zuzhibu.store import REPO

from . import say, utf8_output

CACHE = REPO / "build" / "cache" / "fonts"
UA = {"User-Agent": "zuzhibu-build"}


@dataclass(frozen=True)
class Font:
    key: str
    name: str
    version: str
    file: str  # 放进安装包的文件名
    url: str  # 官方发布的下载地址（zip 时配 member）
    sha256: str  # 字体文件本身的 SHA-256
    license_url: str
    license_sha256: str
    member: str | None = None  # zip 里的路径

    @property
    def license_file(self) -> str:
        return f"LICENSE-{Path(self.file).stem}.txt"


# 霞鹜文楷 GB 的校验值和官方发布页给的一致；朱雀仿宋的整包和官方发布页给的一致，下面是包里那个字体文件的；
# 思源宋体的发布没给校验值，下面是按 zip 内 CRC-32 取出后算的。
FONTS = (
    Font("noto-serif-sc", "思源宋体（Noto Serif SC 可变字重）", "Serif2.003", "NotoSerifSC-VF.ttf",
         "https://github.com/notofonts/noto-cjk/releases/download/Serif2.003/03_NotoSerifCJK-TTF-VF.zip",
         "5326cfb097e3ab26fcb39329752b5c0a439bf8d5c4649520e4b492939c352a09",
         "https://raw.githubusercontent.com/notofonts/noto-cjk/Serif2.003/Serif/LICENSE",
         "6a73f9541c2de74158c0e7cf6b0a58ef774f5a780bf191f2d7ec9cc53efe2bf2",
         member="Variable/TTF/Subset/NotoSerifSC-VF.ttf"),
    Font("zhuque-fangsong", "朱雀仿宋", "v0.212", "ZhuqueFangsong-Regular.ttf",
         "https://github.com/TrionesType/zhuque/releases/download/v0.212/ZhuqueFangsong-v0.212.zip",
         "558c62730844fe54ba220146ed62f859d4e2880188d92d985f8921c6e3743bc4",
         "https://raw.githubusercontent.com/TrionesType/zhuque/v0.212/LICENSE.txt",
         "66cc01ad4df62fca6936431a426104cb32a52ce8da49de9a7d643e02d7659043",
         member="ZhuqueFangsong-Regular.ttf"),
    Font("lxgw-wenkai-gb", "霞鹜文楷 GB", "v1.530", "LXGWWenKaiGB-Regular.ttf",
         "https://github.com/lxgw/LxgwWenkaiGB/releases/download/v1.530/LXGWWenKaiGB-Regular.ttf",
         "5561d48097d92e5ab99fe7b1459d381bbcdff533bfc479207b089c6860c3990e",
         "https://raw.githubusercontent.com/lxgw/LxgwWenkaiGB/v1.530/OFL.txt",
         "a8aa32c38a3402ba4e40fb6d39c913300dd4a0336290dce5c14e3a2f9308d092"),
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class _Remote(io.RawIOBase):
    """只读的远端文件：按 HTTP Range 取需要的那几段（读 zip 目录和其中一个成员时不必下整包）。"""

    def __init__(self, url: str):
        self.origin = url
        self._resolve()
        self.pos = 0

    def _resolve(self) -> None:
        req = urllib.request.Request(self.origin, method="HEAD", headers=UA)
        with urllib.request.urlopen(req, timeout=60) as resp:
            self.url = resp.url  # 发布页的下载会跳到有时效的地址，过期了重取
            self.size = int(resp.headers["Content-Length"])

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self.pos

    def seek(self, offset: int, whence: int = 0) -> int:
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        return self.pos

    def readinto(self, buffer) -> int:
        if self.pos >= self.size:
            return 0
        end = min(self.size, self.pos + len(buffer)) - 1
        for attempt in range(2):
            req = urllib.request.Request(self.url, headers={**UA, "Range": f"bytes={self.pos}-{end}"})
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = resp.read()
                    if resp.status == 200:  # 不认 Range 的服务器给了整个文件
                        data = data[self.pos:end + 1]
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403) and attempt == 0:
                    self._resolve()
                    continue
                raise
        buffer[:len(data)] = data
        self.pos += len(data)
        return len(data)


def _download(url: str, target: Path) -> None:
    fd, part = tempfile.mkstemp(prefix=target.name + ".", suffix=".part", dir=target.parent)
    try:
        req = urllib.request.Request(url, headers=UA)
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(req, timeout=300) as resp:
            shutil.copyfileobj(resp, out)
        os.replace(part, target)
    finally:
        if os.path.exists(part):
            os.unlink(part)


def _extract_member(url: str, member: str, target: Path) -> None:
    fd, part = tempfile.mkstemp(prefix=target.name + ".", suffix=".part", dir=target.parent)
    try:
        with zipfile.ZipFile(io.BufferedReader(_Remote(url), buffer_size=1 << 20)) as z, z.open(member) as src, \
                os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(src, out, 1 << 20)
        os.replace(part, target)
    finally:
        if os.path.exists(part):
            os.unlink(part)


def _cached(target: Path, expected: str, fetch) -> Path:
    if target.is_file() and sha256(target) == expected:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    fetch(target)
    got = sha256(target)
    if got != expected:
        target.unlink()
        raise SystemExit(f"{target.name} 校验不符：{got}，应为 {expected}")
    return target


def fetch(font: Font, cache: Path = CACHE) -> tuple[Path, Path]:
    """下载并核对一款字体和它的许可证，返回 (字体文件, 许可证文件)；缓存里有且对就不重下。"""
    folder = cache / f"{font.key}-{font.version}"
    if font.member:
        f = _cached(folder / font.file, font.sha256, lambda t: _extract_member(font.url, font.member, t))
    else:
        f = _cached(folder / font.file, font.sha256, lambda t: _download(font.url, t))
    lic = _cached(folder / font.license_file, font.license_sha256, lambda t: _download(font.license_url, t))
    return f, lic


def stage(dest: Path, font_list: tuple[Font, ...] = FONTS, cache: Path = CACHE) -> list[Path]:
    """把字体和许可证全文放进 dest，写 FONTS.txt；返回放进去的字体文件。"""
    dest.mkdir(parents=True, exist_ok=True)
    out, lines = [], ["随软件分发的字体。都按 SIL Open Font License 1.1 授权，许可证全文见同目录的 LICENSE-*.txt。", ""]
    for font in font_list:
        f, lic = fetch(font, cache)
        shutil.copy2(f, dest / font.file)
        shutil.copy2(lic, dest / font.license_file)
        out.append(dest / font.file)
        lines += [f"{font.name}", f"  文件：{font.file}（SHA-256 {font.sha256}）", f"  版本：{font.version}",
                  f"  来源：{font.url}" + (f" 中的 {font.member}" if font.member else ""), f"  许可证：{font.license_file}", ""]
    lines.append("Times New Roman 用系统已装的，不随软件分发。")
    (dest / "FONTS.txt").write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    utf8_output()
    args = sys.argv[1:] if argv is None else argv
    for font in FONTS:
        f, lic = fetch(font)
        say(f"{font.name}：{f}（{f.stat().st_size / 1e6:.1f} MB）；许可证 {lic.name}")
    if args:
        stage(Path(args[0]))
        say(f"已放进 {args[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
