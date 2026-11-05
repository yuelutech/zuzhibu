# 补采收件箱

外部会话（本机 Codex 等）补采的名册、简历、人事动态、中文名、照片，先放这里，维护者用 `tools/inbox.py` 检查后入库。人物库 `data/` 只由入库程序和一局写回改，补采方不直接改。

各任务的范围和要求在 `inbox/tasks/<任务>.md`。

## 规矩

1. 每个任务一个文件夹：`inbox/codex/<任务名>/`。只在自己的任务文件夹里写文件，仓库其他文件一律不改。
2. 不提交、不切分支、不推送。几个任务同时跑，各写各的文件夹，互不干扰。
3. 交付前跑自查，错误清零：

   ```
   python tools/inbox.py check inbox/codex/<任务名>
   ```

   Python 用本机设置里的那个（`%APPDATA%\zuzhibu\local.json` 的 python，见 `zuzhibu/local.py`；只用标准库，3.11 以上的都行），在仓库根目录运行。
4. 文件夹里写 `README.md`：做了哪些人、查了哪些来源、哪些没查到或拿不准、为什么。
5. 只写查到来源的事实。查不到的留空、写进 `unknowns`，不凭记忆补，不借同名人的资料。

## 文件格式

只交任务要求的那几种文件。CSV 一律 UTF-8，第一行是列名。

### roster.csv：库里没有的现任人员

| 列 | 说明 |
|---|---|
| person_id | 英文名小写，空格和标点换成连字符：`Marco Rubio` → `marco-rubio` |
| bureau | `ai`（二局）、`whitehouse`（三局）、`military`（五局）、`justice`（六局） |
| english_name | 官方页面上的英文全名 |
| position_en、organization_en | 现任职务和机构，照官方页面写英文 |
| since | 现任职务的到任日期 `YYYY-MM-DD`，只知道月或年的写 `YYYY-MM`、`YYYY` |
| official_bio_url | 官方简历页（.gov、.mil、本机构官网），必须是一级来源 |
| official_portrait_url | 官方标准照链接，没有就空着 |
| notes | 备注 |

库里已有的人（`python tools/gaps.py` 里能查到的）不进 roster.csv。

### resumes/<编号>.json：干部简历事实包

一人一个文件，文件名是人物编号（库里已有的用 gaps.py 里的编号，新人用 roster.csv 的 person_id）。格式：

```json
{"schema_version": "1.0", "date": "2026-10-18", "items": [ { ……一份 cadreItem…… } ]}
```

- 字段定义：`shared/schema/bundle.schema.json` 的 `cadreItem`。每个字段都要有，没有值的填 `null` 或 `[]`。
- 怎么写：`shared/skills/cadre-resume/SKILL.md`，规则原文是 aipolis 的 `rules/cadre.md`（第 9 节美国附则）、`party_join.md`（美国政要入党时间）、`research.md`（取证和核对），本仓库不带副本，在维护者本机设置 aipolis_rules 给的目录里。
- 样例：`bureaus/personnel-congress/data/verified/A000055.json`（国会议员，重核通过）；`data/people/jensen-huang.json` 的 `resume` 字段（企业家，金标）。
- 要点：
  - `subject_id` 等于人物编号，`line` 是 `cadre`，`country_profile` 美国人写 `us`，其他国家的人写 `other`。
  - 姓名、性别、国籍、出生年月、参加工作时间五项必有；出生年月、参加工作时间写到月，来源没有月份的按 cadre.md 第 3 节第 3 条推测，`inferred` 写 `true`，`basis` 写规则和依据。
  - 每个事实的 `source_ids` 指向 `sources` 里的来源；来源要有 `url`、`title`、`published_at`、`checked_at`（打开核对的日期）、`excerpt`（原文摘句）、`tier`（1 官方网站、证券交易委员会申报；2 主流媒体、领英；3 维基百科）。
  - 现任职务要有一级来源，`appointment_state` 写 `正式`、`代理`、`提名人选` 等。
  - 中文名、机构名、职务名照 `shared/glossary/` 三张译名表；表里没有的人名另报 names.csv。
  - 已离任、已换岗的，事实包写现在的情况，同时在 events.json 报这条人事动态。

### events.json：人事动态

格式同一局研究会话输出：`bureaus/dispatch/research.py` 里的 `RESEARCH_SCHEMA`（每条动态的字段见 `EVENT_SCHEMA`）。

```json
{"events": [ {
  "candidate_id": null, "kind": "leave", "person_id": "katie-zacharia",
  "name_en": "Katie Zacharia", "name_zh": "凯蒂·扎卡里亚",
  "announced_at": "2026-10-16", "effective_at": "2026-10-16",
  "ended_roles": [{"organization": "白宫", "role": "新闻秘书", "organization_en": "The White House", "role_en": "Press Secretary",
                   "start": null, "end": "2026-10-16", "concurrent": false, "sector": "politics", "source_ids": ["s1"]}],
  "new_roles": [], "other_current_roles": [], "new_appointment": null, "new_appointment_checked": true,
  "headline_zh": "凯蒂·扎卡里亚卸任白宫新闻秘书", "summary_zh": "……",
  "sources": [{"id": "s1", "url": "https://www.whitehouse.gov/……", "title": "……", "publisher": "白宫", "published_at": "2026-10-16", "excerpt": "……"}],
  "bureau_hint": "whitehouse"
} ], "checked_candidates": [], "notes": "" }
```

- `kind`：appoint 任命、leave 离任、transfer 调任、acting 代理、death 逝世、nominate 提名、confirm 确认（参议院批准等）、announce 宣布。
- 库里有的人 `person_id` 填编号，库外的人填 `null`。
- 离任、逝世要查明此人还有没有其他现职（`other_current_roles`，没有就填 `[]`）、有没有公布新任命（`new_appointment`，查过没有就填 `null` 并把 `new_appointment_checked` 写 `true`）；查不清的照实写 `null`、`false`，此人就不转七局。
- 入库另走一局核查：`python -m bureaus.dispatch.run --inbox inbox/codex/<任务名>/events.json`，另开会话逐条核对来源后才写回。

### names.csv：中文名

列：`person_id, english_name, name_zh, basis, source_url`。只报译名表 `shared/glossary/译名表_v1.4_person.csv` 里没有的人。依据优先新华社、新华网、人民日报、人民网的用例，其次《世界人名翻译大辞典》的译音规则；华裔、韩裔有本人汉名的用本人汉名。规则见同目录 `译名表使用说明.md`。入库后记进 `shared/glossary/补充译名_person.csv`，待审。

### portraits.csv：照片链接

列：`person_id, image_url, source_page, license`。只收官方标准照（本机构官网、.gov、.mil 的领导页或新闻稿），不收新闻现场照、不收生成图。`license` 开头写结论，冒号后写依据：`公有领域`（美国联邦政府作品）、`CC BY …`（写明作者）、`待核`、`版权保留`，例如“公有领域：国务院官网部长标准照”“版权保留：公司官网新闻图片，未见开放许可”。转公开时只留公有领域和 CC BY 的（`docs/公开前检查.md` 第十节）。入库时程序下载原图，裁成 3:4 缩到 600×800；名单里的人另在仓库外做蓝白渐变底标准像。
