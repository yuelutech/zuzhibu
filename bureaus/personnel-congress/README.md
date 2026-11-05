# personnel-congress（四局·国会干部局，原人事三局）

国会议员资料库。

- 输入：Care 历史项目 ceo-profiles 的 538 人名册与简历稿，按 bioguide 编号对应。原料 2026-10-21 公开前移出仓库（真实人物的 AI 初稿和别的项目的工作说明），留在维护者本机素材库和私有仓库归档里；导入已做完，结果在 data/。
- 分级：T1 60 份逐人取证的金标（gold），核实后可以发布；T2 478 份 Codex 初稿（draft），只作底稿，不发布。
- 输出：data/ 下每人一份事实包、名册索引和导入报告；data/verified/ 是 draft 重新取证的结果（reverify.py）。
- 依赖共享层：glossary、skills/cadre-resume、schema、portraits、render/card_3x4。
- v0.1：不在范围内；v0.2 导入（本目录的 import_ceo_profiles.py）。

## 导入脚本 import_ceo_profiles.py

在仓库根目录运行，只用 Python 标准库，不联网：

```
python3 bureaus/personnel-congress/import_ceo_profiles.py --date 2026-10-16
python3 bureaus/personnel-congress/import_ceo_profiles.py --ids A000055 N000147
python3 -m unittest tests.test_import_ceo_profiles
```

参数：`--source` 原料目录（原料已移出仓库，重跑时给本机目录）、`--schema`（默认 shared/schema/bundle.schema.json）、`--out`（默认 data/）、`--date` 事实包日期（默认今天，提交的输出用固定日期，重跑才能不出差异）、`--ids` 只转这几份、`--quiet`。全部校验通过退出码 0，否则 1。

输出：

- data/bundles/<编号>.json：每人一份事实包 `{schema_version, date, items: [cadreItem]}`。item 的 id 是 `cadre-<编号小写>`，subject_id 是编号小写（president、vice-president 原样）。
- data/index.csv：名册索引，含分级、州、选区、党派、批次、校验结果和几个关键字段。
- data/report.md、data/report.json：校验通过率、字段转换统计、留给本机复核的清单。

### 分级

底稿有 career、education、party_join 等逐事实结构、没有 `provenance: codex_normalized` 的是 T1 精做稿，tags[0] 写 `gold`；其余写 `draft`。import/ceo-profiles/batches/TIERING.md 的 T1 名单只作对照：名单列出但底稿仍是 Codex 初稿的 6 人（B001261、C000880、C001035、D000563、W000779、W000802）按底稿标 draft，写进报告。draft 不发布。

### 转换规则

| 事实包字段 | T1 精做稿 | T2 Codex 初稿 |
|---|---|---|
| person.name、gender、birth_date | 底稿值，来源连引文照录 | 底稿值，来源是 congress-legislators 数据集 |
| person.nationality | 一律"美国"（渲染时写"美国人"），来源同性别 | 同左 |
| person.birth_place | 底稿 birthplace 的州＋市 | null，首段的写法记入 unknowns |
| person.work_start | 只写年；底稿按旧规则推定的月份去掉，有确切月份的保留；inferred=true，rule=first_full_time_job | 时间线首个非学习条目的起始年，无来源；首个条目是军职的记入 unknowns 待定写法 |
| person.work_start_action | 底稿写入伍、受衔入伍的填"入伍" | "参加工作" |
| person.education、degree | 最高学位，写成"学校＋专业＋学位"；联合学位只取最高一个；学历取底稿 xueli | 从时间线的学习条目推出，无来源 |
| person.party | 党派代码 → 美国共和党、美国民主党、无党派人士；来源是现任公职的来源 | 同左，来源是数据集 |
| person.party_joined_at | 按 rules/party_join.md：只保留底稿有锚点且有来源的；年月取锚点事件本身的日期，底稿按旧规则推定的月份去掉，锚点文字里明写同年同月的保留；inferred=true，basis 写"第N级锚点：…；来源：…"，inference.rule 按级别填 | null；发布稿的入党句进 excluded，缺口进 unknowns |
| current_roles | 公职按类别、州、选区生成，如"美国联邦参议员（俄亥俄州）"，sector=politics，role 留空；committees_119 和 current_position 里的委员会职务 sector=committee、concurrent=true，委员会名前加参议院或众议院，主席、资深成员、委员按原文（少数党首席成员统一写资深成员）；领袖、党鞭、议长等 sector=politics | 公职同左；现任段里的委员会和领导职务同左，无来源 |
| careers | education → kind=study，高中不入；入学年是底稿推定的标 inferred：本科减四年用 graduation_minus_four，其余按学制倒推的用 degree_duration 并在 basis 注明待复核。career → kind=work，org_zh、title_zh 分别进 organization、role，顿号连写的多个职务进 roles；"其间"字符串解析成 annotations，没有年份的记入 unknowns | 时间线每行一条：学习行解析出学校、专业、学位；工作行整句进 organization，role 为 null；"（其间：…）"解析成 annotations；全部无来源 |
| conflicts | 底稿 conflicts 的 a、b、adopted | 无 |
| unknowns | 底稿 missing、未能结构化的括注、入党和出生地缺口 | 入党时间、出生地、履历无来源、底稿 flags |
| excluded | 去掉的推定月份、高中经历、股票披露、军旅记录 | 发布稿入党句、参加工作月份 |
| 不转换 | disclosure、military、codex_changes、codex_diff、photo | 同左 |

