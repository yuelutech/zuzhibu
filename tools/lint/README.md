# lint（写法 lint）

三个 lint：干部简历 `cadre_lint.py`；讲话稿 `speech_lint.py`、新闻稿 `newswire_lint.py`（流水线默认调用，见 shared/backends/lint_hook.py），后两个共用 `genre_common.py`。都只用标准库。

## cadre_lint.py：干部简历写法 lint

按 cadre.md、party_join.md、research.md（aipolis 规则（本仓库不带副本，维护者本机有；要点见 `shared/skills/cadre-resume/SKILL.md`））检查干部简历文本。只用 Python 标准库，Python 3.10 以上。配套的 Skill 在 shared/skills/cadre-resume/SKILL.md。

### 用法

在仓库根目录运行：

```
python -m tools.lint.cadre_lint 文件或目录 [更多路径]
python -m tools.lint.cadre_lint 简历.txt
python -m tools.lint.cadre_lint output/ --format json
python -m tools.lint.cadre_lint - < 简历.txt
```

目录递归扫描，默认只收 `.md` 和 `.txt`（`--ext` 可改）。文件按 UTF-8 读，带不带 BOM 都行（`--encoding` 可改）。

退出码：

| 退出码 | 含义 |
|---|---|
| 0 | 没有违规 |
| 1 | 有违规 |
| 2 | 参数错误、路径不存在、文件不是 UTF-8、译名表读不出来 |

输出一行一条：`路径:行:列: 代码 规则名：说明：「片段」`。`--format json` 输出 `{"files", "violations", "notes", "items": [...]}`，每条含 path、line、col、code、rule、level、message、text。汇总行写到标准错误。

### 规则

| 代码 | 规则 | 查什么 | 依据 |
|---|---|---|---|
| CR01 | 叙述词 | 工作、后任、先后、从事、参与、负责、曾为、服务于、活跃于、职业生涯、帮助、协助、支持（cadre.md 清单）；另加 毕业后、随后、此后、曾任、历任、任职于 等传记腔词；"加入"后面不是政党的。"参加工作""负责人""工作人员"等固定词和译名表里的机构名不算 | cadre.md 2.6；人事三局 RULES.md 七 |
| CR02 | 期间误用 | "期间"要写"其间"；"其间"后用全角冒号，写在括号里：（其间：……） | cadre.md 2.3 |
| CR03 | 日期格式 | 起止用全角连接号"－"：1985年－1993年、2001年1月－2003年1月；在任写 1993年4月至今，"至今"前不加连接号；年份带"年"字；月份不补零；不写日；不写 2011-01-28、1993.4 一类数字日期；不用全角数字和汉字数字；不写年代、年初、月份等模糊时段；起止不颠倒；每条两行，日期行下面要有单位＋职务行 | cadre.md 3；party_join.md 二 |
| CR04 | 中英文空格 | 英文和汉字之间空一格，只空一格；不用全角空格、制表符；全角标点前后不空格 | cadre.md 2.7 |
| CR05 | 顿号合并 | 条目和"现任"句里并列的职务用顿号连接，不用逗号、斜线、半角逗号；一段只用一个"现任"；顿号合并同一单位的职务时不重复单位名；顿号不重复、不放句首句尾 | cadre.md 1.4、2.4 |
| CR06 | 未知待补字样 | 未知、待补、待定、待核、不详、未查到、暂缺等缺口词；××、XX、？、空括号、N/A、TBD 等占位符；大概、左右、可能、约＋数字等模糊词 | cadre.md 1.5；research.md |
| CR07 | 译名一致 | 对照 shared/glossary 三张表：机构用"简历写法"列，职务用"干部简历写法"列，人名用"本人汉名或通行汉名"（有值且备注未标待核）否则"新华社译名"。报：用了全称（含国名）而不是简历写法；用了"历史写法"列和用法说明"不写……"里的旧写法；英文名没有译成表中写法；人名间隔号不是"·"。`--notes` 另提示用了 status 为"待核"的写法，提示不影响退出码 | 译名表使用说明 三、四 |
| CR08 | 入党句格式 | ××××年×月加入美国××党（可只到年）；美国政党写全称"美国共和党""美国民主党"；不写党员、党籍、××党人、入党；无党派写"无党派人士"；入党句放在国籍之后、学位之前，与参加工作时间按先后排列；"无党派人士"和入党句不同时出现 | cadre.md 1.1；party_join.md 二、五 |
| CR09 | 来源与推算痕迹（附加） | 链接、来源、推算、推断、锚点、核查、维基百科、脚注编号、"据……显示"一类句子不上图 | cadre.md 4.5；research.md |
| CR10 | 五项必写 | 首段要有性别、"××籍"、出生年月、参加工作（入伍）时间，出生年月和参加工作写到月；逝世人物姓名后括注的生卒年月也算出生年月 | cadre.md 1.2 |
| CR11 | 日期粒度统一 | 履历条目（含括注日期）有的写到月、有的只到年时：缺月份的不超过 2 条或全篇四分之一（取较宽）报“全篇写到月”，超过的报“全篇只写到年” | cadre.md 3.2 |
| CR12 | 参加工作年龄 | 参加工作或入伍时不满 16 岁 | cadre.md 3.4 |
| CR13 | 履历排序与衔接 | 按开始时间排；开始相同的先结束的在前；第一条工作与首段参加工作时间一致；非学习条目重叠只作提示 | cadre.md 2.1、2.4、3.2 |
| CR14 | 曾任与履历 | 首段"曾任××"的职务要在履历里找得到；首段末的"曾任"不算 CR01 叙述词 | cadre.md 1.4、8.1 |
| CR15 | 国别附则 | `--country us`（默认）：族裔只用白人、非洲裔、拉丁裔、亚裔、原住民；`--country rw`：不写族裔和民族，现任句党职在前；`--country other` 不查 | cadre.md 第 9 节；country_rw.md 1、4 |

