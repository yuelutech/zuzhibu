# 三局·内阁与内阁级官员（2026年10月版）

任务名 `sanju-neige`，结果只写进 `inbox/codex/sanju-neige/`。文件格式和规矩见 `inbox/README.md`。

## 范围

截至 2026-10-18 下列职位的现任者。正式、代理都算；已提名未到任的，现任者照收，提名人选另报 events.json（nominate）。

1. 总统、副总统：库里已有金标（`donald-trump`、`jd-vance`），只核对现任状态，不重写简历。
2. 内阁各部部长（国防部、司法部归五局、六局，不在本任务）：国务院、财政部、内政部、农业部、商务部、劳工部、卫生与公众服务部、住房和城市发展部、运输部、能源部、教育部、退伍军人事务部、国土安全部。
3. 内阁级官员：白宫办公厅主任、环境保护署署长、行政管理和预算局局长、国家情报总监、中央情报局局长、美国贸易代表、常驻联合国代表、小企业管理局局长。
4. 上面 13 个部的常务副部长（Deputy Secretary），以及行政管理和预算局副局长。

## 资料线索

- 库里已有谁：`python tools/gaps.py --bureau whitehouse`；五局、六局也查一下，避免重复建人（`--bureau military`、`--bureau justice`）。
- CEO 雷达 2026 年 6 月整理的内阁资料，23 人，有中文简历稿、时间线、来源链接、照片来源。人事已有变动，只作线索，每项重新核对：
  `<素材库>\Care历史项目\补要解包_2026-10-16\资料\D\ChatGPT\ai-ceo-radar\research\white-house-cabinet\cabinet-candidates.json`（`<素材库>` 是本机设置 library 的目录）
  （同目录 `photo-index.json` 是照片来源索引）
- 一级来源优先：白宫 whitehouse.gov、各部官网领导页和新闻稿、congress.gov 的提名和确认记录、参议院表决记录、联邦公报。

## 产出

- `roster.csv`：上面职位的现任者里库里没有的人。
- `resumes/`：上面职位全部现任者一人一份（总统、副总统除外）。库里已有的用库里编号。
- `events.json`：2026-06-01 以来这些职位的任免，比如雷达资料里的人已离任、新人到任、代理、提名、确认。每条要有来源。
- `names.csv`：译名表里没有中文名的人。
- `portraits.csv`：库里没有照片的人的官方标准照。
- `README.md`：职位—现任者对照表（职位、姓名、编号、到任日期、状态：正式／代理／提名人选）；没查清的单列，写原因。

## 完成标准

`python tools/inbox.py check inbox/codex/sanju-neige` 错误为 0；README 的对照表覆盖上面全部职位。
