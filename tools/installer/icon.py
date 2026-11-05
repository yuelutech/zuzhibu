"""软件图标：和标题栏同一种红色作底，一只白色海雕双翼上扬、羽尖分开，鹰头朝右、嘴带钩，下方蓝带上三颗白星。
海雕是自己画的剪影，不带国徽上的盾牌、箭束、橄榄枝和绶带。按几何形状直接画，只用标准库；
web/favicon.svg 是同一个图形，由 svg() 写出。

    python -m tools.installer.icon 输出.ico          16、24、32、48、64、128、256 七种尺寸装进一个 .ico
    python -m tools.installer.icon 输出.png [边长]   单张 PNG，边长默认 256
    python -m tools.installer.icon 输出.svg          SVG，即 web/favicon.svg

商店包（MSIX）要的开始菜单、任务栏、磁贴图标由 appx_assets() 给出，build.py --store 时写进 electron-builder 的 appx 目录。
"""

from __future__ import annotations

import math
import re
import struct
import sys
import zlib
from pathlib import Path

SIZES = (16, 24, 32, 48, 64, 128, 256)
APPX_SCALES = (100, 200)  # 商店包图标的缩放档：100% 和 200%
APPX_TARGETS = (16, 24, 32, 48, 256)  # 任务栏、开始菜单按实际像素取的尺寸（targetsize）

RED_TOP, RED_BOTTOM = (0xD4, 0x20, 0x27), (0xA5, 0x0F, 0x16)  # 底色从上到下渐深，中段和标题栏的红相近
NAVY = (0x1F, 0x2F, 0x66)
WHITE = (0xFF, 0xFF, 0xFF)
GOLD = (0xF2, 0xB3, 0x3D)

# 图形按 256×256 的坐标画（与 favicon.svg 一致）：圆角方块外留 8 的透明边
FRAME = (8, 8, 248, 248, 54)
BAND = 206  # 蓝带上沿
STAR = ((0, -11), (3.2, -3.5), (11, -3.4), (4.9, 1.6), (7.1, 9.3), (0, 4.8), (-7.1, 9.3), (-4.9, 1.6), (-11, -3.4), (-3.2, -3.5))
STARS = ((88, 226), (128, 226), (168, 226))
EAGLE = (
    "M112 108 C98 96 86 84 72 70 L30 30 L48 52 L24 44 L44 64 L22 58 L42 76 L24 72 L44 88 L30 86 L54 100 L50 106 L70 112 L68 118 L88 122 L88 130 L112 132 Z",  # 左翼
    "M144 108 C158 96 170 84 184 70 L226 30 L208 52 L232 44 L212 64 L234 58 L214 76 L232 72 L212 88 L226 86 L202 100 L206 106 L186 112 L188 118 L168 122 L168 130 L144 132 Z",  # 右翼
    "M112 104 C106 128 108 158 118 178 L138 178 C148 158 150 128 144 104 Z",  # 身
    "M116 172 L140 172 L154 202 L143 197 L139 212 L128 199 L117 212 L113 197 L102 202 Z",  # 尾羽
    "M114 106 C110 92 116 78 128 72 C140 66 156 70 162 80 L174 84 C170 88 164 90 156 90 C152 96 146 102 140 106 Z",  # 头，朝右
)
BEAK = "M162 80 C170 78 178 82 180 88 C180 92 178 95 175 96 C174 92 170 90 164 90 Z"
EYE = (148, 80, 3.2)


