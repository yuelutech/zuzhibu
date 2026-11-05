# tools

维护工具。

| 工具 | 用途 |
|---|---|
| `guard.py` | 守护检查：字体、凭据、第三方 App 名称每次跑测试都查（`tests/test_guard.py`）；`--history` 查全部 git 历史的凭据和提交署名，`--release` 再查要移出的目录、照片许可、长摘录、本机路径（见 `docs/公开前检查.md`） |
| `gaps.py` | 缺项清单：每人缺什么（完整简历卡条件、中文名、照片），可按局、分批、只列有缺项的 |
| `sync_glossary.py` | 译名表同步：本机母本的 v1.4 三张表拷进 `shared/glossary/`，维护者姓名换成“AI政见”，`--check` 只比对（见 `shared/glossary/README.md`） |
| `inbox.py` | 补采收件箱：`check` 检查 `inbox/codex/<任务名>/`，`import` 把名册、事实包、中文名、照片并进人物库（格式见 `inbox/README.md`） |
| `import_portraits.py` | 照片：本机已有照片，或 `--from-csv` 补采的照片链接，裁成 3:4 缩到 600×800 进 `data/portraits/`（只裁切不换底；名单里的人用仓库外做好的蓝白渐变底标准像） |
| `lint/cadre_lint.py` | 干部简历写法检查 CR01–CR15 |
| `installer/build.py` | Windows 安装包：取 python.org 嵌入版 Python，组装程序和附带数据，再由桌面外壳（`desktop/`，Electron）的 electron-builder 打出 `dist/zuzhibu-<版本>-win-x64-setup.exe`（`installer/icon.py` 画图标），见 `docs/INSTALL.md` |
| `installer/fonts.py` | 随软件分发的字体：思源宋体、朱雀仿宋、霞鹜文楷 GB 从官方发布下载、核对 SHA-256，附许可证全文（构建时调用，字体不进仓库） |
| `installer/smoke.py` | 后台试装：静默安装到工作目录、逐项核对装好的内容、隐藏窗口启动并核对本地服务、只开一个实例、退出和崩溃时本地服务随之结束、启动时崩溃的自救、升级（数据和设置保留）与防降级、卸载；`--shell` 不安装、只试外壳的启动自救 |
| `installer/contents.py` | 逐项核对装好的程序：程序和界面、附带数据、标准像尺寸和登记、字体和许可证、不带账号和密钥 |
