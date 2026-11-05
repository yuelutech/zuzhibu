# 五局·美军（2026年10月版）

任务名 `wuju`，结果只写进 `inbox/codex/wuju/`。文件格式和规矩见 `inbox/README.md`。

## 范围

截至 2026-10-18 下列职位的现任者（正式、代理都算）：

1. 国防部（战争部）部长、常务副部长；陆军部、海军部、空军部部长。
2. 参谋长联席会议主席、副主席。
3. 陆军参谋长、海军作战部长、空军参谋长、海军陆战队司令、太空作战部长、海岸警卫队司令、国民警卫局局长。
4. 11 个作战司令部司令：非洲、中央、欧洲、印度－太平洋、北方、南方、太空、特种作战、战略、运输、网络司令部。
5. 国家安全局局长。

## 资料线索

- 库里已有谁：`python tools/gaps.py --bureau military`。
- 一级来源：war.gov（defense.gov）、各军种和作战司令部官网的领导简历页（.mil）、参议院军事委员会提名记录、congress.gov。
- 军人简历的写法：入伍或授衔时间作参加工作时间（`work_start_action` 写入伍，月份按授衔月）；现军衔写进 `military_rank`；军校毕业即授衔的，毕业月就是参加工作月。官方简历常不写生日：出生年按 cadre.md 第 3 节第 3 条推（带日期的年龄陈述、本科毕业年减 22），推的要写规则和依据。
- 雷达内阁资料里有国防部长一份中文稿（只作线索）：
  `<素材库>\Care历史项目\补要解包_2026-10-16\资料\D\ChatGPT\ai-ceo-radar\research\white-house-cabinet\cabinet-candidates.json`（`<素材库>` 是本机设置 library 的目录）

## 产出

`roster.csv`（库里没有的现任者）、`resumes/`（上面职位全部现任者）、`events.json`（2026-06-01 以来这些职位的任免、提名、确认）、`names.csv`、`portraits.csv`（官方标准照）、`README.md`（职位—现任者对照表，没查清的单列）。

## 完成标准

`python tools/inbox.py check inbox/codex/wuju` 错误为 0；README 的对照表覆盖上面全部职位。
