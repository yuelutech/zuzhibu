# 云会话任务 05：人事三局导入脚本

## 共同背景

AI政见·组织部（仓库 yuelutech/zuzhibu，私有）是 Yuelu Tech 旗下 AI政见 项目的子项目：一个新闻客户端风格的本地软件，六个局——秘书局（英文讲话→领导干部讲话稿）、宣传教育局（英文新闻→中共和央国企风格新闻稿）、人事一至四局（硅谷 AI 领袖、白宫、国会、军队法院等人物资料库）。共享层在 shared/：glossary（译名表 v1.4 定稿，三张 CSV）、skills、samples、render、portraits、schema、backends。v0.1 只用本机 Codex CLI 后端，Python 标准库优先，一个人维护，不建数据库和后台服务。仓库里不放字体、任何第三方 App 的名称与标识、真实人物成品样张、密钥。每个任务：在自己的分支上做，交 PR，带测试，README 写清用法；不改别的目录；本机专属的事（Codex 实跑、办公软件核版式、方正字体、头像位图、发布）不做，留接口。

## 任务

目录：bureaus/personnel-congress/

前提（已满足，提交 fbdcafe）：Care 的 ceo-profiles（profiles/*.json 底稿 538 份、resumes/、roster-538.csv、progress.csv、batches/TIERING.md、RULES.md、schema.md）已拷进 bureaus/personnel-congress/import/ceo-profiles/，说明和已知问题见 bureaus/personnel-congress/import/README.md。

产出：import_ceo_profiles.py：把 538 份底稿转成 shared/schema 的事实包（person、current_roles 含 sector politics/committee、careers、party/party_joined_at 按 party_join.md 规则只保留有锚点的，否则置空并记 unknowns），T1 名单标 gold、其余标 draft；validate 通过率报告；tests 用 3 份样本。

## 交付

在新分支上做，分支名 cloud/05-personnel-congress-import，完成后开 PR 到 main，PR 描述写清改了什么、怎么测的、哪些留给本机做。
