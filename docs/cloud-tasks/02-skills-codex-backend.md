# 云会话任务 02：秘书局与宣传教育局 Skill 初稿 + Codex 后端适配层

## 共同背景

AI政见·组织部（仓库 yuelutech/zuzhibu，私有）是 Yuelu Tech 旗下 AI政见 项目的子项目：一个新闻客户端风格的本地软件，六个局——秘书局（英文讲话→领导干部讲话稿）、宣传教育局（英文新闻→中共和央国企风格新闻稿）、人事一至四局（硅谷 AI 领袖、白宫、国会、军队法院等人物资料库）。共享层在 shared/：glossary（译名表 v1.4 定稿，三张 CSV）、skills、samples、render、portraits、schema、backends。v0.1 只用本机 Codex CLI 后端，Python 标准库优先，一个人维护，不建数据库和后台服务。仓库里不放字体、任何第三方 App 的名称与标识、真实人物成品样张、密钥。每个任务：在自己的分支上做，交 PR，带测试，README 写清用法；不改别的目录；本机专属的事（Codex 实跑、办公软件核版式、方正字体、头像位图、发布）不做，留接口。

## 任务

目录：shared/skills/cadre-speech/、shared/skills/soe-newswire/、shared/backends/、bureaus/secretariat/、bureaus/publicity/

产出：两份 SKILL.md（讲话稿：称谓、结构、段落功能、句式、禁忌；新闻稿：新华社电头、导语、机构名首次提及带国名、引语处理），借 lieflat-gongwen 的量化指标思路写成可检查项；shared/backends/codex_cli.py：封装 `codex exec` 非交互调用（read-only 沙箱、JSON 输出、超时、重试、过程记录到工作目录），用假的 codex 可执行程序做测试；bureaus 两个目录各一个 run.py：读英文原文 → 译名表匹配 → 调后端 → lint → 输出 Markdown；不联网实跑。

## 交付

在新分支上做，分支名 cloud/02-skills-codex-backend，完成后开 PR 到 main，PR 描述写清改了什么、怎么测的、哪些留给本机做。
