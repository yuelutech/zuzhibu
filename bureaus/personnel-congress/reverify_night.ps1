# 四局夜间整批重核：Windows 计划任务“AIpolis-人事三局重核”每天 23:30 调用。
# 07:00 后不再开新人（08:00 一局每日更新要用 Codex），每晚最多 40 人，额度分散到多晚。
# 规则读本机 aipolis 规则目录（local.json 的 aipolis_rules），见 reverify.py --rules-dir。
# -DryRun：只写研究提示词，不调 Codex（试计划任务用）。
param([switch]$DryRun)
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
# Python 取本机设置：环境变量 ZUZHIBU_PYTHON，或 %APPDATA%\zuzhibu\local.json 的 python（见 zuzhibu/local.py）；都没有就用 PATH 里的 python。
$py = $env:ZUZHIBU_PYTHON
if (-not $py) {
    $conf = Join-Path $(if ($env:APPDATA) { $env:APPDATA } else { Join-Path $HOME '.config' }) 'zuzhibu\local.json'
    if (Test-Path $conf) { $py = (Get-Content -Raw -Encoding UTF8 $conf | ConvertFrom-Json).python }
}
if (-not $py) { $py = 'python' }
Set-Location $root
$logDir = Join-Path $root 'bureaus\personnel-congress\data\verified'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir ('夜间运行_' + (Get-Date -Format 'yyyy-MM-dd') + $(if ($DryRun) { '_dry-run' } else { '' }) + '.log')
$extra = @()
if ($DryRun) { $extra = @('--dry-run'); ('Python：' + $py) *>> $log }
& $py bureaus\personnel-congress\reverify.py --resume --jobs 2 --until 07:00 --limit 40 @extra *>> $log
exit $LASTEXITCODE
