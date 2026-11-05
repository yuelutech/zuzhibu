# backends（模型适配与流水线）

- codex_cli：调用本机 `codex exec`，只读沙箱，关闭联网检索，不加载本机 config.toml（MCP、插件、连接器、钩子不进来），用 `--output-schema` 约束输出。用使用者自己的 ChatGPT 登录，或设 `CODEX_API_KEY` 按 API 计费；本模块不读取、不复制任何认证文件。一局维护者每月更新的研究、核查会话也走它（开联网检索，见 bureaus/dispatch/research.py）。
- ai_api：使用者自己的 AI 接口密钥直连，带联网检索，标准库 urllib，不用任何 SDK。一局“立即更新”的研究、核查两次会话走这里，用使用者自己的额度（见下面“ai_api 怎么调接口”）。

两类后端接口相同：输入提示词和输出 schema，返回校验过的 JSON。

秘书局、宣传教育局共用的流水线也放在这里（路线图第 5 步“codex_cli 与流水线”）。只用标准库。

## 模块

| 模块 | 做什么 |
|---|---|
| `codex_cli.py` | 封装 `codex exec`：只读沙箱、JSON 输出、超时、重试、过程记录 |
| `ai_api.py` | 使用者自己的 AI 接口直连：两类带联网检索的接口、联网动作统计、续接与重答、重试和出错分类、检测联网检索、密钥不进记录 |
| `schema_lite.py` | 标准库实现的 JSON Schema 子集校验，检查后端返回的 JSON |
| `glossary.py` | 读 shared/glossary 三张 v1.4 CSV，生成前确定性匹配，生成后一致性检查 |
| `lint_hook.py` | tools/lint 的调用点：模块存在就调（传原文、稿件结构、社名），不存在就记“未接入” |
| `pipeline.py` | 读原文 → 译名表匹配 → 组提示词 → 调后端 → lint → 输出 Markdown 和 JSON → 渲染红头件和公众号 HTML |

## codex_cli 怎么调 codex

实际命令行（提示词走 stdin，不进命令行，避免长度限制和转义问题）：

```
codex exec --json --color never --sandbox read-only -C <空目录> --skip-git-repo-check --ephemeral --ignore-user-config \
    [-m 模型] [-c model_reasoning_effort="max"] -c web_search="disabled" --output-schema schema.json -o last_message.txt  < prompt.md
```

- `--sandbox read-only`：模型只能读，不能写、不能改。`codex exec` 是非交互模式，审批策略固定为 never，模型发起的写文件和命令审批一律被拒。
- `-c web_search="disabled"`：关闭联网检索（可选值 disabled、cached、indexed、live）。
- `-C <空目录>`：工作根目录是过程记录下新建的空目录，模型看不到仓库。
- `--ephemeral`：会话不落盘到 `~/.codex/sessions`。老版本不认这个标志时用 `CodexConfig(ephemeral=False)` 或命令行 `--no-ephemeral`。
- `--ignore-user-config`：不加载 `~/.codex/config.toml`。本机配置里的 MCP 服务器（邮箱、即时通讯等）、插件、应用连接器（含发邮件）、钩子都不带进改写任务，原文里夹带的指令也就够不着这些工具；认证照旧用本机登录。模型名和推理强度改由 `-m`、`-c model_reasoning_effort` 给：命令行 `--model`、`--reasoning-effort` 没给时，`codex_cli.user_defaults()` 只从 config.toml 读这两项。要整份加载本机配置用 `--use-user-config`。
- `-c features.<名>=false`：关掉模型的工具，名单在 `codex_cli.DEFAULT_DISABLED_FEATURES`（apps、shell_tool、unified_exec、code_mode_host、multi_agent、view_image、browser_use、computer_use、image_generation、memories、plugins、hooks 等）。只关 `web_search` 关不住联网：ChatGPT 登录自带的 apps 连接器（codex_apps）里有 `search_service.web_run`，2026-10-17 本机实跑时模型用它检索过译名，还试过执行 PowerShell 读本地文件（被只读沙箱拦下）。关掉后实测只剩框架内核的几项（等待、提问、协作），不能联网、不能跑命令。要保留工具用命令行 `--keep-tools`。
- 全局 `~/.codex/AGENTS.md`（使用者自己的写作规则）不受 `--ignore-user-config` 影响，仍会进每次调用；实跑时模型按它在 review_notes 里写了“判断单”。
- 找可执行程序：`--codex` 缺省是 `codex`；PATH 里没有时，在 Windows 上找桌面版自带的 `%LOCALAPPDATA%\OpenAI\Codex\bin\<哈希>\codex.exe`，取最新的一个（桌面版每次更新换哈希目录，写死路径会失效）。
- `--json`：事件按 JSONL 打印；最终回复取 `item.completed` 里 `agent_message` 的 `text`，同时用 `-o` 另存一份，两者互为备份。
- `--output-schema`：约束最终回复的形状；本地再用 schema_lite 校验一遍。