机构名和学位名去掉英文原名一类的括注（University of X、J.D.），保留通行缩写（MIT、ACLU）。州名用底稿和译名表的写法（爱荷华州、佐治亚州、得克萨斯州、俄克拉何马州、罗得岛州）。每份事实包自带来源表，同一链接同一引文只记一次；来源 checked_at 取底稿的 updated。

### 留给本机

- 59 份 gold 的入党时间：月份按 party_join.md 第二节重核；来源只有维基百科的 44 份要补原始来源（四.8）。清单在 data/report.md。
- 478 份 draft：履历、学历、出生地无来源，入党时间置空，工作条目的单位与职务没有拆开；用 reverify.py 逐人重新取证，见下节。
- 译名对照 shared/glossary 人名表，尤其是 36 份译名来源为 generated 的。
- 校验只按 schema。写法 lint（任务 01）、渲染（任务 03）另做。

## 重新取证 reverify.py

把 478 份 draft 逐人重新取证。每人两次本机 Codex 会话，照 aipolis research.py 的做法：

1. 研究：输入此人 data/bundles/<编号>.json（只作线索）和本机 aipolis 规则目录（`--rules-dir`，或本机设置 aipolis_rules）里 research.md 的节选（证据与日期、干部字段纪律、两次会话和放行条件）、cadre.md 第 1–4 节、party_join.md 全文，维护者姓名换成“AI政见”；联网逐字段取证，按 shared/schema/bundle.schema.json 输出新事实包。
2. 整理（脚本，不调模型）：标识字段和中文译名照底稿；剔除维基百科和非公开网址的来源；条目内日期、学位没写来源的沿用所在条目的来源；没有来源或推算标记不合规的事实置空或删条，记入 unknowns（推算只认四类，入党时间的 basis 要写第几级锚点和链接）；删去没被引用的来源。改动逐条写进 review.md。
3. 独立核查：另开会话重新联网打开来源，逐字段核对，返回已核实的字段路径、已确认的来源和来源记录问题。事实层面照 aipolis fail-closed：没明确通过、有不过的字段、没覆盖全部字段路径，整项搁置。来源记录按确定规则处理：打不开（多为超时）或对某字段不支持的来源，引用它的字段都已核实、且另有已核实来源的，删去这层引用，有一个字段只靠它就搁置；发布日期、标题、摘录与网页不符的，用核查给的更正值（摘录没给更正值的按不支持处理）。删改逐条写进 review.md。

会话隔离：`--ignore-user-config`（不读 ~/.codex/config.toml 里的邮箱 MCP、插件和 danger-full-access），只读沙箱，`web_search="live"`，`--ephemeral`，关掉 shell、连接器、插件、hooks、记忆、多代理、浏览器；模型和 effort 由命令行给。Code Mode 不能关（Codex 0.163 的联网检索经它调用）。~/.codex/AGENTS.md 的写作规则仍会带进会话。研究或核查会话没有联网检索事件的，结果作废。

### 用法

在仓库根目录：

```
python bureaus/personnel-congress/reverify.py --limit 10
python bureaus/personnel-congress/reverify.py --only A000055 A000148
python bureaus/personnel-congress/reverify.py --resume --until 07:30
python bureaus/personnel-congress/reverify.py --dry-run --limit 1
```

| 参数 | 作用 |
|---|---|
| `--limit N` | 最多处理 N 人（在 --only、--resume 筛过之后取前 N 人，按编号顺序） |
| `--only 编号…` | 只处理这些编号，空格或逗号分隔；gold 跳过 |
| `--resume` | 跳过进度表里已是 done、held 的人；failed 重跑 |
| `--redo-held` | 只重跑进度表里是 held 的人 |
| `--effort` | model_reasoning_effort：low、medium（默认）、high、xhigh、max |
| `--model` | 默认 gpt-6.1-sol |
| `--codex` | codex.exe 路径；默认找 PATH，再找 %LOCALAPPDATA%\OpenAI\Codex\bin\ 下最新的（桌面端更新后哈希目录会变） |
| `--timeout` | 单人每次尝试的时限，分钟，默认 15 |
| `--jobs` | 同时处理几人，默认 1 |
| `--until HH:MM` | 到点不再开始新的人，已开始的做完 |
| `--max-failures` | 连续失败几人就停，默认 3（额度用完、断网、掉登录时止损） |
| `--date` | 事实包日期和来源查阅日期，默认今天 |
| `--out`、`--runs` | 输出目录（默认 data/verified）、过程记录目录（默认 output/personnel-congress/reverify） |
| `--dry-run` | 只写研究提示词，不调 Codex |

失败自动重试一次：研究已成功的只重做核查。核查搁置全因来源打不开（多为超时、403）的，另开会话再核查一次。整批跑完后用 `--redo-held` 把 held 的人重跑一遍（与 `--resume` 不同时用）。

