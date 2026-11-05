# 安装、升级、卸载与数据更新

本文件说明 AI政见·组织部 Windows 版的安装、升级、卸载，软件怎样联网取维护者每月更新的数据，以及维护者怎样构建和试装安装包。产品范围以 [PRODUCT.md](PRODUCT.md) 为准。

## 系统要求

- Windows 10 或 11，64 位。
- 不用另装 Python：安装包自带 python.org 发布的 Python 嵌入版（3.14.8）和桌面外壳（Electron）。
- 不需要管理员权限：只给当前用户装。

## 安装

1. 下载 `zuzhibu-<版本>-win-x64-setup.exe` 和同名的 `.sha256`。核对文件没被改过：

   ```
   certutil -hashfile zuzhibu-1.0.0-win-x64-setup.exe SHA256
   ```

   输出的值应与 `.sha256` 里的一致。
2. 双击安装程序，按提示完成安装。
3. 一步装好：显示一个进度窗口，装完自动打开软件。程序装在 `%LOCALAPPDATA%\Programs\zuzhibu-desktop`（打包工具按用户安装时用程序的英文包名作目录名），开始菜单和桌面出现“AI政见·组织部”。
4. 安装包里带一份数据（构建当天仓库的 `data/`），断网也能看。

静默安装（批量部署、脚本）：

```
zuzhibu-1.0.0-win-x64-setup.exe /S
zuzhibu-1.0.0-win-x64-setup.exe /S --no-shortcuts /D=D:\Apps\AI政见·组织部
```

| 参数 | 说明 |
|---|---|
| `/S` | 静默安装：不显示界面，装完不自动打开 |
| `/D=<目录>` | 安装位置；必须放在最后、不加引号。升级时沿用上次的位置 |
| `--no-shortcuts` | 不放开始菜单和桌面快捷方式 |

## 使用

- 同时只开一份：已经开着时再点图标，回到原来的窗口。窗口大小和位置下次打开照旧。
- 关掉窗口软件就退出，后台的本机服务一同结束。
- 打不开时窗口里会说明原因，可以重试，或打开日志所在文件夹；日志在 `%LOCALAPPDATA%\zuzhibu\logs`（`shell.log`、`service.log`）。
- 显卡这边出问题、启动时页面闪退的，软件自己处理：先清掉显卡缓存重开一次；还不行就关掉显卡加速重开，并记进本机设置 `%APPDATA%\zuzhibu\local.json`（`"gpu": "off"`），以后都这样开，画面照常、略费一点处理器。驱动更新后想重新用显卡加速，删掉这一项即可。关了显卡加速仍打不开时，软件不再自动重开，只提示重新启动电脑后再试。
- 立即更新只用您自己的额度（自己的 AI API，或这台电脑上自己登录的 Codex），见“我的”页；不配置也能看维护者每月更新的数据。软件不带、也不用维护者的任何账号或额度。

## 数据更新

| 时机 | 做什么 |
|---|---|
| 第一次打开 | 用安装包附带的数据铺底，放进本机数据目录 `%LOCALAPPDATA%\zuzhibu\data` |
| 每次打开；开着时每天最多一次 | 联网检查仓库 main 分支上的数据：`https://raw.githubusercontent.com/yuelutech/zuzhibu/main/data/`，有新数据才下载 |
| 装了新版 | 新安装包附带的数据比本机的新，就并进来 |

数据多久更新：维护者每月更新一次（每月 1 日前后）。软件联网检查只读 GitHub 上的公开文件，不花任何人的 AI 额度；想更勤，在“我的”页填自己的 AI API，再到人事动态页点“立即更新”，用自己的额度检索从数据截止日到今天的变动（用 API 执行更新是路线图第 7 步，接通前，这台电脑上自己登录了 Codex 的可先用它）。软件不带、也不用维护者的任何账号、密钥或额度。

怎样取：

1. 先取数据清单 `manifest.json`：`data/` 下每个文件的 SHA-256 和字节数，加生成时间和数据日期。清单没变（服务器回 304）就到此为止。
2. 只下载发布方新增或改动过的文件，每个文件下完核对字节数和 SHA-256，JSON 文件再核对能解析。
3. 全部到齐后另建一份完整数据（没变的文件用硬链接，不多占空间），试读一遍，通过了才切换过去，再删掉旧的一份。发布方删掉的文件同步删掉。

失败不破坏本机数据：地址取不到（仓库公开前就是这样）、断网、校验不符、下到一半断开、软件被强行关掉、断电，软件都继续用原来那份完整数据；下次接着下，已下好并核对过的文件不再下。取不到时“我的”页只标“未取到”，不报错。

