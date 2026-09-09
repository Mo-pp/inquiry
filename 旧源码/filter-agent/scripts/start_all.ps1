# start_all.ps1 - controlled one-click launcher for filter-agent and wa-bridge
#
# Default: start the local API and the WhatsApp Web bridge only.
# Optional switches explicitly enable the queue worker, reply scheduler, or
# the one-number outreach test. No WhatsApp message is sent by default.
#
# Examples:
#   .\scripts\start_all.ps1
#   .\scripts\start_all.ps1 -SendTest -AllowPhone "+8618664612668"
#   .\scripts\start_all.ps1 -StartInboxWorker -StartReplyScheduler

[CmdletBinding()]
param(
    [switch]$SendTest,

    [ValidatePattern('^\+[1-9]\d{5,14}$')]
    [string]$AllowPhone,

    [switch]$StartInboxWorker,
    [switch]$StartReplyScheduler,

    [ValidatePattern('^\+[1-9]\d{5,14}$')]
    [string]$ReplyAllowPhone,

    [ValidateRange(30, 1800)]
    [int]$WaReadyTimeoutSeconds = 240
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if ($SendTest -and [string]::IsNullOrWhiteSpace($AllowPhone)) {
    throw "-SendTest requires one explicitly approved -AllowPhone value."
}
if (-not $SendTest -and -not [string]::IsNullOrWhiteSpace($AllowPhone)) {
    throw "-AllowPhone can only be used together with -SendTest."
}
if ($StartReplyScheduler -and [string]::IsNullOrWhiteSpace($ReplyAllowPhone)) {
    throw "-StartReplyScheduler requires one explicit -ReplyAllowPhone value."
}

[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$env:PYTHONIOENCODING = "utf-8"

$ApiUrl = "http://127.0.0.1:8000"
$BridgeUrl = "http://127.0.0.1:3010"

function Test-Http($Url) {
    try {
        Invoke-RestMethod $Url -TimeoutSec 3 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Get-PortPid($Port) {
    $line = netstat -ano | Select-String ":$Port\s.*LISTENING"
    if ($line -and $line -match '\s+(\d+)\s*$') {
        return $Matches[1]
    }
    return $null
}

function Show-PortConflict($Name, $Port) {
    $ownerPid = Get-PortPid $Port
    if ($ownerPid) {
        $proc = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
        Write-Host "[$Name] 端口 $Port 被进程 PID $ownerPid（$($proc.ProcessName)）占用但服务无响应。" -ForegroundColor Red
        Write-Host "        请关闭旧窗口后重跑本脚本；不要在不确认进程用途时强制结束它。" -ForegroundColor Yellow
    } else {
        Write-Host "[$Name] 启动失败且端口 $Port 未被占用，请查看服务窗口中的报错信息。" -ForegroundColor Red
    }
}

function Test-WaBrowserAlive {
    try {
        $procs = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction Stop |
            Where-Object { $_.CommandLine -match 'wa-session' }
        return [bool]$procs
    } catch {
        return $false
    }
}

function Stop-WaBridge {
    $listenerPid = Get-PortPid 3010
    if ($listenerPid) {
        taskkill /PID $listenerPid /T /F 2>$null | Out-Null
        for ($i = 0; $i -lt 10; $i++) {
            Start-Sleep -Seconds 1
            if (-not (Get-PortPid 3010)) { break }
        }
    }
}

function Wait-WaReady($TotalSec) {
    $deadline = (Get-Date).AddSeconds($TotalSec)
    $wa = $null
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        $wa = $null
        try {
            $wa = Invoke-RestMethod "$BridgeUrl/health" -TimeoutSec 3
        } catch {}
        if ($wa -and $wa.status -eq "ready") { return $wa }
        if ($wa -and $wa.status -in @("auth_failure", "disconnected")) { return $wa }
    }
    return $wa
}

function Wait-ApiReady($TotalSec = 30) {
    for ($i = 0; $i -lt $TotalSec; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Http "$ApiUrl/health") { return $true }
    }
    return $false
}

function Ensure-Claude {
    $npmGlobal = Join-Path $env:APPDATA "npm"
    if (-not (Get-Command claude -ErrorAction SilentlyContinue) -and
        (Test-Path (Join-Path $npmGlobal "claude.cmd"))) {
        $env:Path = "$npmGlobal;$env:Path"
    }
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        Write-Host "[claude] 未检测到 claude CLI，正在自动安装..." -ForegroundColor Yellow
        npm install -g "@anthropic-ai/claude-code"
        if ($LASTEXITCODE -ne 0) {
            throw "claude CLI 自动安装失败。请先手动执行 npm install -g @anthropic-ai/claude-code。"
        }
        $env:Path = "$npmGlobal;$env:Path"
    }
    Write-Host "[claude] 就绪：$(claude --version)" -ForegroundColor Green
}

# Windows PowerShell 5.1 (including x86) strips nested quotes when a command
# is passed through Start-Process -ArgumentList. Encode the child command so
# URLs and paths arrive unchanged in the new window.
function Start-PowerShellWindow($WorkingDirectory, [string]$Command) {
    $encodedCommand = [Convert]::ToBase64String(
        [Text.Encoding]::Unicode.GetBytes($Command)
    )
    Start-Process powershell -WorkingDirectory $WorkingDirectory `
        -ArgumentList @("-NoExit", "-EncodedCommand", $encodedCommand)
}

# ---------- 1/2 local filter-agent API ----------
if (Test-Http "$ApiUrl/health") {
    Write-Host "[filter-agent] 已在运行。若入站接口返回 503，请关闭旧 API 窗口后重跑。" -ForegroundColor Yellow
} else {
    $pid8000 = Get-PortPid 8000
    if ($pid8000) {
        Show-PortConflict "filter-agent" 8000
        exit 1
    }
    Write-Host "[filter-agent] 新窗口启动中（入站队列开关已打开）..."
    $apiCommand = '$env:WHATSAPP_INBOUND_ENABLED="true"; python -m filter_agent'
    Start-PowerShellWindow $Root $apiCommand
    if (-not (Wait-ApiReady)) {
        Show-PortConflict "filter-agent" 8000
        exit 1
    }
    Write-Host "[filter-agent] 就绪：$ApiUrl/health" -ForegroundColor Green
}

# ---------- 2/2 wa-bridge ----------
$waStatus = $null
if (Test-Http "$BridgeUrl/health") {
    try { $waStatus = (Invoke-RestMethod "$BridgeUrl/health" -TimeoutSec 3).status } catch {}
}

if ($waStatus -eq "ready" -and -not (Test-WaBrowserAlive)) {
    Write-Host "[wa-bridge] 检测到服务与专用 Chrome 状态不一致，自动重启桥接服务..." -ForegroundColor Yellow
    Stop-WaBridge
    $waStatus = $null
} elseif ($waStatus -in @("auth_failure", "disconnected")) {
    Write-Host "[wa-bridge] 状态异常（$waStatus），自动重启桥接服务..." -ForegroundColor Yellow
    Stop-WaBridge
    $waStatus = $null
}

if ($waStatus -eq "ready") {
    Write-Host "[wa-bridge] 已在运行，WhatsApp 已登录。" -ForegroundColor Green
    Write-Host "            如该窗口不是本脚本启动的，请确认 WA_INBOUND_ENABLED=true；否则关闭旧窗口后重跑。" -ForegroundColor Yellow
} elseif ($waStatus) {
    Write-Host "[wa-bridge] 已在运行，当前状态：$waStatus；请在桥接窗口完成扫码。" -ForegroundColor Yellow
} else {
    $listenerPid = Get-PortPid 3010
    if ($listenerPid) {
        Show-PortConflict "wa-bridge" 3010
        exit 1
    }
    Write-Host "[wa-bridge] 新窗口启动中（会弹出 WhatsApp 专用 Chrome 窗口，请勿关闭）..."
    # Windows PowerShell may block npm.ps1 under the machine execution policy.
    # npm.cmd invokes the same npm CLI without requiring script execution.
    $bridgeCommand = '$env:WA_INBOUND_ENABLED="true"; $env:WA_INBOUND_URL="http://127.0.0.1:8000/api/v1/whatsapp/inbound"; $env:WA_HEADLESS="false"; $env:WA_COMPENSATION_SCAN_ON_READY="false"; $env:WA_COMPENSATION_SCAN_INTERVAL_SECONDS="0"; npm.cmd start'
    Start-PowerShellWindow (Join-Path $Root "wa-bridge") $bridgeCommand
    $wa = Wait-WaReady $WaReadyTimeoutSeconds
    if ($wa -and $wa.status -eq "ready") {
        Write-Host "[wa-bridge] 就绪：$BridgeUrl/health" -ForegroundColor Green
    } elseif ($wa) {
        Write-Host "[wa-bridge] 服务当前状态为 $($wa.status)，尚未达到 ready。" -ForegroundColor Red
        Write-Host "            请先在 bridge 窗口完成扫码/登录并确认出现 'WhatsApp is ready'，再重跑测试。" -ForegroundColor Yellow
        exit 1
    } else {
        Show-PortConflict "wa-bridge" 3010
        exit 1
    }
}

Write-Host ""
Write-Host "基础服务已启动。Google Sheets 同步仍保持关闭（GOOGLE_SHEETS_SYNC_ENABLED=false）。" -ForegroundColor Green
Write-Host "默认不会启动队列 worker、回复调度器，也不会发送 WhatsApp 消息。" -ForegroundColor Gray

# ---------- Optional persistent inbound worker ----------
if ($StartInboxWorker) {
    Write-Host "[inbox-worker] 启动中：会持续处理 wa_inbox_jobs，请确认测试期间只有授权号码产生消息。" -ForegroundColor Yellow
    $workerCommand = '$env:WHATSAPP_INBOUND_WORKER_EXECUTE="true"; python scripts\process_inbox_queue.py --execute'
    Start-PowerShellWindow $Root $workerCommand
}

# ---------- Optional persistent reply scheduler ----------
if ($StartReplyScheduler) {
    Ensure-Claude
    $logDir = Join-Path $Root "logs"
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $logFile = Join-Path $logDir ("scheduler_{0}.log" -f (Get-Date -Format "yyyyMMdd"))
    Write-Host "[reply-scheduler] 启动中：每 20 秒扫描，静默 2 分钟后调用 filter-agent/MCP。" -ForegroundColor Yellow
    $schedulerScript = Join-Path $Root "scripts\process_unread_batch.py"
    $schedulerCommand = "chcp 65001 > `$null; `$OutputEncoding = [System.Text.UTF8Encoding]::new(`$false); [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(`$false); `$env:PYTHONIOENCODING = 'utf-8'; `$env:PYTHONUTF8 = '1'; while (`$true) { & python `"$schedulerScript`" 2>&1 | Tee-Object -FilePath `"$logFile`" -Append; Start-Sleep -Seconds 20 }"
    # Use the UTF-8 wrapper and pass the explicit test allowlist.  The legacy
    # command above is intentionally overwritten here for compatibility with
    # older checked-out launchers.
    $logFile = Join-Path $Root ("logs\scheduler_{0}_utf8.log" -f (Get-Date -Format "yyyyMMdd"))
    $schedulerScript = Join-Path $Root "scripts\run_reply_scheduler.ps1"
    $schedulerCommand = "& `"$schedulerScript`" -AllowPhone `"$ReplyAllowPhone`" -LogFile `"$logFile`""
    Start-PowerShellWindow $Root $schedulerCommand
    Write-Host "[reply-scheduler] 日志：$logFile" -ForegroundColor Cyan
}

# ---------- Optional one-number real outreach test ----------
if ($SendTest) {
    Write-Host ""
    Write-Host "[outreach] 即将对明确授权号码 $AllowPhone 执行一次 getNumberId/sendMessage。" -ForegroundColor Yellow
    Write-Host "[outreach] 这不是 dry-run；请确认桥接窗口已显示 ready。" -ForegroundColor Yellow

    $oldWorkerGate = $env:OUTREACH_WORKER_EXECUTE
    $oldSendGate = $env:OUTREACH_REAL_SEND_ENABLED
    try {
        $env:OUTREACH_WORKER_EXECUTE = "true"
        $env:OUTREACH_REAL_SEND_ENABLED = "true"
        & python (Join-Path $Root "scripts\process_lead_outreach.py") `
            --execute --limit 1 --allow-phone $AllowPhone
        if ($LASTEXITCODE -ne 0) {
            throw "主动触达命令退出码：$LASTEXITCODE"
        }
    } finally {
        if ($null -eq $oldWorkerGate) {
            Remove-Item Env:OUTREACH_WORKER_EXECUTE -ErrorAction SilentlyContinue
        } else {
            $env:OUTREACH_WORKER_EXECUTE = $oldWorkerGate
        }
        if ($null -eq $oldSendGate) {
            Remove-Item Env:OUTREACH_REAL_SEND_ENABLED -ErrorAction SilentlyContinue
        } else {
            $env:OUTREACH_REAL_SEND_ENABLED = $oldSendGate
        }
    }
}

Write-Host ""
Write-Host "窗口停止方式：分别切到服务窗口按 Ctrl+C；不要关闭 WhatsApp 专用 Chrome 窗口后继续测试。" -ForegroundColor Gray