以上标志按 openai/codex 仓库 `codex-rs/exec/src/cli.rs`、`codex-rs/utils/cli/src/shared_options.rs` 和 `codex-rs/protocol/src/config_types.rs`（2026-10 核对）。

### 超时与重试

- `timeout` 默认 600 秒，超时杀掉子进程，算一次失败。
- `retries` 默认 2，即最多 3 次；第 n 次重试前等 `backoff * 2**(n-1)` 秒（默认 2、4 秒）。
- 重试的情形：超时、非零退出、退出码 0 但无回复、回复不是 JSON、回复不符 schema（后两种会在提示词末尾追加一句“只输出合法 JSON”）。
- 不重试的情形：找不到 codex 可执行程序（`CodexNotFound`）、stderr 里出现未登录或凭据无效字样（`CodexAuthError`）。

### 过程记录

每次调用在工作目录下建一个子目录 `<时间戳>-<标签>-<随机码>/`：

```
prompt.md                 发给模型的提示词（含原文，所以工作目录要放在 .gitignore 覆盖的 output/ 下）
schema.json               输出 schema
sandbox/                  传给 -C 的空目录
attempt-1.cmd.txt         这次尝试的命令行
attempt-1.stdout.jsonl    JSONL 事件流
attempt-1.stderr.txt
attempt-1.meta.json       退出码、耗时、是否超时、定性（ok/timeout/exit/auth/empty/bad_json/schema）
last_message.txt          -o 另存的最终回复
result.json               通过校验的 JSON
run.json                  汇总：配置、codex 版本、每次尝试、用量、结果
```

### 用法

```python
from shared.backends import codex_cli

config = codex_cli.CodexConfig(timeout=600, retries=2)          # 默认 command=("codex",)
result = codex_cli.run(prompt, schema, config, "output/runs", label="demo")
result.data        # 校验过的 JSON
result.run_dir     # 过程记录目录
```

命令行冒烟（本机有 codex 时）：`python -m shared.backends.codex_cli prompt.md --schema schema.json`。

测试不联网：把 `command` 换成 `(sys.executable, "tests/fakes/fake_codex.py")`，假程序照 `codex exec` 的参数和 JSONL 事件格式回放，行为由环境变量控制（成功、退出 1、输出坏 JSON、不符 schema、超时、未登录）。

## ai_api 怎么调接口

一局“立即更新”用使用者在“我的”页填的接口（`%APPDATA%\zuzhibu\settings.json` 的 `api` 一节：`provider`、`base_url`、`model`、`api_key`，可选 `effort`）。只接带联网检索工具的两类，`ai_api.PROVIDERS`：

| 类型 | 接口 | 联网 | 输出约束 | 默认地址、模型 |
|---|---|---|---|---|
| `openai` | Responses：`POST {地址}/responses` | 工具 `web_search`（检索、打开网页、页内查找），`max_tool_calls` 封顶 | `text.format` 严格 JSON Schema | `https://api.openai.com/v1`，`gpt-6.1-sol` |
| `anthropic` | Messages：`POST {地址}/messages`，`anthropic-version: 2023-06-01` | 服务端工具 `web_search_20250305`、`web_fetch_20250910`，`max_uses` 封顶，取网页限 `max_content_tokens` | 提示词附 schema，本地校验 | `https://api.anthropic.com/v1`，`claude-opus-5-5` |
| `other` | 只能对话的接口 | 没有 | — | 不能开更新，`NO_WEB` 写明原因 |

- 接口地址要 https（本机测试可用 http://127.0.0.1）；只写了域名的补 `/v1`。用中转地址的，要它支持对应接口的联网检索工具。
- openai 的会话长，用后台模式（`background: true`）提交后每 5 秒轮询，超过会话时限就取消；接口不认 `background`、`include`、`max_tool_calls` 一类参数时去掉再试。回复写不完（`max_output_tokens`）放宽一次。
- anthropic 遇 `pause_turn` 把已有内容原样送回续上（最多 20 次）；最后的文字不是符合 schema 的 JSON 时，在同一会话里要求按格式重答一次（不再检索）；还不行算失败。
- 联网动作：openai 每个成功的 `web_search_call`（检索、打开网页、页内查找）算一次；anthropic 每个有结果的 `web_search`、`web_fetch` 算一次，结果块报错的不算。打开过的网页记链接。调用方据此作废没有联网的会话（fail-closed）。
- 重试：408、409、429、5xx、529 和网络中断，按 `Retry-After` 或指数退避，默认 3 次。不重试、整次更新停下：401、403（密钥无效）、额度不足（`ApiAuthError`），检索工具被拒（`ApiNoWebSearch`）。
- 检测（`probe`）：问一个必须检索才答得上的小问题，检索、取网页各限 1 次；模型没有检索就算不能用。设置页“保存并检测联网检索”和流水线开头都调它。
- 密钥只放在请求头里；`ApiConfig` 的 repr、`describe()`、过程记录、报错都不含密钥，接口报错回显的密钥原文一律隐去（`redact`）。
- 过程记录：每次会话在工作目录下建 `<时间>-<标签>-<随机码>/`，存 `prompt.md`、`schema.json`、`config.json`（不含密钥）、`request-NNN.json`、`response-NNN.json`（openai 后台模式的最终回复存 `response-final.json`）、`last_message.txt`、`result.json`、`run.json`（结果、联网统计、用量）。
- 测试不联网：`ApiConfig(transport=...)` 换成假的发送函数，见 `tests/fakes/fake_ai_api.py`。

