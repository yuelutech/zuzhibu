# 二局·简历（分 3 批）

任务名 `erju-jianli-<批号>`（`erju-jianli-1`、`erju-jianli-2`、`erju-jianli-3`），结果只写进 `inbox/codex/erju-jianli-<批号>/`。文件格式和规矩见 `inbox/README.md`。批号在提示词里给。

## 名单

`python tools/gaps.py --bureau ai --batch <批号>/3`，每批约 35 人。名单按整局排序后切，三批互不重叠，跑几次都是同一批人。

## 每人做什么

1. `resumes/<编号>.json`：干部简历事实包。黄仁勋（`jensen-huang`）已是金标，跳过。
2. 中文名：译名表 `shared/glossary/译名表_v1.4_person.csv` 里没有的，报 `names.csv`。
3. 照片：gaps.py 里照片为“无”的，报 `portraits.csv`（公司官网领导页、新闻稿的标准照）。
4. 现任职务和库里不一致的（离职、换岗、新任、兼任新职），`events.json` 报 2026-06-01 以来的动态。

## 资料线索

- 一级来源：公司官网领导页、证券交易委员会申报（10-K、DEF 14A 代理声明写年龄和履历）、大学官网。DEF 14A 的“Age xx”带申报日期，按 cadre.md 第 3 节第 3 条推出生年月，写规则和依据。
- 二级：主流媒体、领英；三级：维基百科只作线索，不单独支撑事实。
- CEO 雷达 2026 年 6 月整理的 AI 领袖中文简历稿，21 人，只作线索：
  `<素材库>\Care历史项目\解包_2026-10-16\资料\D\Claude\ceo-radar-build\bios_zh.json`（`<素材库>` 是本机设置 library 的目录）
- 非美国人（如魏哲家、门施、哈萨比斯）`country_profile` 写 `other`，国籍照实写。

## 范围提醒

二局只收人工智能和科技企业、研究机构、投资机构的领导人物。名单里明显不属于二局的，不做简历，在 README 里列出编号和理由。

## 产出

`resumes/`、`names.csv`、`portraits.csv`、`events.json`（有才交）、`README.md`（每人一行：编号、做了哪些文件、没查到的项）。

## 完成标准

`python tools/inbox.py check inbox/codex/erju-jianli-<批号>` 错误为 0；名单里每个人在 README 里都有一行。
