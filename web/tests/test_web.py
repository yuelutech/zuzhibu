"""网页静态检查（第二版界面）：index.html 引用的文件都在、路由都有页面、没有外部地址、没有字体文件和第三方 App 名称；
设置、关于的文字照维护者审定的文案稿；戏仿说明只在关于页；界面不写分级、履历、导出一类字样；“人事任免”横图 750×230。

接口在 tests/test_server.py 里测；浏览器层面的验收（各页截图、字体是否用上）在本机的桌面外壳里做。
"""

import json
import os
import re
import shutil
import struct
import subprocess
import sys
import unittest

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(WEB_DIR))

from tools.guard import BANNED_NAMES  # noqa: E402  第三方 App 名称，base64 存，仓库里不出现明文


def read(rel):
    with open(os.path.join(WEB_DIR, rel), encoding="utf-8") as fh:
        return fh.read()


def web_files():
    for root, _dirs, files in os.walk(WEB_DIR):
        if os.path.basename(root) == "tests":
            continue
        for name in files:
            yield os.path.relpath(os.path.join(root, name), WEB_DIR).replace(os.sep, "/")


def sources():
    return [f for f in web_files() if f.endswith((".js", ".css", ".html"))]


class StaticFilesTest(unittest.TestCase):
    def test_index_references_exist(self):
        html = read("index.html")
        refs = re.findall(r'(?:src|href)="((?:js|css|img)/[^"]+)"', html)
        self.assertGreaterEqual(len(refs), 11)
        for ref in refs:
            self.assertTrue(os.path.isfile(os.path.join(WEB_DIR, ref)), ref)

    def test_routes_have_pages(self):
        app = read("js/app.js")
        defined = "".join(read(f) for f in web_files() if f.startswith("js/pages/"))
        pages = set(re.findall(r"ZZB\.pages\.(\w+)", app))
        self.assertEqual(pages, {"home", "person", "article", "news", "roster"})
        for page in pages:
            self.assertIn(f"ZZB.pages.{page} = function", defined, page)
        for route in ("'p'", "'news'"):
            self.assertIn(route, app)

    def test_no_external_requests_in_sources(self):
        for rel in sources():
            text = read(rel)
            self.assertNotRegex(text, r"(?:src|href)=[\"']https?://", rel)
            self.assertNotRegex(text, r"@import|url\([\"']?https?:", rel)

    def test_no_fonts_and_no_third_party_names(self):
        for rel in web_files():
            self.assertFalse(rel.lower().endswith((".ttf", ".otf", ".ttc", ".woff", ".woff2")), rel)
            if rel.endswith((".js", ".css", ".html", ".md")):
                text = read(rel)
                for word in BANNED_NAMES:
                    self.assertNotIn(word, text, rel)

    def test_fonts_are_served_not_bundled(self):
        css = read("css/app.css")
        for name in ("NotoSerifSC-VF.ttf", "ZhuqueFangsong-Regular.ttf", "LXGWWenKaiGB-Regular.ttf"):
            self.assertIn(f'url("../fonts/{name}")', css)
        self.assertIn('local("Times New Roman")', css)
        self.assertFalse(os.path.isdir(os.path.join(WEB_DIR, "fonts")), "字体不进仓库，由本地服务从字体目录供给")

    def test_settings_and_about_follow_the_approved_copy(self):
        settings = read("js/settings.js")
        for text in ("填写本人的大模型接口后，可随时更新人事动态，所用额度由本人账户承担。不填写，也可查看每月更新的资料。",
                     "OpenAI 兼容接口", "Anthropic 接口", "接口类型", "接口地址", "模型名称", "只保存在本机", "'保存'", "'检测接口'",
                     "立即更新", "停止更新", "正在连接接口……", "正在查询公开信息……", "正在深度思考……", "正在逐条核实……", "正在获取简历……",
                     "正在更新简历……", "接口可用，支持联网检索。", "接口不可用：密钥无效或无权限，请核对后重试。"):
            self.assertIn(text, settings)
        about = read("js/about.js")
        for text in ("Yuelu Tech“AI政见”推出的开源桌面软件", "本软件为戏仿项目。“组织部”等名称和干部简历体例仅为借用。",
                     "本软件不收集任何个人信息。", "知识共享署名—相同方式共享 4.0 国际许可协议（CC BY-SA 4.0）", "github.com/yuelutech/zuzhibu",
                     "许可全文", "照片署名", "美国白宫", "美国国会", "美国参议院", "美国众议院", "美国最高法院", "美国国防部", "美国国务院",
                     "美国联邦储备委员会", "美国证券交易委员会", "联邦公报", "本项目为戏仿项目，与任何党政机关无关。"):
            self.assertIn(text, about)
        self.assertNotRegex(about, r"20\d\d年\d+月", "关于不写死日期")

    def test_cover_page(self):
        """封面：图标、名称、副题，六个栏目的入口，最近几条人事动态；打开软件先到封面，标题栏的图标和名字点了回封面。"""
        home = read("js/pages/home.js")
        for must in ("favicon.svg", "'AI政见', h('i', null, '·'), '组织部'", "美国政要干部简历资料库", "d.cfg.sections", "'#/news'"):
            self.assertIn(must, home)
        self.assertIn('<a class="brand" href="#/"', read("index.html"))
        self.assertIn("if (!r.route) return { key: 'home', page: ZZB.pages.home", read("js/app.js"))

    def test_dates_year_month_day(self):
        """日期按 年-月-日 写，数据只到月的写到月、不补日（ui.ymd）；人事动态列表、单篇和封面都用它。"""
        for rel in ("js/pages/news.js", "js/pages/home.js"):
            self.assertIn("ui.ymd(a.date)", read(rel), rel)
            self.assertNotIn("date_cn", read(rel), rel)
        node = shutil.which("node")
        if not node:
            self.skipTest("没有 Node.js，不跑 ui.ymd 的实际结果")
        script = ("const vm=require('vm'),fs=require('fs');const ctx={window:{matchMedia:()=>({matches:false})}};vm.createContext(ctx);"
                  "vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);const f=ctx.window.ZZB.ui.ymd;"
                  "console.log(JSON.stringify(['2026-10-14','2026-08','2026-8-7','2026',''].map(f)));")
        out = subprocess.run([node, "-e", script, os.path.join(WEB_DIR, "js", "ui.js")], capture_output=True, text=True,
                             encoding="utf-8", check=True).stdout
        self.assertEqual(json.loads(out), ["2026-10-14", "2026-08", "2026-08-07", "2026", ""])

    def test_about_qr_codes(self):
        """关于页两个二维码（短视频账号、图文账号）：维护者给的图放 img/，文件名不带平台名；说明只写“扫码关注”，不写平台名称。"""
        about = read("js/about.js")
        for name in ("img/qr-video.png", "img/qr-article.png"):
            self.assertIn(name, about)
            with open(os.path.join(WEB_DIR, name), "rb") as fh:
                head = fh.read(24)
            self.assertEqual(head[:8], b"\x89PNG\r\n\x1a\n", name)
            width, height = struct.unpack(">II", head[16:24])
            self.assertEqual(width, height, name)
            self.assertGreaterEqual(width, 400, f"{name} 要用维护者给的图，不用缩小的图")
        for text in ("短视频　扫码关注 AI政见", "图文　扫码关注 AI政见"):
            self.assertIn(text, about)

    def test_congress_filter_bar(self):
        """国会页筛选条：一个搜索框（姓名、职务），身份、党派、州三个下拉，筛出来写“共 n 人”。"""
        roster = read("js/pages/roster.js")
        for must in ("搜索姓名、职务", "全部身份", "全部党派", "全部州", "清除筛选", "没有符合条件的议员",
                     "['参议员', '众议员', '无表决权代表']", "['共和党', '民主党', '无党派']", "'共', E.countN, '人'"):
            self.assertIn(must, roster)

    def test_congress_dropdowns_do_not_reuse_layout_classes(self):
        """身份、党派面板曾用类名 list，撞上名单页“只列姓名”版式的 .list（三列网格），面板被撑宽、排成三列、伸出窗口。"""
        css, roster = read("css/app.css"), read("js/pages/roster.js")
        self.assertNotRegex(css, r"\.pick-pop\.(list|wide)\b")
        for cls in ("pick-col", "pick-wide"):
            self.assertIn(f".pick-pop.{cls}", css)
            self.assertIn(f"'{cls}'", roster)
        self.assertIn("fit(pop, wide)", roster, "打开时把面板放进窗口")

    def test_congress_filters_count_people_and_cover_leaders(self):
        """按人筛：领导的身份、党派、州取他在议员名单里的那一条；参议院议长不在议员名单里，只在没选身份、党派、州时出现，搜姓名能搜到。"""
        node = shutil.which("node")
        if not node:
            self.skipTest("没有 Node.js，不跑筛选的实际结果")
        groups = [
            {"title": "参议院领导", "filter": False, "members": [
                {"id": "vance", "name": "詹姆斯·戴维·万斯", "name_en": "JD Vance", "post": "参议院议长"},
                {"id": "thune", "name": "约翰·图恩", "name_en": "John Thune", "post": "参议院多数党领袖", "party": "共和党"}]},
            {"title": "参议员", "filter": True, "members": [
                {"id": "thune", "name": "约翰·图恩", "name_en": "John Thune", "post": "南达科他州", "party": "共和党", "state": "南达科他州"},
                {"id": "king", "name": "安格斯·金", "name_en": "Angus King", "post": "缅因州", "party": "无党派", "state": "缅因州"}]},
            {"title": "众议员", "filter": True, "members": [
                {"id": "norton", "name": "埃莉诺·霍姆斯·诺顿", "name_en": "Eleanor Holmes Norton", "post": "哥伦比亚特区代表（无表决权）",
                 "party": "民主党", "state": "哥伦比亚特区"},
                {"id": "moore", "name": "巴里·穆尔", "name_en": "Barry Moore", "post": "阿拉巴马州第1选区", "party": "共和党",
                 "state": "阿拉巴马州", "district": 1}]}]
        cases = [{}, {"party": "共和党"}, {"party": "无党派"}, {"seat": "参议员"}, {"seat": "无表决权代表"}, {"seat": "众议员"},
                 {"state": "南达科他州"}, {"q": "万斯"}, {"q": "vance"}, {"q": "多数党领袖"}, {"seat": "参议员", "party": "共和党"}]
        script = ("const vm=require('vm'),fs=require('fs');const ctx={window:{ZZB:{ui:{h:function(){}},api:{}},matchMedia:()=>({matches:false})}};"
                  "vm.createContext(ctx);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),ctx);const C=ctx.window.ZZB.congress;"
                  "const groups=JSON.parse(process.argv[2]),cases=JSON.parse(process.argv[3]);const who=C.people(groups);"
                  "console.log(JSON.stringify(cases.map(c=>{const f=Object.assign({q:'',seat:'',party:'',state:''},c);"
                  "return Object.keys(who).filter(id=>C.matches(who[id],f));})));")
        out = subprocess.run([node, "-e", script, os.path.join(WEB_DIR, "js", "pages", "roster.js"), json.dumps(groups, ensure_ascii=False),
                              json.dumps(cases, ensure_ascii=False)], capture_output=True, text=True, encoding="utf-8", check=True).stdout
        self.assertEqual(json.loads(out), [
            ["vance", "thune", "king", "norton", "moore"], ["thune", "moore"], ["king"], ["thune", "king"], ["norton"], ["moore"],
            ["thune"], ["vance"], ["vance"], ["thune"], ["thune"]])

    def test_parody_statement_only_in_about(self):
        found = [f for f in sources() if "戏仿" in read(f)]
        self.assertEqual(found, ["js/about.js"])

    def test_no_labels_the_maintainer_dropped(self):
        for rel in sources():
            text = read(rel)
            for word in ("履历", "分级", "金标", "底稿", "核查状态", "截至", "一局", "七局", "导出", "我的"):
                self.assertNotIn(word, text, f"{rel}：{word}")

    def test_banner_image_sizes(self):
        for name, size in (("img/renmian.png", (750, 230)), ("img/renmian@2x.png", (1500, 460))):
            with open(os.path.join(WEB_DIR, name), "rb") as fh:
                head = fh.read(24)
            self.assertEqual(head[:8], b"\x89PNG\r\n\x1a\n", name)
            self.assertEqual(struct.unpack(">II", head[16:24]), size, name)


if __name__ == "__main__":
    unittest.main()
