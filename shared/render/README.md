# render（渲染器）

三个渲染器都放在本目录，共用一种稿件对象（简单的 dict / JSON）。只用标准库（红头件用 zipfile 直接写 docx 的 XML 部件，不需要 python-docx）。输出写到使用者指定的位置，不写进仓库；仓库里只有虚构样例。

| 文件 | 产出 | 用途 |
|---|---|---|
| `article_html.py` | 公众号 HTML，全部行内样式 | 直接粘进公众号编辑器 |
| `card_html.py` + `card.css` | 3:4 图文卡 HTML/CSS，可带配文 txt | 本机用 Playwright 截成 1620×2160 PNG |
| `docx_redhead.py` | docx 红头件 | 在文字处理软件里继续改 |
| `common.py` | 稿件对象规范、字宽估算、断行 | 三个渲染器共用 |
| `build_examples.py` | 重建或检查 `examples/` 里的生成样例 | 测试也调它 |


## 稿件对象

一个 dict 或 JSON 文件，字段用英文键；不认识的键报错，下划线开头的键（如 `_comment`）忽略。

| 字段 | 中文 | 公众号 | 图文卡 | 红头件 | 说明 |
|---|---|---|---|---|---|
| `title` | 标题 | ✓ | ✓ | ✓ | 必填。简历写“××简历” |
| `subtitle` | 副题 | ✓ | ✓ | ✓ | 如讲话稿的“——在××上的讲话” |
| `byline` | 署名行 | ✓ | | ✓ | 标题、副题下居中的几行，字符串或列表：讲话稿的日期行“（2026年1月12日）”和讲话人行 |
| `summary` | 提要 | ✓ | | | 浅蓝提要框文字；缺省依次取现任段、首段第一句 |
| `photo` | 照片路径 | ✓ | ✓ | | 相对输出文件的路径或 URL |
| `lead` | 首段 | ✓ | ✓ | ✓ | 干部简历固定句式的首段 |
| `current` | 现任段 | ✓ | ✓ | ✓ | 政治人物独立成段的“现任……” |
| `careers` | 履历条目 | ✓ | ✓ | ✓ | 列表，写法见下 |
| `paragraphs` | 正文段落 | ✓ | ✓ | ✓ | 讲话稿、新闻稿正文，字符串列表；短的冒号结尾行当称谓，顶格；“一、”开头的短行当一级标题 |
| `closing` | 尾段 | ✓ | ✓ | ✓ | 正文之后的结束段 |
| `afterword` | 写在最后 | ✓ | | | 替换固定段的文字；缺省用默认文字，空字符串不输出该段 |
| `redhead` | 红头 | | | ✓ | `agency` 发文机关标志、`doc_number` 发文字号、`recipient` 主送机关、`signoff` 署名、`date` 成文日期 |
| `name` | 人名 | ✓ | ✓ | | 照片 alt 和配文标签用；缺省从标题去掉“简历” |
| `tags` | 话题标签 | | ✓ | | 配文的 #标签，公司名等，不带 # |

履历条目有三种写法，可以混用：

```json
{"period": "1994年7月－1999年2月", "organization": "青岚微电子公司", "role": "设计工程师、设计部总监", "note": "其间：1995年－1997年在远望大学电气工程专业在职研究生学习，获理学硕士学位"}
["1999年3月－2011年12月", "星川科技有限公司（Starbrook）", "副总裁"]
"2018年7月至今 北辰半导体公司总裁、首席执行官、董事"
```

`organization` 和 `role` 分开写最好：图文卡断行要靠它找到机构名；英文与汉字相接时自动空一格。`note` 是括注（其间、兼任），不带括号时会补上全角括号。渲染器不改稿件文字，译名、日期格式等由 lint 管。

## 用法

在仓库根目录运行：

```
python -m shared.render.article_html 稿件.json -o 稿件.html                       # 带预览外壳的整页
python -m shared.render.article_html 稿件.json --fragment                         # 只打印可粘贴的正文片段
python -m shared.render.card_html 稿件.json -o 稿件.card.html --caption 稿件.txt  # 样式内联；--link-css 改为链接 card.css
python -m shared.render.docx_redhead 稿件.json -o 稿件.docx --local-fonts        # 方正小标宋简体、仿宋_GB2312、黑体、楷体_GB2312、宋体、Times New Roman
```

Python 里调用：

```python
from shared.render.article_html import render_article_html, render_article_page
from shared.render.card_html import render_card_html, render_caption, paginate
from shared.render.docx_redhead import render_docx

doc = {"title": "××简历", "lead": "……", "careers": [["2001年1月至今", "××公司", "总裁"]]}
fragment = render_article_html(doc)          # 可粘贴的片段；render_article_page 是整页
html = render_card_html(doc)                # 首页加续页，一份 HTML
pages = paginate(doc)                       # 看续页怎么分的
render_docx(doc, "out.docx", fonts={"body": "仿宋_GB2312", "latin": "Times New Roman"})
```

