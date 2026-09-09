# Run the reply scheduler with an explicit phone allowlist and UTF-8 logs.
# This wrapper is intentionally single-number by default so a test terminal
# cannot accidentally scan all customer_messages.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^\+[1-9]\d{5,14}$')]
    [string]$AllowPhone,

    [ValidateRange(5, 3600)]
    [int]$IntervalSeconds = 20,

    [string]$LogFile
)

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

try { chcp 65001 > $null } catch {}
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

if ([string]::IsNullOrWhiteSpace($LogFile)) {
    $LogFile = Join-Path $Root ("logs\scheduler_{0}_utf8.log" -f (Get-Date -Format "yyyyMMdd"))
} elseif (-not [IO.Path]::IsPathRooted($LogFile)) {
    $LogFile = Join-Path $Root $LogFile
}

$logDir = Split-Path -Parent $LogFile
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$utf8 = [System.Text.UTF8Encoding]::new($false)

while ($true) {
    $lines = @(& python (Join-Path $Root "scripts\process_unread_batch.py") --allow-phone $AllowPhone 2>&1)
    foreach ($item in $lines) {
        $line = [string]$item
        [IO.File]::AppendAllText($LogFile, $line + [Environment]::NewLine, $utf8)
        Write-Host $line
    }
    Start-Sleep -Seconds $IntervalSeconds
}
