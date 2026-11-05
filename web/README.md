# web（界面）

第二版界面（docs/PRODUCT.md 第八节），纯静态文件，无框架、不用构建工具，运行时不连外网；数据全部从本机主程序的 `/api/…` 取（`zuzhibu/server.py`）。桌面外壳（`desktop/`）在自己的窗口里打开本地服务的地址；从浏览器打开也能用。

- 窗口：顶部一条红色标题栏，左边“AI政见·组织部”和六个栏目（白宫、商界、国会、国防、司法、人事动态），右边搜索框和三条线按钮（设置、关于），最右是系统画的最小化、最大化、关闭（外壳的 `titleBarOverlay`，页面用 `env(titlebar-area-*)` 让出位置，启动时经 `window.shell.setTitleBarOverlay` 设成标题栏同色同高）。没有页脚，只有内容区滚动。
- 封面：打开软件先到封面（`#/`）：软件图标、“AI政见·组织部”、副题“美国政要干部简历资料库”，六个栏目的入口，最近 6 条人事动态（标题加日期）。标题栏左边的图标和名字点了回封面。
- 名单页：照 `data/rosters/<栏目>.json` 的分组和顺序逐级展示 3:4 标准像，像下写姓名和职务（名单里有 `short_post` 就用短称）。每组有标题就显示分隔标题；没有标题的组自成一块，和上一组之间画细线隔开；同一块分几组（如首席大法官单人大像、大法官一排）时，后面的组写同一个标题，只在第一组显示。一行放不下时按行均分。国会上面有分组跳转、搜索框（姓名、职务，英文名也能搜）和身份、党派、州三个下拉：身份按名单的选区认（有选区是众议员，众议员名单里没选区的是无表决权代表），州按名单顺序。筛选对两院领导块和议员名单都生效、按人算：身份、党派、州取这个人在议员名单里的那一条，一个都不剩的块整块收起，分组跳转里那一项也收起；不在议员名单里的参议院议长只在没选身份、党派、州时出现，搜姓名能搜到；“共 n 人”按人数算，领导块和议员名单里都有的只算一个。
- 人物页：只有“××简历”标题、标准像、首段、现任和按时间排的经历。
- 人事动态：列表是标题加日期；单篇从上到下是标题、正文、“人事任免”横图（`img/renmian.png`，750×230，点了回到列表）、发布时间和来源。日期按 年-月-日 写（2026-10-14），数据只到月的照实写到月（2026-09），不补日。稿件由本地服务给（`zuzhibu/renmian.py`），界面不设类别标签和筛选。
- 搜索：只搜名单里的人，在内存里即时筛，回车进人物页；按 `/` 或 Ctrl+F 聚焦。
- 动效：页面切换淡入，标准像从名单飞到人物页、返回时飞回；悬停时照片微微上浮；菜单、弹窗缩放淡入；返回时回到原来的滚动位置。浏览器要求减少动效时都关掉。

## 字体

标题、姓名用思源宋体粗体，简历和动态正文用朱雀仿宋，职务、小标题用霞鹜文楷，西文和数字用 Times New Roman（系统自带）；按钮、输入框等界面控件用微软雅黑。前三种字体文件不进仓库：`css/app.css` 引用 `../fonts/<文件名>`，由本地服务从字体目录供给：

| 顺序 | 字体目录 |
|---|---|
| 1 | 环境变量 `ZUZHIBU_FONTS` |
| 2 | 本机设置 `%APPDATA%\zuzhibu\local.json` 的 `"fonts"` |
| 3 | 程序目录下的 `fonts\`（安装版是 `resources\pyapp\fonts\`，由安装包放入） |

文件名：`NotoSerifSC-VF.ttf`、`ZhuqueFangsong-Regular.ttf`、`LXGWWenKaiGB-Regular.ttf`。取不到时降级到系统的宋体、仿宋、楷体。

## 页面

| 路由 | 页面 |
|---|---|
| `#/whitehouse` `#/business` `#/congress` `#/defense` `#/justice` | 五栏名单（默认首页白宫） |
| `#/p/<人物>` | 人物页（名单外的人没有） |
| `#/news` | 人事动态列表 |
| `#/news/<稿件>` | 人事动态单篇 |

