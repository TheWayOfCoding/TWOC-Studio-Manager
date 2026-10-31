# SPDX-License-Identifier: AGPL-3.0-or-later
# StudioHub watchdog: restart the Hub task when its health endpoint stays dark.
# Runs every 2 minutes as SYSTEM via the StudioHubWatchdog scheduled task
# (created once; see the task-creation command at the bottom of this file).
# Never touches fixtures: it only restarts the web/API process.
$ErrorActionPreference = "Stop"
$HubRoot = Split-Path $PSScriptRoot -Parent
$LogFile = Join-Path $HubRoot "logs\watchdog.log"
$HealthUrl = "http://127.0.0.1:8000/api/health"

function Write-WatchdogLog([string]$Message) {
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    "$stamp $Message" | Out-File $LogFile -Append -Encoding utf8
    if ((Get-Item $LogFile).Length -gt 1MB) {
        Move-Item $LogFile "$LogFile.old" -Force
    }
}

function Test-HubHealth {
    try {
        $health = Invoke-RestMethod $HealthUrl -TimeoutSec 10
        return ($health.status -eq "ok") -or ($health.status -eq "degraded")
    } catch {
        return $false
    }
}

# Right after boot the Hub is still starting; leave it alone.
$uptime = (Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime
if ($uptime.TotalMinutes -lt 3) { exit 0 }

# Three checks ~15s apart: one slow GC pause must not trigger a restart.
for ($i = 0; $i -lt 3; $i++) {
    if (Test-HubHealth) { exit 0 }
    Start-Sleep 15
}

Write-WatchdogLog "health dark 3x; restarting StudioHub task"
& schtasks /End /TN StudioHub 2>&1 | Out-Null
Start-Sleep 5
& schtasks /Run /TN StudioHub 2>&1 | Out-Null
Start-Sleep 30
if (Test-HubHealth) {
    Write-WatchdogLog "restart recovered the Hub"
} else {
    Write-WatchdogLog "restart did NOT recover the Hub; needs human eyes"
}

# One-time task setup (elevated PowerShell on the Hub host):
# schtasks /Create /TN StudioHubWatchdog /SC MINUTE /MO 2 /RU SYSTEM /RL HIGHEST `
#   /TR "powershell -NoProfile -ExecutionPolicy Bypass -File C:\studio\studio-hub\tools\hub-watchdog.ps1" /F
