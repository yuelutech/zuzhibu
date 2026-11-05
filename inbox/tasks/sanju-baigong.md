# 三局·白宫办公厅、总统行政办公室、独立机构（2026年10月版）

任务名 `sanju-baigong`，结果只写进 `inbox/codex/sanju-baigong/`。文件格式和规矩见 `inbox/README.md`。

## 范围

截至 2026-10-18 下列职位的现任者（正式、代理都算）：

1. 白宫办公厅和总统行政办公室：副幕僚长、国家安全事务助理、国土安全顾问、国家经济委员会主任、经济顾问委员会主席、科技政策办公室主任、白宫人工智能事务主管（如仍设）、新闻秘书、通讯联络主任、白宫法律顾问、白宫秘书（Staff Secretary）、国内政策委员会主任、国家药物管制政策办公室主任、国家网络总监、总统特使（现任的中东、俄乌等特使）。
2. 独立机构：联邦储备委员会全体理事（主席、副主席、理事）、证券交易委员会主席、联邦贸易委员会主席、商品期货交易委员会主席、联邦通信委员会全体委员、联邦住房金融局局长、国家航空航天局局长、人事管理局局长。
3. 美国驻华大使。
4. 库里三局现有、又不属于 `sanju-neige` 任务范围的人（驻外大使等，`python tools/gaps.py --bureau whitehouse` 能查到），也做简历。

内阁部长、常务副部长、内阁级官员归 `sanju-neige`，本任务不做。

## 待查清的两件事

- 白宫新闻秘书 2026-10-16 离任（库里 `katie-zacharia`，转七局前核对未通过）：她还有没有其他现职、有没有公布新任命；现任新闻秘书是谁。
- 比尔·普尔特（`bill-pulte`）2026-08-03 卸任代理国家情报总监，库里已无现任职务：查明他是否仍任联邦住房金融局局长或其他职务。

查清的结果写进 resumes/（现任职务）和 events.json（新任命），README 里单列结论和来源。

## 资料线索

- CEO 雷达 2026 年 7 月整理的白宫核心 21 人中文简历稿（只作线索，每项重新核对）：
  `<素材库>\Care历史项目\解包_2026-10-16\资料\D\Claude\ceo-radar-build\repo\local_data\wh_core\wh21_resumes.json`（`<素材库>` 是本机设置 library 的目录）
- 一级来源优先：whitehouse.gov、federalreserve.gov、sec.gov、ftc.gov、cftc.gov、fcc.gov、fhfa.gov、nasa.gov、opm.gov、state.gov、congress.gov 提名和确认记录。

## 产出

`roster.csv`、`resumes/`、`events.json`（2026-06-01 以来这些职位的任免）、`names.csv`、`portraits.csv`、`README.md`（职位—现任者对照表；没查清的单列）。

## 完成标准

`python tools/inbox.py check inbox/codex/sanju-baigong` 错误为 0；README 的对照表覆盖上面全部职位。
