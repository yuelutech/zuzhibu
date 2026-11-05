# zuzhibu

主程序包，只用标准库。

| 模块 | 做什么 |
|---|---|
| `bureaus.py` | 七局定义；机构到局的对照规则，按主要职务判定所属局 |
| `store.py` | 人物库 `data/people/`、动态库 `data/events/`、`data/meta.json` 的读写与查找 |
| `resume.py` | 人物记录 → 干部简历稿件对象（首段、现任段、履历）、纯文本、Markdown；输出资格 |
| `personnel.py` | 已核实动态写回人物记录：现任、履历、更新时间、所属局；转入七局的三项核对 |
| `localupdates.py` | 本机更新：“立即更新”写进的动态、取到的标准简历、名单变动另记一份（`%LOCALAPPDATA%\zuzhibu\local`）；维护者数据换新后按合并规则重放（同一件事以维护者为准；维护者还没有的重放；维护者有更晚变动的不盖）；数据分发换快照时调 `after_sync()`。`python -m zuzhibu.localupdates status`、`merge --data 目录` |
| `rosterchange.py` | 名单换人：同一岗位、换人证据分开判断；名单、人物记录、照片、动态一组一起写（`commit`，出错全部退回） |
| `progress.py` | 立即更新的进度文件和停止文件；6 步提示语和结束提示语（维护者审定的文案） |
| `importer.py` | 各局首发资料导入人物库；内容没变的记录不重写；一局写回过的记录不覆盖 |
| `server.py` | 本地接口与静态文件，只监听 127.0.0.1；POST 只收同源 JSON |
| `datasync.py` | 数据分发：维护者重算 `data/manifest.json`；软件联网按清单增量更新、逐个校验、快照切换，失败不动本机数据 |
| `desktop.py` | 桌面版：安装信息（dist.json）、跟随外壳退出（`--parent-pid`）；从仓库直接运行时的单实例、浏览器应用窗口、关窗退出、日志、出错弹窗。正式的桌面外壳在 `desktop/`（Electron） |
| `__main__.py` | 桌面启动：`python -m zuzhibu`（安装版数据放 `%LOCALAPPDATA%\zuzhibu`，启动时和每天一次联网检查数据） |
