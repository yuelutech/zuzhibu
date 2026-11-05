# 云会话任务 01：写法 lint 与 cadre-resume Skill

## 共同背景

AI政见·组织部（仓库 yuelutech/zuzhibu，私有）是 Yuelu Tech 旗下 AI政见 项目的子项目：一个新闻客户端风格的本地软件，六个局——秘书局（英文讲话→领导干部讲话稿）、宣传教育局（英文新闻→中共和央国企风格新闻稿）、人事一至四局（硅谷 AI 领袖、白宫、国会、军队法院等人物资料库）。共享层在 shared/：glossary（译名表 v1.4 定稿，三张 CSV）、skills、samples、render、portraits、schema、backends。v0.1 只用本机 Codex CLI 后端，Python 标准库优先，一个人维护，不建数据库和后台服务。仓库里不放字体、任何第三方 App 的名称与标识、真实人物成品样张、密钥。每个任务：在自己的分支上做，交 PR，带测试，README 写清用法；不改别的目录；本机专属的事（Codex 实跑、办公软件核版式、方正字体、头像位图、发布）不做，留接口。

## 任务

目录：shared/skills/cadre-resume/、tools/lint/

输入：aipolis 的 rules/cadre.md、party_join.md、research.md。三份的只读副本当时提交在 shared/skills/cadre-resume/rules/（2026-10-21 公开前已移出），以 aipolis 为准，不要改。

产出：SKILL.md（写法纪律的 Skill 版，供 Claude Code、Codex、OpenClaw 用）；tools/lint/cadre_lint.py：禁用叙述词、"期间"误用、日期格式（1985年－1993年、1993年4月至今、年月不补零）、中英文间空格、顿号合并、"未知/待补"字样、译名一致（对照 shared/glossary 三张表的 简历写法 列）、入党句格式；命令行用法 `python -m tools.lint.cadre_lint 文件或目录`，退出码非零表示违规；tests/ 覆盖每条规则，含通过与不通过样例。

## 交付

在新分支上做，分支名 cloud/01-lint-cadre-resume，完成后开 PR 到 main，PR 描述写清改了什么、怎么测的、哪些留给本机做。
