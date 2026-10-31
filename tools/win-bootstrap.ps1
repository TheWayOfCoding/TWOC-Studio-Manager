# SPDX-License-Identifier: AGPL-3.0-or-later
# First-time (and repeat-safe) setup of the Studio Hub on a Windows host.
# Transfer the tree + config/studios.local/ first (USB, file share, or VNC),
# then from an elevated PowerShell in the deployed tree:
#   powershell -ExecutionPolicy Bypass -File tools\win-bootstrap.ps1
# Installs Python if missing, builds .venv, detects the IR port, opens the
# firewall, registers the StudioHub scheduled task (SYSTEM, at startup),
# registers the StudioHubWatchdog task (SYSTEM, every 2 min), starts the
# Hub, and health-checks. Safe to re-run.
param([string]$PythonVersion = "3.14.10")
$ErrorActionPreference = "Stop"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { throw "run this elevated: Start > PowerShell > right-click > Run as administrator" }

$root = Split-Path $PSScriptRoot -Parent
if ($root -match " ") { throw "install path must not contain spaces: $root" }
Write-Host "repo: $root"

# 1. Python (a real install: the Microsoft Store stub in WindowsApps counts as missing).
$py = Get-Command python -ErrorAction SilentlyContinue | Where-Object { $_.Source -notmatch "WindowsApps" } | Select-Object -First 1
if (-not $py) {
    $url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-amd64.exe"
    $dst = Join-Path $env:TEMP "python-setup.exe"
    Write-Host "downloading $url"
    curl.exe -sS -L -o $dst $url
    if ((Get-Item $dst).Length -lt 10MB) { throw "download failed (see $dst); try -PythonVersion with a release that ships a Windows installer" }
    Write-Host "installing Python $PythonVersion (per-user, quiet)"
    & $dst /quiet PrependPath=1 Include_test=0
    if ($LASTEXITCODE -ne 0) { throw "Python installer exited $LASTEXITCODE" }
    Remove-Item $dst -Force
    $mm = ($PythonVersion -split "\.")[0..1] -join ""
    $py = Get-Command (Join-Path $env:LOCALAPPDATA "Programs\Python\Python$mm\python.exe") -ErrorAction Stop
}
Write-Host "python: $($py.Source)"

# 2. Project venv + deps (bleak rides along in requirements.txt).
& $py.Source -m venv "$root\.venv"
& "$root\.venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r "$root\backend\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# 3. IR port: the Hub's own detector, stored machine-wide. Empty is fine
# (per-request override or a later setx); the Hub errors helpfully without one.
Push-Location "$root\backend"
$detected = & "$root\.venv\Scripts\python.exe" -c "from app.drivers.ir_usb_serial import default_port; print(default_port() or '')"
Pop-Location
if ($detected) { setx IR_PORT $detected /M | Out-Null; Write-Host "IR_PORT=$detected" }
else { Write-Warning "no USB serial port detected; plug in the IR module and run: setx IR_PORT COMx /M" }

# 4. Firewall (idempotent).
if (-not (Get-NetFirewallRule -DisplayName "Studio Hub" -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName "Studio Hub" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow | Out-Null
}

# 5. Scheduled task: SYSTEM, at startup, logs to logs\hub-task.log. The log
# dir must exist before the first start (the task redirect cannot create it).
$action = "cmd /c cd /d $root && .venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8000 >> logs\hub-task.log 2>&1"
schtasks /Create /TN StudioHub /TR $action /SC ONSTART /RU SYSTEM /RL HIGHEST /F | Out-Null
if ($LASTEXITCODE -ne 0) { throw "schtasks /Create failed" }
New-Item -ItemType Directory -Force "$root\logs" | Out-Null
schtasks /End /TN StudioHub 2>$null | Out-Null
Start-Sleep 3
schtasks /Run /TN StudioHub | Out-Null

# 5b. Watchdog: every 2 min as SYSTEM, restarts StudioHub when /api/health
# stays dark (skips the first 3 min after boot). Repeat-safe (/F).
$watchdog = "powershell -NoProfile -ExecutionPolicy Bypass -File $root\tools\hub-watchdog.ps1"
schtasks /Create /TN StudioHubWatchdog /SC MINUTE /MO 2 /RU SYSTEM /RL HIGHEST /TR "$watchdog" /F | Out-Null
if ($LASTEXITCODE -ne 0) { throw "schtasks /Create StudioHubWatchdog failed" }

# 6. Health-check loop.
$health = $null
for ($i = 0; $i -lt 12; $i++) {
    try { $health = Invoke-RestMethod http://127.0.0.1:8000/api/health -TimeoutSec 3; break }
    catch { Start-Sleep 5 }
}
if (-not $health) { throw "Hub did not answer; see $root\logs\hub-task.log" }
$health | ConvertTo-Json
Write-Host "BOOTSTRAP_OK"