def rounded_rect(x0: float, y0: float, x1: float, y1: float, r: float, steps: int = 12) -> list[tuple[float, float]]:
    pts = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0), (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for i in range(steps + 1):
            a = math.radians(a0 + 90 * i / steps)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def circle(cx: float, cy: float, r: float, steps: int = 24) -> list[tuple[float, float]]:
    return [(cx + r * math.cos(2 * math.pi * i / steps), cy + r * math.sin(2 * math.pi * i / steps)) for i in range(steps)]


def outline(d: str, steps: int = 16) -> list[tuple[float, float]]:
    """把只有 M、L、C、Z（绝对坐标、一笔画完）的路径展开成折线。"""
    pts: list[tuple[float, float]] = []
    nums: list[float] = []
    cmd = ""
    for tok in re.findall(r"[MLCZ]|-?\d+(?:\.\d+)?", d):
        if tok in ("M", "L", "C", "Z"):
            cmd = tok
            continue
        nums.append(float(tok))
        if cmd in ("M", "L") and len(nums) == 2:
            pts.append((nums[0], nums[1]))
            nums = []
        elif cmd == "C" and len(nums) == 6:
            (x0, y0), (x1, y1, x2, y2, x3, y3) = pts[-1], nums
            for i in range(1, steps + 1):
                t = i / steps
                u = 1 - t
                pts.append((u * u * u * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x3,
                            u * u * u * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y3))
            nums = []
    return pts


def coverage(polys: list[list[tuple[float, float]]], size: int, ss: int) -> list[list[float]]:
    """按奇偶规则填充，每个像素取 ss×ss 个采样点，返回 0–1 的覆盖率。"""
    scale = size / 256.0
    edges = []
    for poly in polys:
        for i, (x0, y0) in enumerate(poly):
            x1, y1 = poly[(i + 1) % len(poly)]
            if y0 != y1:
                edges.append((x0 * scale, y0 * scale, x1 * scale, y1 * scale))
    grid = [[0] * size for _ in range(size)]
    sub = size * ss
    for j in range(sub):
        y = (j + 0.5) / ss
        xs = sorted(x0 + (y - y0) * (x1 - x0) / (y1 - y0) for x0, y0, x1, y1 in edges if (y0 <= y < y1) or (y1 <= y < y0))
        row = grid[j // ss]
        for a, b in zip(xs[0::2], xs[1::2]):
            start = max(0, int(a * ss + 0.5))
            end = min(sub, int(b * ss + 0.5))
            if start >= end:
                continue
            p0, p1 = start // ss, (end - 1) // ss
            if p0 == p1:
                row[p0] += end - start
                continue
            row[p0] += (p0 + 1) * ss - start
            for p in range(p0 + 1, p1):
                row[p] += ss
            row[p1] += end - p1 * ss
    total = ss * ss
    return [[v / total for v in row] for row in grid]


def render(size: int) -> bytes:
    """RGBA 像素，逐行。各层按 SVG 的先后顺序叠上去。"""
    ss = 8 if size <= 32 else 4
    frame = coverage([rounded_rect(*FRAME)], size, ss)
    band = coverage([[(0, BAND), (256, BAND), (256, 256), (0, 256)]], size, ss)

    def red(y: int) -> tuple[float, ...]:
        t = (y + 0.5) / size
        return tuple(a + (b - a) * t for a, b in zip(RED_TOP, RED_BOTTOM))

    layers = [
        (frame, red),
        ([[b * f for b, f in zip(rb, rf)] for rb, rf in zip(band, frame)], NAVY),
        (coverage([[(cx + x, cy + y) for x, y in STAR] for cx, cy in STARS], size, ss), WHITE),
        *((coverage([outline(d)], size, ss), WHITE) for d in EAGLE),
        (coverage([outline(BEAK)], size, ss), GOLD),
        (coverage([circle(*EYE)], size, ss), RED_BOTTOM),
    ]
    pix = [[0.0, 0.0, 0.0, 0.0] for _ in range(size * size)]  # 预乘透明度
    for cov, color in layers:
        for y in range(size):
            c = color(y) if callable(color) else color
            for x, a in enumerate(cov[y]):
                if a:
                    p, k = pix[y * size + x], 1 - a
                    p[0], p[1], p[2], p[3] = c[0] * a + p[0] * k, c[1] * a + p[1] * k, c[2] * a + p[2] * k, a + p[3] * k
    out = bytearray()
    for r, g, b, a in pix:
        if a <= 0:
            out += b"\0\0\0\0"
            continue
        out += bytes((min(255, round(r / a)), min(255, round(g / a)), min(255, round(b / a)), round(a * 255)))
    return bytes(out)


def svg() -> str:
    """同一个图形的 SVG，即 web/favicon.svg。"""
    def hexc(c: tuple[int, int, int]) -> str:
        return "#%02x%02x%02x" % c

    def num(v: float) -> str:
        return f"{round(v, 2):g}"

    x0, y0, x1, y1, r = FRAME
    stars = " ".join("M" + " L".join(f"{num(cx + x)} {num(cy + y)}" for x, y in STAR) + " Z" for cx, cy in STARS)
    lines = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">',
        f'<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{hexc(RED_TOP)}"/>'
        f'<stop offset="1" stop-color="{hexc(RED_BOTTOM)}"/></linearGradient>'
        f'<clipPath id="frame"><rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" rx="{r}"/></clipPath></defs>',
        '<g clip-path="url(#frame)">',
        '<rect width="256" height="256" fill="url(#bg)"/>',
        f'<rect y="{BAND}" width="256" height="{256 - BAND}" fill="{hexc(NAVY)}"/>',
        f'<path fill="#fff" d="{stars}"/>',
        *(f'<path fill="#fff" d="{d}"/>' for d in EAGLE),
        f'<path fill="{hexc(GOLD)}" d="{BEAK}"/>',
        f'<circle cx="{EYE[0]}" cy="{EYE[1]}" r="{EYE[2]}" fill="{hexc(RED_BOTTOM)}"/>',
        "</g></svg>",
    ]
    return "\n".join(lines) + "\n"


def png(size: int, rgba: bytes, height: int | None = None) -> bytes:
    """边长 size 的正方形 PNG；给了 height 就是宽 size、高 height。"""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    height = size if height is None else height
    stride = size * 4
    raw = b"".join(b"\0" + rgba[i * stride:(i + 1) * stride] for i in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def placed(width: int, height: int, side: int) -> bytes:
    """宽 width、高 height 的透明底，正中放边长 side 的图标。"""
    icon_px, out = render(side), bytearray(width * height * 4)
    x0, y0 = (width - side) // 2, (height - side) // 2
    for y in range(side):
        start = ((y0 + y) * width + x0) * 4
        out[start:start + side * 4] = icon_px[y * side * 4:(y + 1) * side * 4]
    return bytes(out)


def appx_assets() -> dict[str, bytes]:
    """商店包的图标，文件名照 MSIX 的资源限定写法：商店和开始菜单的小图标铺满；方磁贴、宽磁贴的图标占高度的三分之二，
    四周露出磁贴底色（electron-builder.yml 的 appx.backgroundColor）；任务栏按实际像素取 targetsize，带不加底板的一份。"""
    out: dict[str, bytes] = {}
    for scale in APPX_SCALES:
        k = scale // 100
        out[f"StoreLogo.scale-{scale}.png"] = png(50 * k, render(50 * k))
        out[f"Square44x44Logo.scale-{scale}.png"] = png(44 * k, render(44 * k))
        out[f"Square150x150Logo.scale-{scale}.png"] = png(150 * k, placed(150 * k, 150 * k, 100 * k))
        out[f"Wide310x150Logo.scale-{scale}.png"] = png(310 * k, placed(310 * k, 150 * k, 100 * k), 150 * k)
    for size in APPX_TARGETS:
        data = png(size, render(size))
        out[f"Square44x44Logo.targetsize-{size}.png"] = data
        out[f"Square44x44Logo.targetsize-{size}_altform-unplated.png"] = data
    return out


def ico(sizes: tuple[int, ...] = SIZES) -> bytes:
    images = [png(s, render(s)) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for s, data in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return head + entries + b"".join(images)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    out = Path(args[0]) if args else None
    kind = out.suffix.lower() if out else ""
    if kind == ".ico" and len(args) == 1:
        data = ico()
    elif kind == ".svg" and len(args) == 1:
        data = svg().encode("utf-8")
    elif kind == ".png" and len(args) in (1, 2) and (len(args) == 1 or args[1].isdigit() and 16 <= int(args[1]) <= 1024):
        size = int(args[1]) if len(args) == 2 else 256
        data = png(size, render(size))
    else:
        print(__doc__)
        return 2
    out.write_bytes(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
