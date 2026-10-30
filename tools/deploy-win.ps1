# SPDX-License-Identifier: AGPL-3.0-or-later
# Deploy the working tree to the Windows Hub host over SSH and health-check.
# One-time setup (passwordless SSH): on THIS pc run `ssh-keygen -t ed25519`,
# then on the Hub host in an elevated PowerShell append your id_ed25519.pub
# to C:\ProgramData\ssh\administrators_authorized_keys and lock it down:
#   icacls C:\ProgramData\ssh\administrators_authorized_keys /inheritance:r /grant SYSTEM:F /grant Administrators:F
# Afterwards: powershell -ExecutionPolicy Bypass -File tools\deploy-win.ps1 -TargetHost <hub-ip>
# (or set STUDIO_HUB_HOST once on this PC and run it bare)
# First-ever deploy still needs tools\win-bootstrap.ps1 run on the host once
# (this script restarts a task that must already exist).
param(
    [string]$TargetHost = $env:STUDIO_HUB_HOST,
    [string]$TargetUser = "studio",
    [string]$InstallDir = "C:\studio\studio-hub"
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
if ($InstallDir -match " ") { throw "install dir must not contain spaces: $InstallDir" }
if (-not $TargetHost) { throw "no Hub host: pass -TargetHost <ip> or set STUDIO_HUB_HOST on this PC" }

# Key auth must work non-interactively; fail fast with the fix, not a password prompt.
ssh -o BatchMode=yes -o ConnectTimeout=5 "$TargetUser@$TargetHost" "echo SSH_OK" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "passwordless SSH to $TargetUser@$TargetHost failed; do the one-time key setup in this script's header" }

# File set: tracked + wanted untracked + real studio config. Never design notes.
Set-Location $root
$files = @()
$files += git ls-files
$files += git ls-files --others --exclude-standard 2>$null | Where-Object { $_ -notmatch "^pytest-of-Scott/" }
$files += Get-ChildItem config/studios.local -Recurse -File | ForEach-Object {
    $_.FullName.Substring($root.Length + 1).Replace("\", "/")
}
$files = $files | Sort-Object -Unique
$stage = Join-Path ([IO.Path]::GetTempPath()) "studio-hub-deploy"
$zip = "$stage.zip"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $zip -Force -ErrorAction SilentlyContinue
foreach ($f in $files) {
    $dst = Join-Path $stage ($f.Replace("/", "\"))
    New-Item -ItemType Directory -Path (Split-Path $dst) -Force | Out-Null
    Copy-Item $f $dst -Force
}
Compress-Archive -Path "$stage\*" -DestinationPath $zip -Force

scp $zip "$TargetUser@${TargetHost}:C:/studio/deploy.zip"
if ($LASTEXITCODE -ne 0) { throw "scp failed" }
# Remote steps mirror the proven manual flow. The health loop below is the
# real gate: transport failure aborts here, a bad restart fails there.
$remote = "powershell -NoProfile -Command ""Expand-Archive -Path C:\studio\deploy.zip -DestinationPath $InstallDir -Force"" & schtasks /End /TN StudioHub & ping -n 4 127.0.0.1 >nul & schtasks /Run /TN StudioHub & del C:\studio\deploy.zip"
ssh "$TargetUser@$TargetHost" $remote
if ($LASTEXITCODE -ne 0) { throw "remote update failed (SSH transport)" }

$health = $null
for ($i = 0; $i -lt 12; $i++) {
    try { $health = Invoke-RestMethod "http://${TargetHost}:8000/api/health" -TimeoutSec 3; break }
    catch { Start-Sleep 5 }
}
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $zip -Force -ErrorAction SilentlyContinue
if (-not $health) { throw "Hub did not answer after deploy" }
$health | ConvertTo-Json
Write-Host "DEPLOY_OK"