“我的”页的“数据”一栏显示数据日期和上次联网检查的结果，可以：

- 立即检查更新；
- 关掉自动联网更新（只在点“立即检查”时联网）；
- 改数据地址：任何按同样目录结构提供 `data/` 的 https 地址（比如自建的镜像），清空恢复默认。

本机“立即更新”写进来的改动：发布方没改过的文件保留，发布方改过的以发布的为准。

隐私：联网只发 GET 请求取公开的数据文件，请求里只有软件名和版本，不带任何用户信息，不上传任何东西。

本机数据目录（`%LOCALAPPDATA%\zuzhibu\data`）：

| 路径 | 内容 |
|---|---|
| `current.json` | 指向当前在用的那份数据 |
| `snapshots\<编号>\` | 一份完整数据（people、events、portraits、meta.json、manifest.json），建好后不再改 |
| `staging\` | 下载中、已核对过的文件，按 SHA-256 存；更新完成后清空 |
| `sync.json` | 最近一次联网的时间、结果、来源 |

## 升级

- 下载新版安装程序直接双击：先关掉在运行的旧版（含后台的本机服务），卸掉旧版、装上新版，装完自动打开。安装位置、本机数据、设置都保留。
- 不能装回比已装版本更旧的版本（会提示已装了更新版本，静默安装以退出码 2 结束）：要换回旧版，先卸载再装。

## 卸载

- “设置”→“应用”→“已安装的应用”，找到“AI政见·组织部”卸载；或者静默卸载：

  ```
  "%LOCALAPPDATA%\Programs\zuzhibu-desktop\Uninstall AI政见·组织部.exe" /S
  ```

- 卸载先关掉在运行的软件，再删除程序目录和快捷方式。本机数据和设置保留，重装后接着用。要彻底清除，再删两个目录：`%LOCALAPPDATA%\zuzhibu`（数据、日志、窗口配置）和 `%APPDATA%\zuzhibu`（设置，含 AI API 密钥）。

## 从源码运行

```
python -m zuzhibu                         # 读仓库的 data/，不联网取数（数据随 git pull 更新），用浏览器的应用窗口打开
python -m zuzhibu --sync --home 本机目录    # 按安装版的方式：本机数据目录、联网取数
cd desktop && npm ci && npm start         # 用桌面外壳打开（见 desktop/README.md）
```

## 维护者

### 数据清单

一局更新脚本（`bureaus/dispatch/daily.ps1`）提交前运行 `python -m zuzhibu.datasync manifest` 重算 `data/manifest.json`。手工改了 `data/`（补采入库等）也要在提交前运行；没重算时，软件取到的仍是上一版清单对应的数据，要等下次重算清单后才取到新的。

```
python -m zuzhibu.datasync manifest                     # 重算清单
python -m zuzhibu.datasync check --home 临时目录 --bundled data   # 按软件的方式取一次，看结果
```

### 构建安装包

只能在 Windows 上构建。要 Python 和 Node.js 24（npm）；Electron 和 electron-builder 按 `desktop/package-lock.json` 装，它们和 npm 的下载缓存都放 `build/cache/`：

```
python -m tools.installer.build
```

1. 下载 python.org 的嵌入版 Python（版本和 SHA-256 写在 `tools/installer/build.py`，缓存在 `build/cache/`，每次用前核对）。
2. 组装待装目录 `build/installer/stage/`：`runtime\` 是嵌入版 Python，`python3xx._pth` 加一行 `..\pyapp`、不导入 site；`pyapp\` 是程序、界面、写法检查、一局流水线、译名表、附带数据和清单、`dist.json`，`pyapp\fonts\` 是随软件分发的字体（见下）。测试、样张、写法规范（`shared/skills/`）、各局原料、暂缓的秘书局和宣传教育局不装；仓库里的字体文件一律不装。
3. 用嵌入版 Python 预编译、做导入自检，再跑一遍仓库的单元测试（`--no-test` 跳过）；画图标。
4. `npm ci` 装外壳的依赖，electron-builder 按 `desktop/electron-builder.yml` 打出 `dist/zuzhibu-<版本>-win-x64-setup.exe` 和 `.sha256`；过程文件（解包目录等）在 `build/installer/electron/`。

随软件分发的字体有三款，都按 SIL Open Font License 1.1 授权：思源宋体（Noto Serif SC 可变字重，Serif2.003）、朱雀仿宋（v0.212）、霞鹜文楷 GB（v1.530）。构建时由 `tools/installer/fonts.py` 从各自的官方发布下载（思源宋体只按范围取发布包里的那一个文件），逐个核对 SHA-256，缓存在 `build/cache/fonts/`；放进 `pyapp\fonts\` 的有字体文件、各自的许可证全文（`LICENSE-<字体>.txt`）和 `FONTS.txt`（名称、版本、来源）。字体不进仓库，只给软件自己的页面用，不装进 Windows 的字体目录；Times New Roman 用系统已装的，不随软件分发。升级字体时改 `fonts.py` 里的版本、地址和两个 SHA-256（字体文件、许可证），先运行 `python -m tools.installer.fonts` 下载核对。

版本号在 `zuzhibu/server.py` 的 `VERSION` 和 `desktop/package.json` 的 `version` 两处，发布新版前一起改（不一致时构建会停）。升级 Python 时改 `build.py` 里的 `PYTHON_VERSION` 和 `PYTHON_SHA256`（python.org 发布页列出的 SHA-256）；升级 Electron 用 `npm install --save-dev --save-exact electron@<版本>`（在 `desktop/` 里），提交锁文件。`desktop/electron-builder.yml` 里的 `appId` 是各版本互认的标识，不能改。

安装包的做法：electron-builder 的一步安装（NSIS），按用户装到 `%LOCALAPPDATA%\Programs\zuzhibu-desktop`（一步安装按用户装时，electron-builder 一律用 `desktop/package.json` 的 `name` 作目录名；`/D=` 指定的位置会记进注册表，升级沿用）、不弹管理员确认；`resources\runtime` 是嵌入版 Python，`resources\pyapp` 是程序和附带数据，外壳以 `resources\runtime\python.exe -m zuzhibu --no-window` 起本机服务；升级和卸载时先关掉安装目录里在运行的程序；不许降级和 `--no-shortcuts` 写在 `desktop/resources/installer.nsh`；卸载不删本机数据和设置。

### 试装

```
python -m tools.installer.smoke dist/zuzhibu-1.0.0-win-x64-setup.exe
```

全程在后台：软件窗口不显示、不进任务栏，不放快捷方式，装在工作目录（默认 `build/smoke/<时间>`，`--work` 可改），软件的本机目录和设置也在那里，不碰这台电脑上真实的安装和数据：静默安装，核对登记，逐项核对装好的内容（见下）→ 后台启动，核对本机服务答应、附带数据铺底，像使用者那样在设置里填一个 AI 接口（假密钥，地址连不上）→ 再启动一次应立即退出（只开一个实例）→ 退出时本机服务随之结束，强行结束外壳时本机服务也自己退出、不留进程 → 启动时崩溃的自救：在启动阶段弄崩页面进程、显卡进程（开发者协议的 `Page.crash`、`Browser.crashGpuProcess`；窗口不显示时显卡进程有时迟迟不起来，3 秒没崩成就按显卡进程崩溃处理），崩一次应清掉显卡缓存自动重开，连崩两次应关闭显卡加速重开、记进本机设置、下次直接关着开，一直崩应开满 3 次就停下、不留进程 → 另打一个高版本的包试升级（旧版自动卸掉、装在原处，本机数据、设置和填的接口、窗口记录原样保留，新版读得到填的接口），再试装回原包（应被拦下）→ 静默卸载，核对程序目录和登记清干净、本机数据和设置保留。`--quick` 跳过升级与降级。这台电脑已装有本软件时拒绝试装（`--force` 才继续）。只改了外壳时，可以不打安装包、不安装，只试启动自救：`python -m tools.installer.smoke --shell <electron-builder 解包目录 win-unpacked>`。联网取数的各种情形（成功、404、连不上、半途断开、强行结束后接着下）由 `tests/test_datasync.py` 覆盖。

逐项核对装好的内容：

```
python -m tools.installer.contents [目录]
```

目录可以是安装目录、`build/installer/electron/win-unpacked` 或待装目录 `build/installer/stage`，不给就用这台电脑“已安装”里登记的位置。核对：程序和界面文件和仓库里的逐个一致；附带数据和数据清单逐个一致；标准像都是 `tools/import_portraits.py` 规定的尺寸、和 `index.csv` 一一对应（另报名单里有标准像的人数）；三款字体和许可证全文都在、校验对，别处没有字体文件；没有设置、登录凭据一类的文件，程序和数据里没有像凭据的串。有一项不对退出码 1。

后台试装只说明安装包本身没问题。发布前还要在一台正常使用的电脑上照使用者的做法装一次：双击安装程序，不带参数，装到默认位置，放桌面和开始菜单快捷方式，从桌面快捷方式打开一次，再运行上面的核对。

### 发布

从公开仓库的检出构建（安装包附带的是构建时 `data/` 的全部文件），试装通过后把 `-setup.exe` 和 `.sha256` 上传到 GitHub Releases。

默认数据地址写在 `zuzhibu/datasync.py` 的 `DEFAULT_SOURCES`，构建时记进 `dist.json`。仓库换了名字或位置，改它并重新构建；已装的旧版可在“我的”页把数据地址改成新的。

### 微软应用商店

#### 打商店包

```
python -m tools.installer.build --store
```

打安装包的同时，用同一个解包目录打出 `dist/zuzhibu-<版本>-win-x64-store.appx` 和 `.sha256`，打完逐项核对：包的清单（身份三项、版本、显示名“AI政见·组织部”、语言 zh-CN、只要完全信任一项权限、程序和图标都在包里），再把包解到临时目录做和安装包一样的内容核对。单独核对：`python -m tools.installer.contents dist/zuzhibu-<版本>-win-x64-store.appx`。

- 身份三项要和合作伙伴中心“产品标识”页一字不差，填在 `tools/installer/build.py` 的 `STORE_IDENTITY_NAME`、`STORE_PUBLISHER`、`STORE_PUBLISHER_DISPLAY_NAME`（公开信息，不是密钥）。没填时 `--store` 停下；临时可用 `--store-identity`、`--store-publisher` 给。
- 版本号照常改两处；商店包的版本是 `<版本>.0`，第四段留给商店。
- 开始菜单、任务栏、磁贴的图标由 `tools/installer/icon.py` 的 `appx_assets()` 画（和安装包同一个图形），构建时放进 `build/installer/store-resources/appx/`。
- 要先自己试，在合作伙伴中心把受众设成“私密”，审核通过后从商店链接装。

商店版和下载版的不同：

- 程序装在 `C:\Program Files\WindowsApps\` 下（只读），商店自动更新程序；数据照样每月联网取，和下载版同一套。
- 本机目录（数据、日志、窗口配置）在 `%LOCALAPPDATA%\Packages\<包系列名>\LocalState`，不和下载版的 `%LOCALAPPDATA%\zuzhibu` 混用（`desktop/lib/store.js`）；卸载商店版时一起删掉。
- 设置在 `%APPDATA%\zuzhibu`：按系统对商店应用的规定，这台电脑上原有的设置文件（下载版留下的）商店版照样读写，商店版新建的另存在包的私有位置，卸载商店版时删掉。
- 两版能同时装，开始菜单里会有两个同名的入口，一台电脑最好只装一个。

#### 上架步骤（维护者本人办）

1. 开开发者账号：从 https://storedeveloper.microsoft.com 进（别的入口走旧流程），选免费开始。
   - 个人账号：个人微软账户登录，用手机拍身份证件和自拍核验身份，补全资料；发布者显示名填“AI政见”（50 字以内，要有权使用，和别人重名的不给用）。
   - 公司账号（以 Yuelu Tech 的名义发布时用，也免费）：要邓白氏编码，或营业执照一类的正式文件，外加公司域名的工作邮箱；人工审核一般 2–5 个工作日。商店政策：顾客会把发布者名当成企业的，应当用公司账号。
2. 预留名称：合作伙伴中心 →“应用和游戏”→ 新建产品 →“MSIX 或 PWA 应用”，名称填“AI政见·组织部”。
3. 抄产品标识：这个应用的“产品管理 → 产品标识”页，把 Package/Identity/Name、Package/Identity/Publisher、Package/Properties/PublisherDisplayName 填进 `tools/installer/build.py`，提交。
4. 打商店包：`python -m tools.installer.build --store`，核对全部通过。
5. 开始提交，逐页填：
   - 定价和可用性：免费；市场按需要选；第一次先选私密受众，自己从商店装上试用，再改成公开。
   - 属性：类别（如“书籍和参考”或“新闻和天气”）；隐私政策网址（软件联网取公开数据，使用者填的 AI 接口密钥只发往使用者自己的接口；可以链到公开仓库里写隐私的那段）；网站填公开仓库地址。
   - 年龄分级：答完问卷。
   - 程序包：上传 `.appx`。
   - 商店列表（中文简体）：说明，至少 1 张截图（建议 4 张）。
   - 提交选项：受限权限 runFullTrust 写理由（Electron 桌面程序，内置 Python 起本机服务，要完全信任运行）；“给认证人员的说明”写清：内置 python.org 的 Python，本机服务只在 127.0.0.1 上；联网只读 GitHub 上的公开数据；“立即更新”要使用者自己的 AI 接口，不配置也能用。
6. 提交认证：一般几个工作日内出结果，通过后上架。以后每发一版：改版本号，重打商店包，新建提交、上传。