设置、关于是三条线菜单打开的弹窗，不占路由。文字照维护者审定的文案稿，改字先问维护者。

## 接口（zuzhibu/server.py）

| 方法 | 路径 | 返回 |
|---|---|---|
| GET | `/api/app` | 软件名、版本、栏目（名称取名单文件）、默认首页 |
| GET | `/api/rosters/<栏目>` | 一栏的分组和成员（姓名、职务、标准像，国会成员带党派、州、选区） |
| GET | `/api/index` | 搜索用的名单人物 |
| GET | `/api/profile/<人物>` | 人物页（名单外 404） |
| GET | `/api/news`、`/api/news/<稿件>` | 人事动态列表（标题、日期）、单篇（标题、事件日期 `date`/`date_cn`、正文段落 `paragraphs`、来源一栏 `source_line`），由 `zuzhibu/renmian.py` 的 `articles`、`article` 生成 |
| GET | `/api/about`、`/api/license` | 版本、照片署名；许可全文 |
| GET/POST | `/api/settings`、`/api/settings/check` | 大模型接口设置（存 `%APPDATA%\zuzhibu\settings.json`，密钥打码返回）：接口类型清单、能不能开更新和原因、上次检测结果；换接口类型或地址没重填密钥时清掉旧密钥。`POST /api/settings/check` 检测联网检索（约检索 1 次） |
| GET/POST | `/api/update` | 立即更新的进度 `{state, running, step, percent, text, message, detail, result, error, started, finished, plan}`（state：idle、running、stopping、stopped、done、failed；text、message 照审定的文案）/ 开始立即更新 `{ok}` 或 `{ok: false, message}` |
| POST | `/api/update/stop` | 停止更新：不再开新会话，已核实的照样保存；`{ok, state: "stopping"}` |
| GET/POST | `/api/datasync` | 数据日期、来源、联网更新状态和进度 / `{"action":"check"}` 立即检查，`{"url":…}` 改数据地址（空串恢复默认），`{"auto":…}` 自动联网更新开关 |
| GET/POST | `/api/ping`、`/api/bye` | 页面每分钟报到一次、关闭时说一声（安装版据此判断窗口还开着） |
| GET | `/fonts/<文件>`、`/portraits/<文件>` | 字体（字体目录）、标准像（数据目录） |

接口只认 Host 为 127.0.0.1、localhost 的请求；POST 只收同源的 `application/json`。七局版的 `/api/bureaus`、`/api/people`、`/api/events`、`/api/search` 等接口保留，界面不再用。

## 目录

```
web/
  index.html            壳：标题栏（栏目、搜索、三条线菜单）、内容区
  favicon.svg           软件图标（关于页顶上也用它），由 tools/installer/icon.py 写出，和安装包的 .ico 同一个图形
  img/renmian.png       “人事任免”横图（750×230；@2x 是两倍图）
  css/app.css           全部样式，设计变量和字体在开头
  js/ui.js              DOM 构造、标准像、提示、本机浏览器存储、日期
  js/api.js             接口层（唯一的网络出口），内存缓存
  js/shell.js           标题栏：栏目、搜索、三条线菜单；和桌面外壳（window.shell）对接
  js/panels.js          弹窗框架
  js/settings.js        设置：大模型接口、更新人事动态
  js/about.js           关于：简介、声明、开源声明、友情链接、关注 AI政见（两个二维码）
  img/qr-video.png      关于页的二维码：短视频账号、图文账号（img/qr-article.png），维护者给的图；短视频那张是圆形码，原图偏心，重新裁成整圆（点阵照抄，右上角徽标外圈补完整），页面上用圆框
  js/app.js             路由、页面切换（视图过渡）、滚动位置、报到
  js/pages/home.js      封面
  js/pages/roster.js    名单页（含国会筛选）
  js/pages/person.js    人物页
  js/pages/news.js      人事动态列表和单篇
  tests/test_web.py     静态检查
```

## 测试

```
python -m unittest discover -s web/tests     # 静态检查
python -m unittest tests.test_server         # 接口
```

验收在桌面外壳里做（desktop/README.md：打目录版，设 `ZUZHIBU_REPO` 用仓库里的代码）：各页截图，`document.fonts` 看三种字体是否已加载。
