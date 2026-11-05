# 云会话任务 03：渲染层

## 共同背景

AI政见·组织部（仓库 yuelutech/zuzhibu，私有）是 Yuelu Tech 旗下 AI政见 项目的子项目：一个新闻客户端风格的本地软件，六个局——秘书局（英文讲话→领导干部讲话稿）、宣传教育局（英文新闻→中共和央国企风格新闻稿）、人事一至四局（硅谷 AI 领袖、白宫、国会、军队法院等人物资料库）。共享层在 shared/：glossary（译名表 v1.4 定稿，三张 CSV）、skills、samples、render、portraits、schema、backends。v0.1 只用本机 Codex CLI 后端，Python 标准库优先，一个人维护，不建数据库和后台服务。仓库里不放字体、任何第三方 App 的名称与标识、真实人物成品样张、密钥。每个任务：在自己的分支上做，交 PR，带测试，README 写清用法；不改别的目录；本机专属的事（Codex 实跑、办公软件核版式、方正字体、头像位图、发布）不做，留接口。

## 任务

目录：shared/render/

产出：article_html.py（公众号 HTML：浅蓝提要框、居中照片、首段、带缩进的履历行、"写在最后"固定段，全部行内样式）；card_html.py + card.css（3:4 图文卡 HTML/CSS，540×720 磅按三倍出 1620×2160，字体回退顺序 Times New Roman 在前、仿宋在后，首行缩进 2 字、1.5 行距、两端对齐、续页垂直居中，约 22 字处在机构名后断行）；docx_redhead.py（python-docx 红头件：标题、发文字号、红线、正文，字体名可配置，默认免费字体）；每个渲染器带示例输入和生成样例；Playwright 截图步骤只写文档不在云上跑。

## 交付

在新分支上做，分支名 cloud/03-render，完成后开 PR 到 main，PR 描述写清改了什么、怎么测的、哪些留给本机做。
