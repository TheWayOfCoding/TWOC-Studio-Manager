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
#
# Divergence guard: hub-side studios.local edits (OBS targets page, equipment
# page, presets) are compared per-file with this PC's copy before anything
# ships. Same path + different content aborts the deploy — resolve with
# tools\pull-hub-config.ps1 (review, then -Apply) or re-run with -Force to
# deliberately overwrite the hub's copy. Every deploy also takes a
# timestamped hub-side backup (C:\studio\backups, keeps 5) before restarting.
param(
    [string]$TargetHost = $env:STUDIO_HUB_HOST,
    [string]$TargetUser = "studio",
    [string]$InstallDir = "C:\studio\studio-hub",
    [switch]$Force
)
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = Split-Path $PSScriptRoot -Parent
if ($InstallDir -match " ") { throw "install dir must not contain spaces: $InstallDir" }
if (-not $TargetHost) { throw "no Hub host: pass -TargetHost <ip> or set STUDIO_HUB_HOST on this PC" }

function Get-Sha256($path) {
    # certutil, not Get-FileHash: the latter is missing on older hosts.
    $lines = certutil -hashfile $path SHA256 2>$null | Where-Object { $_ -match "^[0-9a-fA-F ]+$" }
    return (($lines -join "").Replace(" ", "").ToLower())
}

function Get-DirHashes($dir) {
    $hashes = @{}
    if (Test-Path $dir) {
        Get-ChildItem $dir -Recurse -File | ForEach-Object {
            $rel = $_.FullName.Substring($dir.Length + 1).Replace("\", "/")
            $hashes[$rel] = Get-Sha256 $_.FullName
        }
    }
    return $hashes
}

# Key auth must work non-interactively; fail fast with the fix, not a password prompt.
ssh -o BatchMode=yes -o ConnectTimeout=5 "$TargetUser@$TargetHost" "echo SSH_OK" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "passwordless SSH to $TargetUser@$TargetHost failed; do the one-time key setup in this script's header" }

# Guard: fetch the hub's studios.local and compare same-path hashes.
# Dev-only files are the payload (new work ships); hub-only files survive
# (expand never deletes); same path + different bytes is a real conflict.
$hubZip = "C:/studio/hub-config.zip"
$hasHubConfig = ssh "$TargetUser@$TargetHost" "powershell -NoProfile -Command ""Test-Path $InstallDir/config/studios.local"""
$tmpCfg = Join-Path ([IO.Path]::GetTempPath()) "studio-hub-cfg"
Remove-Item $tmpCfg -Recurse -Force -ErrorAction SilentlyContinue
if ($hasHubConfig -match "True") {
    ssh "$TargetUser@$TargetHost" "powershell -NoProfile -Command ""Compress-Archive -Path $InstallDir/config/studios.local/* -DestinationPath $hubZip -Force"""
    if ($LASTEXITCODE -ne 0) { throw "hub config download failed (remote zip)" }
    scp "$TargetUser@${TargetHost}:$hubZip" "$tmpCfg.zip"
    if ($LASTEXITCODE -ne 0) { throw "hub config download failed (scp)" }
    Expand-Archive -Path "$tmpCfg.zip" -DestinationPath $tmpCfg -Force
    Remove-Item "$tmpCfg.zip" -Force
    ssh "$TargetUser@$TargetHost" "del C:\studio\hub-config.zip"
}
$devHashes = Get-DirHashes (Join-Path $root "config\studios.local")
$hubHashes = Get-DirHashes $tmpCfg
$diverged = @()
foreach ($rel in $devHashes.Keys) {
    if ($hubHashes.ContainsKey($rel) -and $hubHashes[$rel] -ne $devHashes[$rel]) { $diverged += $rel }
}
$hubOnly = @($hubHashes.Keys | Where-Object { -not $devHashes.ContainsKey($_) })
Remove-Item $tmpCfg -Recurse -Force -ErrorAction SilentlyContinue
if ($hubOnly.Count -gt 0) {
    Write-Host ("hub-only files (kept, untouched): " + ($hubOnly -join ", "))
}
if ($diverged.Count -gt 0 -and -not $Force) {
    throw ("refusing deploy: hub studios.local differs in: " + ($diverged -join ", ") +
        ". Run tools\pull-hub-config.ps1 to review, -Apply to take the hub copy, or re-run with -Force to overwrite it.")
}
if ($diverged.Count -gt 0) {
    Write-Host ("FORCE: overwriting hub copy of: " + ($diverged -join ", "))
}

# File set: tracked + wanted untracked + real studio config. Never design notes.
Set-Location $root
$files = @()
$files += git ls-files
$files += git ls-files --others --exclude-standard 2>$null | Where-Object { $_ -notmatch "^pytest-of-" }
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
# Remote steps: expand the tree, then the hub-side step (backup + restart).
# The health loop below is the real gate: transport failure aborts here,
# a bad restart fails there.
ssh "$TargetUser@$TargetHost" "powershell -NoProfile -Command ""Expand-Archive -Path C:\studio\deploy.zip -DestinationPath $InstallDir -Force"""
if ($LASTEXITCODE -ne 0) { throw "remote update failed (SSH transport)" }
ssh "$TargetUser@$TargetHost" "powershell -NoProfile -ExecutionPolicy Bypass -File $InstallDir/tools/hub-deploy-step.ps1"
if ($LASTEXITCODE -ne 0) { throw "remote restart failed" }
ssh "$TargetUser@$TargetHost" "del C:\studio\deploy.zip"

# Health loop over a literal IPv4: mDNS names can resolve to an unreachable
# IPv6 first and stall .NET HTTP past the timeout (ssh falls through fine).
$healthHost = $TargetHost
try {
    $resolved = Resolve-DnsName $TargetHost -Type A -ErrorAction Stop |
        Where-Object { $_.IPAddress } | Select-Object -First 1 -ExpandProperty IPAddress
    if ($resolved) { $healthHost = $resolved.ToString() }
} catch { }
$health = $null
for ($i = 0; $i -lt 12; $i++) {
    # curl, not Invoke-RestMethod: .NET Framework TLS cannot handshake with
    # the hub's OpenSSL 3 stack. Probe order: https 8000, http 8001,
    # http 8000 (last covers pre-TLS single-listener hubs).
    foreach ($probe in @("https://${healthHost}:8000", "http://${healthHost}:8001", "http://${healthHost}:8000")) {
        try {
            $raw = curl.exe -sk --max-time 3 "$probe/api/health" 2>$null
            if ($raw) { $health = $raw | ConvertFrom-Json; break }
        } catch { }
    }
    if ($health) { break }
    Start-Sleep 5
}
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $zip -Force -ErrorAction SilentlyContinue
if (-not $health) { throw "Hub did not answer after deploy" }
$health | ConvertTo-Json
Write-Host "DEPLOY_OK"