CR09 不在任务简报的八条里，是 research.md"PPT页面、备注和配文不含来源编号、推算标记、核查过程或未知占位"那句的另外三项，不要可以 `--ignore CR09`。

### 选项

| 选项 | 作用 |
|---|---|
| `--glossary 目录` | 译名表目录，默认 shared/glossary。目录里按文件名后缀找 `*_org.csv`、`*_title.csv`、`*_person.csv`，有几个版本取版本号最大的 |
| `--no-glossary` | 跳过 CR07。译名表读不出来时程序退出码 2，加这个选项可以只查其余各条 |
| `--select CR01,CR03` | 只查这些规则 |
| `--ignore CR09` | 不查这些规则 |
| `--allow 文件` | 放行词表，一行一个词，带 `#` 的行是注释；这些词在所有检查前被遮住，用来放过机构名里恰好含叙述词的情况 |
| `--country us/rw/other` | 国别附则（CR15），默认 us |
| `--cjk-space require/forbid/off` | 中英文之间的空格。默认 require，按 cadre.md 2.7"英文和汉字之间空一格"；forbid 查相反方向；off 不查 |
| `--ext .md,.txt` | 扫目录时收的后缀 |
| `--encoding utf-8-sig` | 文件编码 |
| `--format text/json` | 输出格式 |
| `--notes` | 同时输出提示 |
| `-q` | 不逐条输出，只给退出码和汇总 |
| `--list-rules` | 列出规则 |

Python 里直接调用：

```python
from tools.lint import cadre_lint

glossary = cadre_lint.load_glossary("shared/glossary")
for v in cadre_lint.lint_text(text, glossary=glossary):
    print(v.format())          # 或 v.to_dict()
```

### 两处取舍，请维护者确认

1. **中英文空格的方向。** cadre.md 2.7 写"英文和汉字之间空一格"，但同一条的例子（LSI Logic公司）、周受资和黄仁勋两段样例，以及译名表"简历写法"列的 34 条（Anthropic公司、xAI公司……）都没有空格。lint 按写出来的规则默认要求空格，比对译名时忽略这个空格差异，所以"Anthropic 公司"同时通过 CR04 和 CR07。如果定的是不空，改 `--cjk-space forbid`，或者把默认值改掉；SKILL.md 里的说法要一起改。
2. **译名表和 cadre.md 不一致时以译名表为准。** 例如 cadre.md 的例子写"参议院商业、科学和交通委员会"，译名表 v1.4 写"商务、科学和运输委员会"，lint 报后者。

### 已知限制

