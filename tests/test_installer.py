"""安装包构建：图标、装进 pyapp\\ 的文件、嵌入版 Python 的 ._pth、外壳版本号、打包配置和安装脚本补充规则、
随软件分发的字体、装好内容的逐项核对。"""

import hashlib
import http.server
import io
import json
import os
import re
import struct
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import tools.installer as installer
from tools.import_portraits import SIZE as PORTRAIT_SIZE
from tools.installer import build, contents, fonts, icon
from zuzhibu import datasync
from zuzhibu.store import REPO
from zuzhibu.server import VERSION


def jpeg(width: int, height: int) -> bytes:
    """只有文件头的 JPEG：SOI、APP0、SOF0。"""
    app0 = b"JFIF\x00\x01\x02\x00\x00\x01\x00\x01\x00\x00"
    sof = struct.pack(">BHHB", 8, height, width, 3) + b"\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    return (b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", len(app0) + 2) + app0
            + b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof + b"\xff\xd9")


def png(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)


def demo_font(cache: Path, key: str = "demo", body: bytes = b"fake font", lic: bytes = b"SIL OPEN FONT LICENSE Version 1.1"):
    font = fonts.Font(key, "演示字体", "v1", f"{key}-Regular.ttf", f"https://github.com/x/{key}/releases/download/v1/{key}.ttf",
                      hashlib.sha256(body).hexdigest(), f"https://raw.githubusercontent.com/x/{key}/v1/OFL.txt",
                      hashlib.sha256(lic).hexdigest())
    folder = cache / f"{key}-v1"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / font.file).write_bytes(body)
    (folder / font.license_file).write_bytes(lic)
    return font


class _RangeHandler(http.server.BaseHTTPRequestHandler):
    """只认 HEAD 和带 Range 的 GET，记下取过的字节数。"""
    payload = b""
    fetched = 0

    def log_message(self, *args):
        pass

    def do_HEAD(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.payload)))
        self.end_headers()

    def do_GET(self):  # noqa: N802
        m = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range") or "")
        if not m:
            return self.send_error(400, "only ranges")
        chunk = self.payload[int(m[1]):int(m[2]) + 1]
        type(self).fetched += len(chunk)
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {m[1]}-{m[2]}/{len(self.payload)}")
        self.send_header("Content-Length", str(len(chunk)))
        self.end_headers()
        self.wfile.write(chunk)