三个入口都接受 dict 或 JSON 文件路径；稿件不合规抛 `common.RenderError`，命令行退出码 2。

## 版式

### 公众号 HTML

- 自上而下：副题、署名行、浅蓝提要框（`#EAF3FB` 底、`#3771BB` 左边线）、居中照片（宽 60%，最大 300px）、首段、现任段、履历行（左缩进 2em，起止时间在前）、正文段落、尾段、“写在最后”固定段。
- 全部行内样式，没有 `<style>` 和 class。正文 16px、行距 1.75、两端对齐、首行缩进 2em；“一、”一级标题加粗。
- 标题不在片段里，粘贴后在编辑器里填；照片要在编辑器里重新上传。
- 导出物只留正文，默认不加“写在最后”；稿件对象的 afterword 字段给了文字才加。

### 3:4 图文卡

按 rules/cadre.md 第 5 节：

- 一张卡 540×720，CSS 里 1px 当 1 磅；底色 `#3771BB`，白字。视口 540×720、deviceScaleFactor 3 逐张截图，就是 1620×2160。
- 首页：标题 44（小标宋），照片框 165×220（等比填满；偏高裁底部，偏宽两侧等裁），首段和现任段 20、首行缩进 2 字、1.5 倍行距（30px）、两端对齐，不加手动换行。
- 续页：整页高的文本框，垂直居中，段落居中。每条日期一段、内容一段，条目之间空一段；一页最多 23 行（两侧留白 36 后正文宽 468，约 23 个字），超过就分页。履历一律放续页，首页不放。
- 内容行超过约 22 个字宽时断行：机构名后、逗号后、括注前，贪心装满一行；数字和英文按半个字宽估算。
- 字体只写名字：正文 `"Times New Roman"` 在前、`"仿宋_GB2312"` 等在后，于是数字英文走 Times New Roman、汉字走仿宋；标题 `"方正小标宋简体"`，缺字体时退到思源宋体。都在 `card.css` 的 `:root` 变量里，本机可整体覆盖。
- 配文 txt：标题、空行、首段原文、空行、`#AI政见 #人名 #标签 #干部简历`。

### docx 红头件

- A4，上 37、下 35、左 28、右 26 毫米。
- 发文机关标志 36 磅红字居中（缺省“AI政见·组织部文件”，一律虚构机关名）；发文字号 3 号居中；其下 4 毫米一条与版心等宽的红线（1.5 磅）；红线下空两行是标题，2 号居中；副题 3 号居中；署名行（讲话稿的日期行、讲话人行）3 号楷体居中；主送机关顶格；正文 3 号、固定行距 28 磅、首行缩进 2 字（写的是 firstLineChars，不随字号变）、两端对齐；“一、”一级标题黑体，“（一）”二级标题的标题句楷体、句后正文仍用正文字体；称谓行顶格；落款署名右空二字、成文日期右空四字。
- 文档网格每行 28 字、每页 22 行（`w:docGrid` linesAndChars）；机关标志、标题、署名行、落款不对齐网格，免得大字被撑开。
- 履历条目在正文里一条一段：起止时间、全角空格、单位职务。
- 页码 4 号“—　1　—”，单页居右、双页居左，各空一字（奇偶页页脚不同）；`--no-page-numbers` 可去掉。需要时用 `--footer-note` 在页码下加一行灰字说明，默认不加。
- 字体七项可配：`header`（机关标志）、`title`、`body`（汉字）、`latin`（数字西文）、`heading`（一级标题，黑体位）、`kai`（二级标题、署名行，楷体位）、`page`（页码，宋体位）。默认免费字体：思源宋体（Noto Serif CJK SC）、思源黑体（Noto Sans CJK SC）和 Liberation Serif（与 Times New Roman 同度量）。`--local-fonts` 换成方正小标宋简体、仿宋_GB2312、黑体、楷体_GB2312、宋体、Times New Roman，也可逐项 `--font-body` 等指定。字体文件不进仓库。
- 字号、行距、边距、网格在 `docx_redhead.LAYOUT`，`render_docx(..., layout={"line_pitch": 28.5})` 或命令行 `--line-pitch 28.5` 可覆盖。同样的输入生成同样的字节（zip 时间戳固定）。

## 样例 examples/