- 规则是文本层面的，不读事实包，不查内容真假。履历条目按"日期行＋内容行"或"日期 空格 内容"一行识别，首段按含"，男，""，女，"或"出生"的第一个正文行识别。
- CR05"顿号合并时不重复单位名"靠译名表里的机构名或"公司""大学"一类后缀切单位，拿不准的不报。
- CR07 只报表里有的词：旧写法只取"历史写法"列和用法说明里"不写……"的词；和标准写法只差国名、州名一类前缀的不报（加不加国名按译名表使用说明的国名规则，lint 不判断）；表里没有的英文、没有的译名不报。
- 对四局导入原料（Care 历史项目 ceo-profiles，已移出仓库）里 538 份历史稿跑过一遍：那批稿子是年份模式、半角连字符、首段顺序不同，所以 CR03、CR05、CR08 大量报出是预期的；CR07 剩下的报出是"与/和"的委员会名差异和人名音译差异，可以作为 v0.2 导入时的改写清单。

### 留给本机做

- 用 aipolis 实跑出来的简历和私有金标过一遍，校词表：叙述词的补充清单、放行词表、CR06 的模糊词。
- 接进流水线：生成后先 lint，不过就带着意见重写一次，仍不过交人工（见 docs/ROADMAP.md 做法一节）。
- 两处取舍（见上）由维护者拍板。
- aipolis 改了 rules/ 后，同步只读副本，再对一遍规则表。

### 测试

```
python -m unittest tests.test_cadre_lint
```

样本在 tests/fixtures/cadre_lint/，全是虚构人名和机构：pass/ 三份按规范写的简历应零违规，fail/one_per_rule.txt 九条规则各触发一次，glossary/ 是三张虚构译名表。另有一组用真实译名表的冒烟测试，shared/glossary 不在时自动跳过。

## speech_lint.py、newswire_lint.py：讲话稿、新闻稿 lint

照 shared/skills/cadre-speech/SKILL.md 第 7 节（S01–S30）和 shared/skills/soe-newswire/SKILL.md 第 9 节（N01–N35）参数卡，实现程序能判的条目。流水线生成后自动调用，P0 不过带着意见重写一次，仍不过标“交人工”。

### 用法

```
python -m tools.lint.speech_lint   输出目录/原文.json --source 原文.txt [--format json]
python -m tools.lint.newswire_lint 输出目录/原文.json --source 原文.txt [--agency 政见社] [--format json]
```

待查稿件可以是流水线输出的 `<原文名>.json`（读 `document`），也可以是纯文本（标题、副题、段落空行隔开）。`--source` 给英文原文时才查数字保真、篇幅比、限定词和英文夹杂里“原文有没有这个词”。退出码：0 没有 P0；1 有 P0；2 读不了文件。

Python 里：`lint_text(text, source=原文, document={"title", "subtitle", "paragraphs"}, extra={"agency": "政见社"})`，返回 `[{level, rule, message, where}]`。

### 实现了哪些条目

| 文体 | 程序判 | 留给别处或人工 |
|---|---|---|
| 讲话稿 | S01–S10、S13、S14、S16–S28、S30 | S11 专名保真、S12 译名一致由流水线的译名表检查做；S15 四字格没有词表；S29 外来引语要对照语义 |
| 新闻稿 | N01–N09、N12、N14、N15、N17–N22、N24–N34；N35 用启发式 | N10、N11、N13 由译名表检查做；N16 引语保真、N23 时态要对照语义 |

几处取舍：

- 数字保真（S10、N20）拆两级：成稿里原文没有的阿拉伯数字报 P0（编造、推算、自行换算）；原文数字在成稿里找不到对应的合并成一条 P1。月、日不比对（电头日期、星期换算都会产生）；原文的 two decades、three quarters、'27、$2.5 billion 都换算后比对；成稿里“两个”“三点”这类汉字数字只用来认“有对应”。
- 限定词（about、nearly、up to、more than……）：原文带限定词的数字，成稿对应数字前后没有“约、近、最多、超过”一类字的报 P1（讲话稿记在 S10，新闻稿记在 N35）。
- 英文夹杂（S20、N29）：连续 4 个以上英文词 P0；单个英文词在译名表写法里或在放行表（AI、GPU、Token 等）里的不报，原文里有、表里没有的报 P2（表外保留英文要进复核清单），原文也没有的报 P1。整条译名表写法（如“美国《华尔街日报》（The Wall Street Journal）”）先遮住再查。
- S01 称谓允许逗号：SKILL 第 1.2 条的“尊敬的各位嘉宾，女士们、先生们，朋友们：”带逗号，参数卡正则不带，按第 1.2 条放行。
- “美国国务院”不算 S18/N26 的“国务院”。

阈值都是参数卡的经验值，待私有金标校准（路线图第 9 步）。

### 测试

```
python -m unittest tests.test_genre_lint
```