### 输出

- data/verified/<编号>.json：新事实包。tags[0] 写 `reverified`（核查通过）或 `held`（搁置，留给人工改），其余 tags 照底稿。failed 不写。
- data/verified/<编号>.review.md：状态、耗时、检索次数、入党锚点；搁置原因；改了什么（个人信息逐字段对照、现任、新旧履历、研究稿整理记录）；来源表（核查是否确认、来源记录问题）和核查后的更正；unknowns、excluded、conflicts；过程记录路径。
- data/verified/进度.csv：每人一行：bioguide、姓名、状态（done、held、failed）、耗时秒、检索次数（研究加核查的 web_search 事件数）、入党锚点级别（1–5、无党派、空）、尝试次数、研究秒、核查秒、研究检索、核查检索、输入token、输出token、完成时间、说明（搁置或失败原因）。表被 Excel 打开时写不进去，下一人完成时补写。
- output/personnel-congress/reverify/：reverify.log；每人一个目录，内有每次调用的提示词、JSONL 事件流、结果和 summary-*.json。

reverify 是模型两次会话的结果，不等于人工终审（research.md 两次会话一节）；done 也要看 review.md 抽检，审签后怎么转正另定。draft 原件 data/bundles/、import/ 原料和 gold 都不动。

### 前 10 人实跑

2026-10-17，A000055 至 A000381，gpt-6.1-sol，effort medium，2 路并行。

| 轮次 | done | held | failed | 平均耗时 | 平均检索 |
|---|---|---|---|---|---|
| 第一轮（初版提示词） | 4 | 6 | 0 | 6.2 分钟 | 23 次 |
| 第二轮（现行提示词） | 6 | 4 | 0 | 5.8 分钟（中位 5.8，最长 7.6） | 23 次（研究 17、核查 6） |
| 第二轮 held 4 人用 --redo-held 重跑 | 4 | 0 | 0 | 6.5 分钟 | 25 次 |

最后 10 人全部 done，入党锚点：第 1 级 1 人、第 2 级 2 人、第 3 级 5 人、第 4 级 1 人、未找到 1 人。

- 第二轮：研究平均 257 秒、核查 91 秒；10 人墙钟 29.6 分钟。每人约 220 万输入 token（大部分是缓存）、1.3 万输出 token。
- 第一轮 held 的主因是研究会话只在搜索结果里看到网页就引用：每人真正打开网页 0 至 8 次（平均 3.8），引用的来源却有 10 至 25 个，核查打不开或原文没写。现行提示词要求逐个打开、原文没写的细节不写；研究会话仍多半只看搜索结果就引用（review.md 逐人列出），靠核查逐个重开把关。第二轮 held 4 人：3 人的某个字段只靠一个打不开的来源（FEC 数据页超时、403），1 人有军衔、职务、月份在打开的原文里找不到；重跑时研究换了能打开的来源，4 人都通过。
- effort 保持 medium：held 不是推理不够，而是来源打不开和取证纪律；medium 的入党锚点判断（第 1 至 4 级、改党籍沿革、非党派选举排除）已经合规。

总时长估算：每人约 6 分钟；478 人顺序跑约 46 小时，2 路并行约 24 小时（按实测墙钟）；第一遍约四成 held，--redo-held 再跑约 9 小时；合计约 33 小时，每晚 8 小时约 4 晚。全部跑完约 10 亿输入 token，ChatGPT 账号的 Codex 额度可能撑不住一晚跑这么多：额度用完时 Codex 报错，连续 3 人失败整批停下，次晚 `--resume` 接着跑。

### 夜间整批

计划任务“AIpolis-人事三局重核”每晚 23:30 跑 `reverify_night.ps1`：07:00 后不再开新的人，每晚最多 40 人。Python 和 aipolis 规则目录读本机设置（`%APPDATA%\zuzhibu\local.json` 的 python、aipolis_rules，见 `zuzhibu/local.py`）。手动跑一晚，或只写提示词试一次：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File bureaus\personnel-congress\reverify_night.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File bureaus\personnel-congress\reverify_night.ps1 -DryRun
```

挂 Windows 计划任务（每晚 23:30 开始；9 小时兜底停止）：

```powershell
Register-ScheduledTask -TaskName 'AIpolis-人事三局重核' -Trigger (New-ScheduledTaskTrigger -Daily -At 23:30) -Settings (New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 9)) -Action (New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File "<仓库目录>\bureaus\personnel-congress\reverify_night.ps1"')
```

- 默认在用户登录时运行，锁屏可以，注销不行；要注销也跑，在任务计划程序里改为“不管用户是否登录都要运行”。
- 任务负责唤醒电脑；运行期间脚本让系统不因空闲睡眠，跑完恢复。
- 早上看 data/verified/进度.csv 和 output/personnel-congress/reverify/reverify.log。任务“上次运行结果”为 0x1 表示连续失败停下，查 Codex 登录、额度或网络。
- 全部 draft 跑完后，把命令里的 `--resume` 换成 `--redo-held` 再跑一晚。
- 2 路是实测值；3 路以上没测，额度消耗同比增加。
