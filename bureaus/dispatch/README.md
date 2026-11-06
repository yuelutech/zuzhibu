# dispatch（一局·干部调配局）

人事动态。维护者每月更新一次（用户可用自己的 AI API 随时更新）：采集线索 → 研究 → 核查 → 写回人物记录。规则见 [docs/PRODUCT.md](../../docs/PRODUCT.md) 第四、七节。

## 文件

| 文件 | 做什么 |
|---|---|
| `collect.py` | 免费线索：congress-legislators 公开数据与人物库四局对比（离任、新就任）；名册里任职起始日期落在窗口内的人 |
| `research.py` | 两次联网会话（本机 Codex，或使用者自己的 AI 接口 `shared/backends/ai_api.py`）的提示词和输出 schema；核查放行规则；来源摘录的程序核对 |
| `run.py` | 命令行入口：分组研究、逐条核查，一组一组写回，写过程记录和报告；立即更新按 6 步报进度、可停止；每月更新和立即更新写回之后刷新商界市值 |
| `resumes.py` | 获取简历：名单上的职务换了人，给新任查标准简历和官网照片（两次会话，照写法纪律放行，程序核对官网照片） |
| `marketcap.py` | 商界名单的市值：一次联网会话查齐 `data/rosters/market-caps.json` 里全部上市公司在同一交易日的收盘市值（每家带来源网址和原文摘录），全部对得上才写回；名单页据此分“市值万亿美元以上/以下”、层内从大到小排。一人兼几家上市公司的每家一行（马斯克：特斯拉、太空探索技术公司），按其中市值最大的一家排，各家分开和上次比。`python -m bureaus.dispatch.marketcap --data <数据目录> [--backend api] [--as-of 2026-10-01]`；每月更新和立即更新写回之后，在第 6 步里调 `marketcap.refresh()`（用同一个后端） |
| `daily.ps1` | 计划任务包装：一局更新、并入四局重核结果、在 main 上提交数据并推送 |

## 用法

在仓库根目录，本机已安装并登录 Codex CLI：

```
python -m bureaus.dispatch.run --daily                                   # 近 3 天
python -m bureaus.dispatch.run --since 2026-08-01 --until 2026-10-17     # 指定窗口（回溯）
python -m bureaus.dispatch.run --daily --groups congress,gov --jobs 2    # 只跑部分组
python -m bureaus.dispatch.run --daily --dry-run                         # 只采集线索、写提示词
python -m bureaus.dispatch.run --inbox inbox/codex/<任务名>/events.json  # 补采的动态：不开研究会话，只核查和写回
```

使用者用自己的 AI 接口（设置里填的大模型接口和密钥、自己的额度；软件“立即更新”就是这样调的，不用 Codex）：

```
python -m bureaus.dispatch.run --backend api --since-last --jobs 1 --data <本机数据目录> --progress <进度文件> --stop-file <停止文件>
python -m bureaus.dispatch.run --backend api --since 2026-10-18 --until 2026-10-21 --data <本机数据目录> --journal <本机更新目录>
```

| 参数 | 作用 |
|---|---|
| `--groups` | 资料库五栏：whitehouse（白宫）、ai（商界）、congress（国会）、military（国防）、justice（司法），默认全部；gov 是白宫、国防、司法三组 |
| `--jobs` | 同时跑几组，默认 3；用 AI 接口时软件给 1，少碰接口限流 |
| `--since-last` | 窗口从上次更新的截止日到今天（取 `meta.json` 的 `daily`、`user_update` 和本机更新目录里最晚的截止日；最长 62 天，取不到就 31 天）；窗口超过 7 天时研究会话最少检索 40 次 |
| `--backend` | codex（默认，本机已登录的 Codex CLI）或 api（使用者在设置里填的大模型接口） |
| `--settings` | `--backend api` 时读哪个设置文件，默认设置页保存的 `%APPDATA%\zuzhibu\settings.json`；密钥只从这里读，不进命令行 |
| `--journal` | 本机更新目录，默认 `%LOCALAPPDATA%\zuzhibu\local`（`--backend api` 时总会用）：写回的动态、取到的标准简历、名单变动另记一份，维护者数据换新后据此重放（`zuzhibu/localupdates.py`）；有本机更新目录才做获取简历、名单换人 |
| `--progress`、`--stop-file` | 进度文件（6 步，`zuzhibu/progress.py`）和停止文件；停止文件出现后不再开新会话，已核实的照样写回 |
| `--no-probe` | `--backend api` 时不先检测接口能不能联网检索 |
| `--model`、`--effort` | 只对 Codex，默认 gpt-6.1-sol、medium；AI 接口的模型在设置里填 |
| `--timeout` | 每次会话限时，分钟，默认 30 |
| `--origin` | daily、backfill、user-api（`--backend api` 的默认）、user-codex（软件里用本机 Codex 立即更新），写进动态记录 |
| `--review` | 人工核查单，只配 `--inbox`：程序核查没过的不开核查会话，按核查单里打开来源逐字段核对的结论放行（放行规则同核查会话：核查单没列的、单里没打开过的来源、没附来源的更正都不算），`verification.method` 记 `manual`。Codex 没额度时用（2026-11-06 起） |
| `--no-excerpt-check` | 不另取网页核对摘录；补采动态也不做程序核查，全部开核查会话 |

## 流程