| 文件 | 说明 |
|---|---|
| `cadre.json` | 虚构企业负责人简历，“现任”写在首段里，带其间、兼任括注 |
| `politician.json` | 虚构政治人物，“现任”独立成段，履历分两张续页 |
| `speech.json` | 虚构讲话稿，走 `paragraphs` 和 `redhead` |
| `portrait.svg` | 占位头像，3:4，无文字 |
| `*.article.html`、`*.card.html`、`*.caption.txt`、`*.redhead.docx` | 三个渲染器的生成结果 |

人名、机构名、州名全部虚构。改了渲染器或样例输入后运行 `python -m shared.render.build_examples` 重建，`--check` 只比对不写；测试会检查样例是否过期（docx 只比 `word/*.xml`，因为 zip 里的时间戳每次不同）。

## 测试

```
python -m unittest                                 # 全部
python -m unittest tests.test_render_card_html     # 单个
```

`tests/test_render_*.py` 做结构性检查：字段规范化与断行；公众号 HTML 标签配对、全部行内样式、各部件存在与顺序；图文卡的 CSS 参数、分页行数、断行位置、页数；红头件直接读 zip 里的 `word/document.xml`，查段落顺序、红线、字号、字体名、缩进、行距、页边距、页码、文档网格、署名行和标题字体。

## 留给本机的事（云上不跑）

### 1. Playwright 截图

```python
# 本机一次性准备：pip install playwright && playwright install chromium
from pathlib import Path
from playwright.sync_api import sync_playwright

html = Path("out/李明远.card.html").resolve()
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 540, "height": 720}, device_scale_factor=3)
    page.goto(html.as_uri())
    page.evaluate("document.fonts.ready")              # 等本机字体加载完
    for index, card in enumerate(page.locator(".card").all(), start=1):
        card.screenshot(path=f"out/{index:02d}.png")   # 每张 1620×2160
    browser.close()
```

### 2. 字体核对

- 确认本机装了仿宋_GB2312、方正小标宋简体、Times New Roman，且字体家族名与 `card.css` 里写的一致；名字不同就改 `:root` 变量，或在输出 HTML 前面加一段 `<style>:root{--card-font-body: …}</style>` 覆盖。
- 看截图：数字、英文是 Times New Roman 的衬线形，汉字是仿宋，首页标题是小标宋。退成了思源宋体或黑体，说明名字没对上。
- docx：在文字处理软件里打开 `--local-fonts` 生成的文件，字体面板应显示仿宋_GB2312、方正小标宋简体、Times New Roman；查红线位置、2 字缩进、28 磅行距（每页 22 行）、落款位置，按需要改 `LAYOUT`。

### 3. 像素回归

- 首次用本机字体截一套 PNG 作基线，留在本机，不进仓库。以后改了 CSS 或渲染器就重截并逐像素比对（Pillow 的 `ImageChops.difference`，或 Playwright 的 `expect(page).to_have_screenshot()`），差异超过阈值再人工看。
- 与 aipolis 的 PPT 模板导出图并排比：标题位置、照片框、首段行数、续页每行字数（约 22）、23 行是否顶到边。

## 边界

- 不做公众号自动发布，云上不装浏览器、不跑截图，仓库不含字体文件。
- 红头和电头一律虚构机关名。

## 模块说明（2026-10-17 合并自原子目录 README）

- `article_html.py`（公众号 HTML）：输入稿件对象，输出一段全部用行内样式的 HTML，可直接粘进公众号编辑器：浅蓝提要框、居中照片、首段、带缩进的履历行、固定的“写在最后”段。照片要在编辑器里重新上传；推草稿箱由本机工具另做（需要公众号后台 IP 白名单）。
- `docx_redhead.py`（docx 红头件）：输入稿件对象（可带 `redhead` 红头字段），输出可在常用文字处理软件里修改的 .docx。只用标准库 zipfile 直接写 docx 的 XML 部件，不依赖 python-docx：发文机关标志、发文字号、红色分隔线、标题、署名行、主送机关、正文、落款、页码和页脚是页码。只写字体名，不含字体文件；默认免费字体，本机可换成方正小标宋简体、仿宋_GB2312、黑体、楷体_GB2312、宋体、Times New Roman。发文机关名一律虚构。在文字处理软件里核版式这一步留给本机。
- `card_html.py` 加 `card.css`（3:4 图文卡）：输入稿件对象（简历或活动报道），输出一份 HTML，每张卡 540×720（CSS 里 1px 当 1 磅）：首页标题、照片、首段，续页履历，按行数分页。HTML 版只作网页预览和版式核对；正式出图仍走本机 aipolis 的 PPT 模板与办公软件导出（1620×2160 PNG），或本机 Playwright 三倍截图。仓库不含字体文件，云会话不跑浏览器。