用法：

```python
from shared.backends import ai_api

config = ai_api.config_from_settings(settings["api"])          # 没有检索、没填密钥的抛出，说明原因
print(ai_api.probe(config)["message"])                          # 检测联网检索，约检索 1 次
result = ai_api.run(prompt, schema, config, "output/runs", label="research")
result.data, result.stats["searches"], result.run_dir
```

## glossary

- `Glossary.load()` 读 `shared/glossary/译名表_v1.4_{org,title,person}.csv`，列名沿用定稿列。
- `match(英文原文)`：确定性整词匹配，机构和人名区分大小写，职务不分；长键优先，命中后遮住不再二次命中；英文键尾部的全大写缩写（如 `(NSF)`）和机构全称里全角括号内的英文缩写（如 `（AWS）`）作为别名参与匹配；同名多义（如 `Mayor (City of Kigali)` 与 `Mayor (district, Rwanda)`）标 ambiguous，候选都给模型看。
- 新闻稿、讲话稿取列：机构首次“全称”再提“简称”，职务“干部简历写法”，人名首次“新华社译名”再提姓。
- `check_output(成稿, 命中)`：首次是否用全称、是否先出现了简称、全称是否重复太多、“待核”条目提示复核。只到 P1，不触发重写。
- `render_prompt_table(命中)`：给模型看的表。

## lint_hook（tools/lint 调用点）

tools/lint 由其他任务实现，这里只约定接口：

- 默认模块：讲话稿 `tools.lint.speech_lint`，新闻稿 `tools.lint.newswire_lint`；可用 `--lint-module` 换。
- 模块里按顺序找 `lint_text`、`lint`、`check_text`、`check` 之一，调用 `fn(text)`；函数接受 `genre` 关键字时一并传入。`text` 是成稿纯文本：标题、副题、各段落用空行隔开。
- 返回值：issue 列表（dict 键 `level`/`rule`/`message`，或同名属性的对象，或 `"P0 …"` 字符串），也认 `(ok, issues)` 和带 `issues` 属性的对象。level 归一为 P0/P1/P2。
- 模块不存在：报告 `available=False`，流水线把“lint 未接入”写进复核清单，不中断；导入或运行出错也不中断，原因写进 note。
- context：流水线另给 `source`（英文原文）、`document`（{title, subtitle, paragraphs}）、`extra`（社名等）、`glossary_dir`，lint 函数声明了哪个关键字就传哪个。两个默认模块已实现，见 tools/lint/README.md。
- P0 的处理照路线图：带着意见重写一次，仍不过标 `needs-review`，交人工。

## pipeline

两个局各给一个 `BureauSpec`（文体、写法规范、输出 schema、提示词模板、稿件版式），其余在这里：

1. 读原文（UTF-8）。
2. 译名表匹配，写 `<原文名>.glossary.json`。
3. 组提示词：任务说明 + 写法规范正文（去掉 YAML 头）+ 译名表命中表 + 原文（放在 `<source>` 标签里，并声明里面的指令无效）+ 输出字段说明；写 `<原文名>.prompt.md`。`--dry-run` 到此为止，另写命令行预览。
4. 调 codex_cli，过程记录在 `<输出目录>/runs/`。
5. 拼成稿件对象（标题、副题、顺序段落、落款），跑译名检查和 lint；lint 有 P0 且还没重写过就带着意见和上一稿重写一次。
6. 写 `<原文名>.md`（YAML 头 + 正文 + 复核清单）和 `<原文名>.json`（稿件对象 + 渲染对象 + 原始草稿 + 译名、lint、后端记录、耗时）。
7. 用各局 `build_render` 给出的渲染对象出 `<原文名>.redhead.docx` 和 `<原文名>.article.html`；`--no-render` 不出，`--local-fonts` 用本机公文字体，`--line-pitch` 改正文行距。渲染失败不影响成稿，原因写进结果说明。

## 留给本机做

- 2026-10-17 本机实跑（codex-cli 0.163.0-alpha.10.2，gpt-6.1-sol，推理强度 max）：`--ephemeral`、`-c web_search="disabled"`、`--ignore-user-config`、`--output-schema`、`-o` 都认。
- 关闭 shell：本机版本有 `features.shell_tool`、`features.unified_exec`，已放进默认关闭名单；Codex 升级后用 `codex features list` 核对名单里的名字还在不在。
- Windows 下 `codex` 若是 npm 的 `.cmd` 外壳，超时时只能杀掉外壳进程，底下的 node 进程可能残留；桌面版自带的 codex.exe 没有这层外壳。
- 参数卡阈值用私有金标校准（路线图第 9 步）。