1. 线索：免费来源，程序抓取，不调模型。线索只是提示，写回前一律经过研究和核查。
2. 研究会话：联网逐条取证，输出结构化动态（类别、原职务、新职务、宣布和生效日期、其他现职、新任命、来源与原文摘录）。会话隔离与 reverify.py 相同：不读本机 config.toml，只读沙箱，只留联网检索。
3. 核查（2026-10-21 起和补采动态一样逐条核）：先剔除重复等；再程序核查（`research.precheck`）；过不了的一次开一个核查会话只核一条，重新打开来源逐字段核对，会话里没打开过的来源不算核实。已入库的动态可用 `--recheck <动态编号>` 按现行规则重核（不重新写回）。
4. 放行（程序，fail-closed）：核查没联网、没给结论、有定不了正确值的字段，整条搁置；来源打不开的不算核实，另有核实来源撑住各字段就删去它，不整条搁置；核查给出的更正要附来源（会话里打开过的网页和能看出正确值的原文摘录；改职务名称的要一级来源），没附来源的不采纳，采纳的更正来源记进动态来源表（编号 cN，supports 写它撑住的字段）；离任类“没有其他现职”“没有新任命”两项核查确认了才保留。
5. 写回：`zuzhibu/personnel.py`，现任职务、履历、更新时间、所属局；转入七局要过三项核对。生效日还没到的宣布记为拟任（`planned_roles`），不改现任；拟任到期后，下次更新的线索里会列出来（`collect.planned_candidates`），交研究会话复核。
6. 记录：核实的动态写进 `data/events/<年-月>/`；搁置的只留在 `output/dispatch/<时间>/held.json`；报告在同目录 `report.md`。

补采的动态（`--inbox`）不开研究会话，直接按下面的顺序走（每月更新研究会话出来的动态也一样），核查方式记进动态的 `verification.method`：

1. 先剔除：库里已有的算重复（同一人、同一类别、日期相差 7 天内或宣布日相同；同一人、同一天、同一机构的，类别不同也算）；没有原职务也没有新职务的（撤回提名、委员会表决，或职务错写在 `new_appointment`）、人物编号和英文名对不上的搁置；二局范围外的搁置。
2. 程序核查（`research.precheck`，不调模型，`method` 记 `excerpt`）：逐条取来源网页，一级来源（`zuzhibu/sources.py`）、网页取得到、摘录逐字找到的算核实来源；人物的姓、各职务的英文实词、类别字样在核实来源的摘录或网页标题里，每个日期都在核实来源的网页上，才放行。离任类“没有其他现职”“没有新任命”程序核不了，按未查清写回，不转七局。
3. 过不了的开核查会话（给了 `--review` 时改按人工核查单，`method` 记 `manual`），一次一条（`method` 记 `codex`，`precheck` 记程序核查没过的原因）：会话没联网的整条搁置；说核实了、会话里却没打开过的来源不算核实。

使用者自己的 AI 接口（`--backend api`）：提示词、输出 schema、放行和写回与上面完全相同，只是两次会话改由 `shared/backends/ai_api.py` 直连使用者的接口（OpenAI Responses 接口的 web_search 工具，或 Anthropic Messages 接口的 web_search、web_fetch 工具），按次计入使用者自己的账户。

1. 读设置：接口类型没有联网检索的（只能对话）、没填密钥的，退出码 2，不联网。
2. 检测：问一个必须检索才答得上的小问题（约检索 1 次）；模型没有检索、密钥无效、额度不足、检索工具被拒，退出码 2，不开研究会话。
3. 重放：本机更新目录里以前写进的动态，先按 `zuzhibu/localupdates.py` 的规则重放到数据上（维护者数据换新后，维护者已有的以维护者为准、有更晚动态的标待核）。
4. 研究、核查、放行、写回同上；会话里没有联网动作的结果作废、全部搁置。`verification.method` 记 `api`，`by` 写明是使用者自己的哪类接口，`model` 记模型名。
5. 获取简历、名单换人（`zuzhibu/rosterchange.py`，规则见产品说明第七节）：核实的任免落地了、名单上同一岗位是别人的，在这批写回之前判断换不换；要上名单的新任没有完整简历卡或照片的，`resumes.py` 取标准简历和官网照片。名单、人物记录、照片、动态一组一起写（`rosterchange.commit`，出错全部退回）；取不到、停下了、写入出错的整组不写，记进本机更新目录的 `pending/`，下次接着办。
6. 记录：写回的动态、取到的标准简历、名单变动另记一份到本机更新目录；状态记在 `meta.json` 的 `user_update` 和本机更新目录的 `state.json`，不顶替维护者的 `daily`；有组失败、停下了、接口出错时截止日不往前推，下次把这段重查。结果只写 `--data`，不提交、不推送。会话的过程记录（提示词、请求体、响应原文）在 `--runs` 下，密钥不在其中。

进度（`--progress`）按 6 步报：连接接口、查询公开信息、深度思考（按组）、逐条核实（按条）、获取简历、更新简历；提示语照维护者审定的文案稿，集中在 `zuzhibu/progress.py`。停止（`--stop-file`）：不再开新会话，接口上正在跑的会话取消，已核实的照样写回，报“已停止”；密钥、额度、联网检索出问题时同样停下，报“未完成”和原因。

## 计划任务

Windows 计划任务“AI政见组织部-一局每日更新”（名字沿用），2026-10-21 起每月 1 日 08:00 运行 `daily.ps1`（用户登录时运行），窗口从上次更新的截止日到当天。四局夜间重核“AIpolis-人事三局重核”同日起暂停。

## 退出码

0 完成；2 Codex 没找到或未登录，或 AI 接口没配好、不能联网检索、密钥无效或额度不足；3 有组失败；4 另一次更新正在运行；5 补采动态文件不合格式；6 使用者停止。
