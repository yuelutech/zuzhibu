# desktop（桌面外壳）

Electron 外壳：启动内置的 Python 本地服务（`python -m zuzhibu`），在自己的窗口里打开界面（`web/`）。数据分发、每月更新、用户自带 AI 接口都在 Python 里，外壳不管。

| 文件 | 做什么 |
|---|---|
| `main.js` | 主进程：起本地服务、等它答应后打开；退出时结束服务；只开一个实例；记住窗口大小和位置；服务起不来时给出错页；外链交给系统浏览器 |
| `preload.js` | 给页面的接口，只有 `window.shell` 四项（见下） |
| `lib/policy.js` | 规则：哪些地址在窗口里开、标题栏配色的取值、窗口位置的恢复、认出本地服务的地址；测试在 `test/` |
| `lib/recovery.js` | 启动时崩溃的自救规则（见下）；测试在 `test/` |
| `lib/store.js` | 商店版（微软应用商店的 MSIX 包）的本机目录（见下）；测试在 `test/` |
| `pages/` | 外壳自带的“正在打开”和出错页 |
| `electron-builder.yml`、`resources/installer.nsh` | 打安装包和商店包（appx）的配置，安装脚本补充规则（不许降级、`--no-shortcuts`） |

## 本地服务

- 安装版：`resources\runtime\python.exe -X utf8 -m zuzhibu --no-window --port 0 --home <本机目录> --parent-pid <外壳进程号>`，工作目录 `resources\pyapp`（程序和附带数据，有 `dist.json` 即按安装版运行）。
- 外壳从服务的输出里读出 `http://127.0.0.1:<端口>/`，`/api/health` 答应了才在窗口里打开；90 秒内没就绪、服务启动失败或意外退出，显示出错页，可重试、打开日志所在文件夹。
- 本机目录 `%LOCALAPPDATA%\zuzhibu`（环境变量 `ZUZHIBU_HOME` 可改）：`data\` 数据（Python 管），`logs\shell.log`、`logs\service.log` 日志，`shell\` 窗口位置和浏览器配置。外壳把它经 `--home` 和环境变量 `ZUZHIBU_HOME` 交给本地服务。
- 商店版（装在 `WindowsApps\<包全名>\app\` 下，`process.windowsStore`）的本机目录在包自己的数据目录 `%LOCALAPPDATA%\Packages\<包系列名>\LocalState`：系统把商店版在 AppData 下新建的文件另存到包的私有位置、改已有的文件却改在原处，和下载版共用 `%LOCALAPPDATA%\zuzhibu` 会两边错乱，资源管理器也打不开私有位置的日志目录（`lib/store.js`）。
- 外壳退出时结束本地服务；外壳意外崩溃时，服务凭 `--parent-pid` 自己退出。
- “正在打开”页和本地服务同时开始，页面进程出了事不耽误服务启动。

## 启动时崩溃

显卡驱动出状况、显卡缓存坏了的电脑上，页面进程或显卡进程会在启动时崩溃。外壳的处理（规则在 `lib/recovery.js`）：

- 每次启动先在 `shell\startup.json` 记一笔“这次还没打开”，程序页面或出错页打开了、或正常退出了再清掉。启动阶段（到程序页面打开后 5 秒）页面进程或显卡进程崩溃，或“正在打开”页 30 秒没加载出来，外壳就记下这次没打开、自己重开；整个程序在启动时被结束的，下次启动也照此算。
- 上次没打开：这次先清掉显卡缓存（用户数据目录里的 `GPUCache`、`GrShaderCache`、`ShaderCache`、`DawnGraphiteCache`、`DawnWebGPUCache`、`GPUPersistentCache`）。
- 连着两次没打开：关掉显卡加速，并记进本机设置 `%APPDATA%\zuzhibu\local.json` 的 `"gpu": "off"`（原有的项保留），以后都这样开。删掉这一项，或写 `"gpu": "on"`，就恢复显卡加速。环境变量 `ZUZHIBU_GPU=on|off` 只管这一次，排查用。
- 关着显卡加速还打不开：不再自动重开，弹一句“软件没能打开，请重新启动电脑后再打开”，可打开日志所在文件夹。
- 启动以后页面进程崩了：重新加载，一分钟内最多两次；第三次按启动时崩溃处理。显卡进程在启动以后崩了，Chromium 自己会重开它，外壳只记日志。
- 自己重开用 `app.relaunch`：先藏起窗口、结束本地服务，留 2 秒让它的助手进程认准本进程再退出（电脑忙时助手起得慢，退得太快它会认错、一直等）；退出 3 秒还没完成就强行结束。
- 每一步都写进 `logs\shell.log`，不弹技术性的报错。

试装模式下（`ZUZHIBU_SHELL_TEST`）可用 `ZUZHIBU_SHELL_TEST_CRASH="gpu,renderer"` 让第 1、2 次启动在启动阶段弄崩显卡进程、页面进程，`tools/installer/smoke.py` 用它核对上面几种情形（`--shell <win-unpacked 目录>` 不安装、只试这一项）。

## 窗口与页面接口

窗口用 `titleBarStyle: 'hidden'` 加 `titleBarOverlay`：最小化、最大化、关闭由系统画在右上角，默认底色 `#B8141C`、按钮白色、高 48（和界面标题栏一致，界面启动时也会再设一次）。三条线按钮和可拖动区由页面自己画，布局用 `navigator.windowControlsOverlay` 或 CSS 的 `env(titlebar-area-*)`。

```js
window.shell.version            // 外壳版本号，如 "1.0.0"
window.shell.platform           // "win32"
window.shell.openExternal(url)  // 用系统浏览器打开 http、https 链接；别的协议不放行。返回 Promise<boolean>
window.shell.setTitleBarOverlay({ color: '#B8141C', symbolColor: '#FFFFFF', height: 48 })
                                // 颜色收 #rgb、#rrggbb、#rrggbbaa，高度 0–120；不合规的项不改。返回 Promise<boolean>
```

页面拿不到 Node（`contextIsolation` 开、`nodeIntegration` 关、沙箱运行）。窗口里只开本地服务和外壳自带的页面，别的地址（链接、`window.open`、跳转）一律交给系统浏览器。没有菜单栏；F5 刷新；从仓库运行时 F12 开发者工具。

## 开发

```
cd desktop
npm ci                      # 按锁文件装依赖（node_modules 不进仓库）
npm start                   # 用仓库里的代码和数据运行（Python 取 ZUZHIBU_PYTHON、本机设置 local.json 的 python，或 PATH 里的 python）
npm test                    # 规则测试（python -m unittest 也会调）
```

`npm start` 打不开时，先打一个目录版，再让它用仓库里的代码：

```
python -m tools.installer.build --stage-only --no-test   # 备好嵌入版 Python 和程序（打包要用）
npm run pack                                            # 打目录版 ..\dist\win-unpacked
set ZUZHIBU_REPO=<仓库目录>
..\dist\win-unpacked\AI政见·组织部.exe
```

正式的安装包在仓库根目录用 `python -m tools.installer.build` 打，试装用 `python -m tools.installer.smoke`，见 `docs/INSTALL.md`。
