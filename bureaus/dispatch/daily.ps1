# 一局更新：Windows 计划任务“AI政见组织部-一局每日更新”每月 1 日 08:00 调用（2026-10-21 起由每天改为每月，省维护者的 Codex 额度），也可手动运行。
# 1. 一局：从上次更新的截止日到今天的人事变动，研究、核查、写回人物库（bureaus/dispatch/run.py）；窗口取不到就 31 天。
# 2. 四局重核的新结果并进人物库（一局写回过的记录不覆盖）。
# 3. 重算数据清单 data/manifest.json（软件联网按它增量更新、逐个校验，zuzhibu/datasync.py）。
# 4. 当前分支是 main 时，提交 data/ 和重核结果并推送，软件用户联网即可取得最新数据；不是 main 就只写工作目录。
#    想更勤的用户在软件里用自己的 AI API 更新，不用维护者的额度。
# -DryRun：一局只采集线索、写提示词，不调 Codex，不并入人物库，不提交（试计划任务用）。
param([switch]$DryRun)
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
# Python 取本机设置：环境变量 ZUZHIBU_PYTHON，或 %APPDATA%\zuzhibu\local.json 的 python（见 zuzhibu/local.py）；都没有就用 PATH 里的 python。
$py = $env:ZUZHIBU_PYTHON
if (-not $py) {
    $conf = Join-Path $(if ($env:APPDATA) { $env:APPDATA } else { Join-Path $HOME '.config' }) 'zuzhibu\local.json'
    if (Test-Path $conf) { $py = (Get-Content -Raw -Encoding UTF8 $conf | ConvertFrom-Json).python }
}
if (-not $py) { $py = 'python' }
$env:GIT_REDIRECT_STDERR = '2>&1'   # git 的进度信息写到标准输出，PowerShell 不再记成错误
Set-Location $root
$logDir = Join-Path $root 'output\dispatch'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ('每日更新_' + (Get-Date -Format 'yyyy-MM-dd') + $(if ($DryRun) { '_dry-run' } else { '' }) + '.log')
# 窗口：上次更新截止日到今天（每月一次、隔几天手动跑都不漏），最长 62 天；取不到上次的就 31 天
$days = 31
try {
    $meta = Get-Content -Raw -Encoding UTF8 (Join-Path $root 'data\meta.json') | ConvertFrom-Json
    $last = [datetime]::ParseExact([string]$meta.daily.last_window[1], 'yyyy-MM-dd', $null)
    $days = [Math]::Min(62, [Math]::Max(1, ((Get-Date).Date - $last.Date).Days + 1))
} catch {}
$searches = if ($days -gt 7) { 40 } else { 15 }   # 窗口长，研究会话多检索
('窗口 ' + $days + ' 天，最少检索 ' + $searches + ' 次') | Out-File -Append -Encoding utf8 $log
if ($DryRun) {
    ('Python：' + $py) *>> $log
    & $py -m bureaus.dispatch.run --daily --days $days --min-searches $searches --jobs 3 --dry-run *>> $log
    $code = $LASTEXITCODE
    'dry-run：不并入人物库，不提交' *>> $log
    exit $code
}
& $py -m bureaus.dispatch.run --daily --days $days --min-searches $searches --jobs 3 *>> $log
$code = $LASTEXITCODE
& $py -m zuzhibu.importer *>> $log
& $py -m zuzhibu.datasync manifest *>> $log
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -eq 'main') {
    git add -- data bureaus/personnel-congress/data/verified *>> $log
    git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) {
        git commit -m ('一局每日更新 ' + (Get-Date -Format 'yyyy-MM-dd')) *>> $log
        git push origin main *>> $log
    }
} else {
    ('当前分支 ' + $branch + ' 不是 main：数据只写进工作目录，不提交') | Out-File -Append -Encoding utf8 $log
}
exit $code
