# 六局·公检法（2026年10月版）

任务名 `liuju`，结果只写进 `inbox/codex/liuju/`。文件格式和规矩见 `inbox/README.md`。

## 范围

截至 2026-10-18 下列职位的现任者（正式、代理都算）：

1. 最高法院首席大法官和 8 位大法官。
2. 司法部长、常务副部长、副部长（Associate Attorney General）、首席政府律师（Solicitor General）。
3. 联邦调查局、缉毒局、烟酒枪炮及爆炸物管理局、法警局局长。
4. 国土安全部下的移民和海关执法局、海关和边境保护局、特勤局负责人。
5. 库里六局已有的联邦上诉法院法官（一局新建的，`python tools/gaps.py --bureau justice` 能查到），也做简历。

## 资料线索

- 库里已有谁：`python tools/gaps.py --bureau justice`。
- 一级来源：联邦司法中心法官传记库（fjc.gov，有出生年、学历、任职、提名和确认日期）、最高法院官网大法官简历、司法部 justice.gov 领导页、fbi.gov、dea.gov、atf.gov、usmarshals.gov、ice.gov、cbp.gov、secretservice.gov、congress.gov 提名和确认记录。

## 产出

`roster.csv`（库里没有的现任者）、`resumes/`（上面全部人员）、`events.json`（2026-06-01 以来这些职位的任免、提名、确认）、`names.csv`、`portraits.csv`（官方标准照）、`README.md`（职位—现任者对照表，没查清的单列）。

## 完成标准

`python tools/inbox.py check inbox/codex/liuju` 错误为 0；README 的对照表覆盖上面全部职位。