class IconTest(unittest.TestCase):
    def test_ico_layout(self):
        data = icon.ico((16, 32, 256))
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind, count), (0, 1, 3))
        for i, size in enumerate((16, 32, 256)):
            w, h, _, _, planes, bits, length, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i: 22 + 16 * i])
            self.assertEqual((w, h, planes, bits), (size % 256, size % 256, 1, 32))
            self.assertEqual(data[offset:offset + 8], b"\x89PNG\r\n\x1a\n")
        pixels = icon.render(16)

        def at(row, col):
            return pixels[(row * 16 + col) * 4:(row * 16 + col + 1) * 4]

        self.assertEqual(at(0, 0)[3], 0, "圆角外透明")
        r, g, b, _ = at(8, 2)
        self.assertTrue(r > 150 and g < 60 and b < 60, "底色是标识红")
        self.assertTrue(min(at(8, 8)[:3]) > 200, "正中是白色海雕")
        r, g, b, _ = at(14, 4)
        self.assertTrue(b > r, "下方是蓝带")

    def test_sizes_and_favicon(self):
        self.assertEqual(icon.SIZES, (16, 24, 32, 48, 64, 128, 256))
        favicon = Path(build.REPO, "web", "favicon.svg").read_bytes().decode("utf-8")
        self.assertEqual(favicon, icon.svg(), "web/favicon.svg 要和图标同一个图形：python -m tools.installer.icon web/favicon.svg")

    def test_appx_assets(self):
        """商店包的图标：四种标识各两档缩放，任务栏按实际像素的几种各带一份不加底板的；尺寸照 MSIX 的规定。"""
        assets = icon.appx_assets()
        logos = {"StoreLogo": (50, 50), "Square44x44Logo": (44, 44), "Square150x150Logo": (150, 150), "Wide310x150Logo": (310, 150)}
        for name, (w, h) in logos.items():
            for scale in icon.APPX_SCALES:
                data = assets[f"{name}.scale-{scale}.png"]
                self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
                self.assertEqual(struct.unpack(">II", data[16:24]), (w * scale // 100, h * scale // 100), name)
        for size in icon.APPX_TARGETS:
            for suffix in ("", "_altform-unplated"):
                self.assertEqual(struct.unpack(">II", assets[f"Square44x44Logo.targetsize-{size}{suffix}.png"][16:24]), (size, size))
        self.assertEqual(len(assets), len(logos) * len(icon.APPX_SCALES) + 2 * len(icon.APPX_TARGETS))
        wide = icon.placed(310, 150, 100)

        def alpha(x, y):
            return wide[(y * 310 + x) * 4 + 3]

        self.assertEqual((alpha(0, 0), alpha(60, 75), alpha(250, 75)), (0, 0, 0), "宽磁贴两边露出磁贴底色")
        self.assertEqual(alpha(155, 75), 255, "图标在正中")


class BuildPiecesTest(unittest.TestCase):
    def test_app_files(self):
        files = build.app_files()
        for must in ("zuzhibu/server.py", "zuzhibu/datasync.py", "web/index.html", "web/favicon.svg", "tools/lint/cadre_lint.py",
                     "shared/render/card.css", "bureaus/dispatch/run.py", "bureaus/personnel-sv/roster/roster.csv", "LICENSE"):
            self.assertIn(must, files)
        for rel in files:
            self.assertNotIn("/tests/", "/" + rel)
            self.assertFalse(rel.endswith((".pyc", ".ttf", ".otf", "daily.ps1")), rel)
            self.assertFalse(rel.startswith(("data/", "inbox/", "desktop/", "bureaus/personnel-congress", "bureaus/secretariat",
                                             "shared/samples", "shared/skills")), rel)

    def test_unpack_runtime_rewrites_pth_and_rejects_bad_zip(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "py.zip"
            with zipfile.ZipFile(good, "w") as z:
                z.writestr("python314._pth", "python314.zip\r\n.\r\n#import site\r\n")
                z.writestr("pythonw.exe", b"MZ")
                z.writestr("python.exe", b"MZ")
            self.assertEqual(build.unpack_runtime(good, Path(tmp) / "rt"), "python314")
            pth = (Path(tmp) / "rt" / "python314._pth").read_text(encoding="ascii").splitlines()
            self.assertEqual(pth[:3], ["python314.zip", ".", "..\\pyapp"], "外壳把程序放在 resources\\pyapp")
            self.assertNotIn("import site", [line.strip() for line in pth])
            bad = Path(tmp) / "bad.zip"
            with zipfile.ZipFile(bad, "w") as z:
                z.writestr("../evil.dll", b"x")
            with self.assertRaises(SystemExit):
                build.unpack_runtime(bad, Path(tmp) / "rt2")

    def test_store_identity(self):
        """商店包的身份三项：照合作伙伴中心的写法核对，没填或写错就停下。"""
        self.assertEqual(build.store_identity("YueluTech.Zuzhibu", "CN=12345678-ABCD-1234-ABCD-1234567890AB", " AI政见 "),
                         {"identityName": "YueluTech.Zuzhibu", "publisher": "CN=12345678-ABCD-1234-ABCD-1234567890AB",
                          "publisherDisplayName": "AI政见"})
        for name, publisher, display in (("", "CN=1", "AI政见"), ("YueluTech.Zuzhibu", "", "AI政见"),
                                         ("Yuelu_Tech", "CN=1", "AI政见"), ("ab", "CN=1", "AI政见"),
                                         ("YueluTech.Zuzhibu", "1234", "AI政见"), ("YueluTech.Zuzhibu", "CN=1, O=x", "AI政见"),
                                         ("YueluTech.Zuzhibu", "CN=1", "  ")):
            with self.assertRaises(SystemExit, msg=(name, publisher, display)):
                build.store_identity(name, publisher, display)

    def test_store_command(self):
        """商店包用打安装包时的同一个解包目录（--prepackaged），图标目录和身份三项从命令行传给 electron-builder。"""
        identity = build.store_identity("YueluTech.Zuzhibu", "CN=1234", "AI政见")
        cmd = build.store_command("node", Path("cli.js"), Path("w/win-unpacked"), Path("out"), Path("res"), identity, "1.2.3")
        self.assertEqual(cmd[2:5], ["--win", "appx", "--x64"])
        self.assertEqual(cmd[cmd.index("--prepackaged") + 1], str(Path("w/win-unpacked")))
        for need in ("-c.directories.output=out", "-c.directories.buildResources=res", "-c.extraMetadata.version=1.2.3",
                     "-c.appx.identityName=YueluTech.Zuzhibu", "-c.appx.publisher=CN=1234", "-c.appx.publisherDisplayName=AI政见"):
            self.assertIn(need, cmd)


class ShellPackagingTest(unittest.TestCase):
    def test_versions_and_config(self):
        shell = build.shell_versions()
        self.assertEqual(shell["version"], VERSION)
        self.assertRegex(shell["electron"], r"^\d+\.\d+\.\d+$", "Electron 版本写死，不用范围")
        config = (build.DESKTOP / "electron-builder.yml").read_text(encoding="utf-8")
        for need in ("perMachine: false", "allowElevation: false", "oneClick: true", "deleteAppDataOnUninstall: false",
                     "../build/installer/stage/runtime", "../build/installer/stage/pyapp", "to: pyapp",
                     "artifactName: zuzhibu-${version}-win-x64-setup.${ext}", "include: resources/installer.nsh",
                     "appx:", "displayName: AI政见·组织部", "applicationId: zuzhibu",
                     "artifactName: zuzhibu-${version}-win-x64-store.${ext}"):
            self.assertIn(need, config)
        self.assertNotRegex(config, r"(?m)^\s*(identityName|publisher):", "身份三项由 build.py 传，不写在配置里")
        script = (build.DESKTOP / "resources" / "installer.nsh").read_text(encoding="utf-8")
        self.assertIn("VersionCompare", script, "不许装回旧版")
        self.assertIn("--no-shortcuts", script)

    def test_tool_caches_stay_in_build(self):
        env = build.tool_env()
        for key in ("npm_config_cache", "ELECTRON_CACHE", "electron_config_cache", "ELECTRON_BUILDER_CACHE"):
            self.assertTrue(Path(env[key]).is_relative_to(build.BUILD), key)
        self.assertEqual(env["CSC_IDENTITY_AUTO_DISCOVERY"], "false")
        self.assertTrue(fonts.CACHE.is_relative_to(build.BUILD))


class OutputTest(unittest.TestCase):
    def test_output_survives_gbk_pipe(self):
        """输出被重定向时 Windows 默认按 GBK 写：改成 UTF-8 后 ✓✗ 照常写出，不抛错让脚本半路退出。"""
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="gbk", errors="strict")
        with mock.patch.object(sys, "stdout", stream), mock.patch.object(sys, "stderr", stream):
            installer.utf8_output()
            build.log("  ✓ 通过  ✗ 没通过")
        stream.flush()
        self.assertIn("  ✓ 通过  ✗ 没通过", raw.getvalue().decode("utf-8"))

    def test_say_never_raises(self):
        stream = io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict")
        with mock.patch.object(sys, "stdout", stream):
            installer.say("✓ GBK 写不了这个勾")
        closed = io.StringIO()
        closed.close()
        with mock.patch.object(sys, "stdout", closed):
            installer.say("输出已经关了")


class FontsTest(unittest.TestCase):
    def test_pinned_to_official_releases(self):
        self.assertEqual([f.key for f in fonts.FONTS], ["noto-serif-sc", "zhuque-fangsong", "lxgw-wenkai-gb"])
        self.assertEqual(len({f.file for f in fonts.FONTS}), len(fonts.FONTS))
        for f in fonts.FONTS:
            self.assertRegex(f.sha256, r"^[0-9a-f]{64}$", f.key)
            self.assertRegex(f.license_sha256, r"^[0-9a-f]{64}$", f.key)
            self.assertTrue(f.url.startswith("https://github.com/") and f"/releases/download/{f.version}/" in f.url, f.url)
            self.assertTrue(f.license_url.startswith("https://raw.githubusercontent.com/") and f"/{f.version}/" in f.license_url)
            self.assertTrue(f.file.endswith(".ttf") and "times" not in f.file.lower(), "Times New Roman 不随软件分发")

    def test_stage_copies_fonts_and_full_licenses(self):
        with tempfile.TemporaryDirectory() as tmp:
            font = demo_font(Path(tmp) / "cache")
            out = fonts.stage(Path(tmp) / "dest", (font,), Path(tmp) / "cache")
            dest = Path(tmp) / "dest"
            self.assertEqual(out, [dest / "demo-Regular.ttf"])
            self.assertEqual((dest / "LICENSE-demo-Regular.txt").read_bytes(), b"SIL OPEN FONT LICENSE Version 1.1")
            notes = (dest / "FONTS.txt").read_text(encoding="utf-8")
            for need in ("演示字体", "v1", font.sha256, "LICENSE-demo-Regular.txt", "Times New Roman"):
                self.assertIn(need, notes)

    def test_wrong_download_is_refused_and_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "x.ttf"
            with self.assertRaises(SystemExit):
                fonts._cached(target, "0" * 64, lambda t: t.write_bytes(b"tampered"))
            self.assertFalse(target.exists())

    def test_extract_member_reads_only_the_ranges_it_needs(self):
        member = os.urandom(100_000)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("big.bin", os.urandom(4_000_000), compress_type=zipfile.ZIP_STORED)
            z.writestr("Variable/TTF/Demo.ttf", member, compress_type=zipfile.ZIP_DEFLATED)
        _RangeHandler.payload, _RangeHandler.fetched = buf.getvalue(), 0
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"no_proxy": "*"}):
                target = Path(tmp) / "Demo.ttf"
                fonts._extract_member(f"http://127.0.0.1:{server.server_port}/fonts.zip", "Variable/TTF/Demo.ttf", target)
                self.assertEqual(target.read_bytes(), member)
                self.assertEqual([p.name for p in Path(tmp).iterdir()], ["Demo.ttf"], "不留半成品")
        finally:
            server.shutdown()
            server.server_close()
        self.assertLess(_RangeHandler.fetched, len(_RangeHandler.payload) // 4, "只取 zip 目录和这一个文件")


class ContentsTest(unittest.TestCase):
    def test_image_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name, data in (("a.jpg", jpeg(600, 800)), ("b.png", png(600, 800)), ("c.jpg", b"not an image")):
                (Path(tmp) / name).write_bytes(data)
            self.assertEqual(contents.image_size(Path(tmp) / "a.jpg"), (600, 800))
            self.assertEqual(contents.image_size(Path(tmp) / "b.png"), (600, 800))
            self.assertIsNone(contents.image_size(Path(tmp) / "c.jpg"))
        sizes = {contents.image_size(p) for p in (REPO / "data" / "portraits").glob("*.jpg")}
        self.assertTrue(sizes <= {PORTRAIT_SIZE, contents.LEGACY_SIZE},
                        f"仓库里的标准像只能是 tools/import_portraits.py 规定的尺寸，或名单外人物的旧照尺寸：{sizes}")

    def test_locate(self):
        with tempfile.TemporaryDirectory() as tmp:
            for root in (Path(tmp) / "inst" / "resources", Path(tmp) / "stage", Path(tmp) / "appx" / "app" / "resources"):
                (root / "pyapp").mkdir(parents=True)
                (root / "pyapp" / "dist.json").write_text("{}", encoding="utf-8")
            self.assertEqual(contents.locate(Path(tmp) / "inst"), Path(tmp) / "inst" / "resources" / "pyapp")
            self.assertEqual(contents.locate(Path(tmp) / "stage"), Path(tmp) / "stage" / "pyapp")
            self.assertEqual(contents.locate(Path(tmp) / "appx"), Path(tmp) / "appx" / "app" / "resources" / "pyapp")
            with self.assertRaises(SystemExit):
                contents.locate(Path(tmp))

    def test_store_package_names(self):
        """商店包里的文件名按网址写法存，解开时还原成原名；越出包外的名字不解。"""
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "a.appx"
            with zipfile.ZipFile(pkg, "w") as z:
                z.writestr("AppxManifest.xml", "<x/>")
                z.writestr("app/resources/pyapp/shared/glossary/%E8%AF%91%E5%90%8D%E8%A1%A8.csv", "a,b\n")
                z.writestr("app/%E7%A9%BA%20%E6%A0%BC.txt", "x")
            names = contents.extract_package(pkg, Path(tmp) / "out")
            self.assertIn("app/resources/pyapp/shared/glossary/译名表.csv", names)
            self.assertTrue((Path(tmp) / "out" / "app" / "resources" / "pyapp" / "shared" / "glossary" / "译名表.csv").is_file())
            self.assertTrue((Path(tmp) / "out" / "app" / "空 格.txt").is_file())
            evil = Path(tmp) / "evil.appx"
            with zipfile.ZipFile(evil, "w") as z:
                z.writestr("app/..%2F..%2Fevil.dll", b"x")
            with self.assertRaises(SystemExit):
                contents.extract_package(evil, Path(tmp) / "out2")
            self.assertFalse((Path(tmp) / "evil.dll").exists())

    def test_store_manifest(self):
        """商店包的清单：身份和给定的一致、版本第四段是 0、显示名和语言、只要完全信任一项权限、程序和图标都在包里。"""
        identity = {"identityName": "YueluTech.Zuzhibu", "publisher": "CN=1234", "publisherDisplayName": "AI政见"}
        manifest = f"""<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10"
  xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10"
  xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities">
  <Identity Name="YueluTech.Zuzhibu" ProcessorArchitecture="x64" Publisher='CN=1234' Version="{VERSION}.0" />
  <Properties><DisplayName>AI政见·组织部</DisplayName><PublisherDisplayName>AI政见</PublisherDisplayName>
    <Description>AI政见·组织部 桌面外壳</Description><Logo>assets\\StoreLogo.png</Logo></Properties>
  <Resources><Resource Language="zh-CN" /></Resources>
  <Dependencies><TargetDeviceFamily Name="Windows.Desktop" MinVersion="10.0.17763.0" MaxVersionTested="10.0.26100.0" /></Dependencies>
  <Capabilities><rescap:Capability Name="runFullTrust" /></Capabilities>
  <Applications><Application Id="zuzhibu" Executable="app\\AI政见·组织部.exe" EntryPoint="Windows.FullTrustApplication">
    <uap:VisualElements BackgroundColor="#B8141C" DisplayName="AI政见·组织部" Square150x150Logo="assets\\Square150x150Logo.png"
      Square44x44Logo="assets\\Square44x44Logo.png" Description="AI政见·组织部 桌面外壳">
      <uap:DefaultTile Wide310x150Logo="assets\\Wide310x150Logo.png" />
    </uap:VisualElements></Application></Applications>
</Package>"""
        names = ["AppxManifest.xml", "AppxBlockMap.xml", "[Content_Types].xml", "resources.pri", "app/AI政见·组织部.exe",
                 "assets/StoreLogo.scale-100.png", "assets/Square150x150Logo.scale-200.png",
                 "assets/Square44x44Logo.targetsize-16_altform-unplated.png", "assets/Wide310x150Logo.scale-100.png"]

        def passes(text=manifest, files=names, ident=identity):
            return all(ok is not False for ok, _ in contents.check_store_manifest(text, files, ident))

        self.assertTrue(passes(), contents.check_store_manifest(manifest, names, identity))
        self.assertIn("包没有签名", contents.check_store_manifest(manifest, names, identity)[-1][1])
        self.assertFalse(passes(text=manifest.replace(f'Version="{VERSION}.0"', f'Version="{VERSION}.1"')), "第四段留给商店")
        self.assertFalse(passes(text=manifest.replace('Language="zh-CN"', 'Language="en-US"')))
        self.assertFalse(passes(text=manifest.replace("</Capabilities>", '<Capability Name="internetClient" /></Capabilities>')))
        self.assertFalse(passes(text=manifest.replace("<DisplayName>AI政见·组织部</DisplayName>", "<DisplayName>组织部</DisplayName>")))
        self.assertFalse(passes(ident={**identity, "publisher": "CN=9999"}), "和合作伙伴中心的不一致")
        self.assertFalse(passes(files=[n for n in names if "Wide310x150Logo" not in n]), "缺宽磁贴图标")
        self.assertFalse(passes(files=[n for n in names if not n.endswith(".exe")]), "包里没有程序")
        self.assertFalse(passes(files=[n for n in names if n != "AppxBlockMap.xml"]))

    def test_program_and_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, pyapp = Path(tmp) / "repo", Path(tmp) / "pyapp"
            for root in (repo, pyapp):
                (root / "web").mkdir(parents=True)
                (root / "web" / "index.html").write_text("<!doctype html>", encoding="utf-8")
            (pyapp / "dist.json").write_text(json.dumps({"name": "x", "version": "1"}), encoding="utf-8")
            people = pyapp / "data" / "people"
            people.mkdir(parents=True)
            (people / "a.json").write_text('{"id": "a"}', encoding="utf-8")
            (pyapp / "data" / "meta.json").write_text('{"updated_at": "2026-10-21"}', encoding="utf-8")
            (pyapp / "data" / "manifest.json").write_text(datasync.dumps_manifest(datasync.build_manifest(pyapp / "data")),
                                                          encoding="utf-8")
            self.assertTrue(all(ok is not False for ok, _ in contents.check_program(pyapp, repo, ["web/index.html"])))
            self.assertTrue(all(ok is not False for ok, _ in contents.check_data(pyapp)))
            (repo / "web" / "index.html").write_text("<!doctype html><p>新界面</p>", encoding="utf-8")
            (people / "a.json").write_text('{"id": "b"}', encoding="utf-8")
            self.assertIn(False, [ok for ok, _ in contents.check_program(pyapp, repo, ["web/index.html"])])
            self.assertIn(False, [ok for ok, _ in contents.check_data(pyapp)])

    def test_portraits(self):
        with tempfile.TemporaryDirectory() as tmp:
            pyapp = Path(tmp)
            folder = pyapp / "data" / "portraits"
            folder.mkdir(parents=True)
            (pyapp / "data" / "rosters").mkdir()
            (pyapp / "data" / "rosters" / "a.json").write_text(json.dumps(
                {"id": "a", "groups": [{"members": [{"person": "p1"}, {"person": "p3"}]}]}), encoding="utf-8")
            for pid in ("p1", "p2"):
                (folder / f"{pid}.jpg").write_bytes(jpeg(600, 800))
            (folder / "index.csv").write_text("person_id,file\np1,p1.jpg\np2,p2.jpg\n", encoding="utf-8")
            items = contents.check_portraits(pyapp, (600, 800))
            self.assertEqual([ok for ok, _ in items], [True, True, None])
            self.assertIn("名单 2 人，有标准像 1 人，没有的：p3", items[2][1])
            (folder / "p2.jpg").write_bytes(jpeg(300, 400))   # 名单外人物的旧照：允许
            items = contents.check_portraits(pyapp, (600, 800))
            self.assertTrue(items[0][0], items[0][1])
            (folder / "p1.jpg").write_bytes(jpeg(300, 400))   # 名单人物不是 600×800：不行
            (folder / "p4.jpg").write_bytes(jpeg(600, 800))
            items = contents.check_portraits(pyapp, (600, 800))
            self.assertEqual([ok for ok, _ in items][:2], [False, False])
            self.assertIn("300×400 2 张", items[0][1])
            self.assertIn("不符的有 p1.jpg", items[0][1])
            self.assertIn("有照片没登记的 p4.jpg", items[1][1])

    def test_fonts_and_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            top = Path(tmp) / "inst"
            pyapp = top / "resources" / "pyapp"
            font = demo_font(Path(tmp) / "cache")
            fonts.stage(pyapp / "fonts", (font,), Path(tmp) / "cache")
            (pyapp / "zuzhibu").mkdir(parents=True)
            (pyapp / "zuzhibu" / "x.py").write_text("print('ok')\n", encoding="utf-8")
            self.assertTrue(all(ok for ok, _ in contents.check_fonts(top, pyapp, (font,), "fonts")))
            self.assertTrue(all(ok for ok, _ in contents.check_secrets(top, pyapp)))
            (top / "locales").mkdir()
            (top / "locales" / "times.ttf").write_bytes(b"x")
            (pyapp / "fonts" / font.file).write_bytes(b"changed")
            self.assertEqual([ok for ok, _ in contents.check_fonts(top, pyapp, (font,), "fonts")], [False, True, False])
            (pyapp / "auth.json").write_text("{}", encoding="utf-8")
            (pyapp / "zuzhibu" / "y.py").write_text("KEY = '" + "sk-" + "A1b2" * 10 + "'\n", encoding="utf-8")
            self.assertEqual([ok for ok, _ in contents.check_secrets(top, pyapp)], [False, False])

    def test_banners(self):
        self.assertEqual([ok for ok, _ in contents.check_banners(REPO)], [True, True], "仓库里的人事任免横图是 750×230 和两倍图")
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "web" / "img").mkdir(parents=True)
            (Path(tmp) / "web" / "img" / "renmian.png").write_bytes(png(750, 230))
            (Path(tmp) / "web" / "img" / "renmian@2x.png").write_bytes(png(1000, 300))
            items = contents.check_banners(Path(tmp))
            self.assertEqual([ok for ok, _ in items], [True, False])
            self.assertIn("应为 1500×460：1000×300", items[1][1])


if __name__ == "__main__":
    unittest.main()
