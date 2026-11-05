# publicity（宣传教育局）

- 输入：外国报纸的英文新闻。
- 输出：中共和央国企风格的新闻稿，导出 docx 红头件和公众号 HTML。
- 依赖共享层：glossary、skills/soe-newswire、render/docx_redhead、render/article_html、backends/codex_cli、samples。
- v0.1：在范围内，验收是本机 Codex 跑通 2 篇。

## 文件

- `run.py`：命令行入口。提示词模板、稿件版式在这里；流水线在 shared/backends/pipeline.py。
- `output.schema.json`：给 `codex exec --output-schema` 的最终回复形状，也是本地校验的依据。正文字段照 shared/skills/soe-newswire/SKILL.md 的结构（标题、副题、电头、导语、正文段落），对照字段（译名表命中、表外专名、数字对照、引语对照、消息来源、删去的内容、复核说明）供复核清单。

## 用法

在仓库根目录，本机已安装并登录 Codex CLI：

```
python -m bureaus.publicity.run 原文.txt
python -m bureaus.publicity.run 原文.txt --city 旧金山 --date 2026-10-15 --source-name "美国《华尔街日报》"
python -m bureaus.publicity.run 原文.txt --dry-run
```

常用参数（全部参数见 `--help`）：

| 参数 | 说明 |
|---|---|
| `-o/--out` | 输出目录，默认 `output/publicity/`（.gitignore 已忽略 output/） |
| `--date` | 发稿日期 YYYY-MM-DD，默认今天；电头写“M月D日电”，不补零 |
| `--agency` | 电头社名，默认“政见社”（虚构，待维护者定）；写真实媒体名会被拒绝 |
| `--city` | 发电地城市，不给由模型按事件地判断，无法确定写北京 |
| `--source-name` | 原文媒体的中文写法，用于“据××报道” |
| `--codex`、`--model`、`--reasoning-effort`、`--timeout`、`--retries` | 后端参数：PATH 里的 codex（没有就找 Windows 桌面版自带的 codex.exe）；模型和推理强度不给时只从本机 config.toml 取这两项；600 秒；重试 2 次 |
| `--use-user-config` | 整份加载本机 config.toml（默认不加载：MCP、插件、连接器、钩子不带进改写任务） |
| `--keep-tools` | 不关模型的工具（默认关掉 apps 连接器里的联网搜索、shell、看图、生图等） |
| `--web-search` | 联网检索，默认 disabled |
| `--no-ephemeral` | 老版本 Codex 不认 `--ephemeral` 时用 |
| `--dry-run` | 只做译名表匹配和提示词，不调后端 |
| `--lint-module`、`--no-lint`、`--max-rewrites` | lint 调用点；默认模块 `tools.lint.newswire_lint`，P0 不过带着意见重写一次 |
| `--no-render`、`--local-fonts`、`--line-pitch` | 渲染：默认出红头件和公众号 HTML；`--local-fonts` 用方正小标宋简体、仿宋_GB2312、黑体、楷体_GB2312、宋体、Times New Roman；`--line-pitch` 改正文行距（磅） |

退出码：0 成功；2 生成了但 lint 仍有 P0，交人工；1 失败。

## 产物

```
output/publicity/
  <原文名>.md             成稿：YAML 头 + 标题 + 电头导语段 + 正文 + 复核清单
  <原文名>.json           稿件对象 document（标题、副题、段落、落款）+ 渲染对象 render + 原始草稿 + 译名、lint、后端记录、耗时
  <原文名>.redhead.docx   红头件（发文机关标志为虚构的 AI政见·组织部宣传教育局）
  <原文名>.article.html    公众号 HTML 整页，可粘贴的正文在注释标出的 <section> 里
  <原文名>.prompt.md      发给模型的提示词
  <原文名>.glossary.json  译名表命中
  <原文名>.command.txt    --dry-run 时的命令行预览
  runs/<时间戳>-<原文名>-<id>/   后端过程记录（见 shared/backends/README.md）
```

第一段是电头加导语（“政见社旧金山10月15日电 美国……”），之后按重要性递减。复核清单在 `---` 之后，不随稿件发布。

## 测试

`python -m unittest tests.test_bureau_runs -v`：用 tests/fakes/fake_codex.py 冒充 codex，用虚构的英文新闻片段跑完整流程，检查电头、Markdown 和 JSON 产物、译名表命中与一致性检查、真实媒体名作社名时的拒绝。

## 留给本机做

- 2026-10-17 已用真稿各跑 2 篇（判断单在仓库外）；社名、电头空格、消息来源写法等待定项由维护者定。
- 在文字处理软件里核红头件版式、把公众号 HTML 粘进编辑器看格式（验收标准第 2、3 条）。
