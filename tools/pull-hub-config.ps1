# SPDX-License-Identifier: AGPL-3.0-or-later
# Pull the hub's studios.local down for review (read-only by default).
# Review lands in config/studios.hub-review/ (git-ignored) with a diff
# summary against this PC's copy. -Apply copies the hub copy over this
# PC's studios.local for missing+differing files (dev versions of
# differing files are stashed under studios.hub-review/dev-backup/ first),
# so the next deploy ships the merged result instead of clobbering it.
param(
    [string]$TargetHost = $env:STUDIO_HUB_HOST,
    [string]$TargetUser = "studio",
    [string]$InstallDir = "C:\studio\studio-hub",
    [switch]$Apply
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
if (-not $TargetHost) { throw "no Hub host: pass -TargetHost <ip> or set STUDIO_HUB_HOST on this PC" }
Set-Location $root

$hubZip = "C:/studio/hub-config.zip"
$hasHubConfig = ssh "$TargetUser@$TargetHost" "powershell -NoProfile -Command ""Test-Path $InstallDir/config/studios.local"""
if ($hasHubConfig -notmatch "True") { throw "hub has no studios.local (nothing to pull)" }
ssh "$TargetUser@$TargetHost" "powershell -NoProfile -Command ""Compress-Archive -Path $InstallDir/config/studios.local/* -DestinationPath $hubZip -Force"""
if ($LASTEXITCODE -ne 0) { throw "hub config download failed (remote zip)" }
$tmpZip = Join-Path ([IO.Path]::GetTempPath()) "studio-hub-pull.zip"
scp "$TargetUser@${TargetHost}:$hubZip" $tmpZip
if ($LASTEXITCODE -ne 0) { throw "hub config download failed (scp)" }
ssh "$TargetUser@$TargetHost" "del C:\studio\hub-config.zip"
$review = Join-Path $root "config\studios.hub-review"
Remove-Item $review -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -Path $tmpZip -DestinationPath $review -Force
Remove-Item $tmpZip -Force

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
$devDir = Join-Path $root "config\studios.local"
$devHashes = Get-DirHashes $devDir
$hubHashes = Get-DirHashes $review
$missing = @($hubHashes.Keys | Where-Object { -not $devHashes.ContainsKey($_) })
$differing = @($hubHashes.Keys | Where-Object { $devHashes.ContainsKey($_) -and $devHashes[$_] -ne $hubHashes[$_] })
$devOnly = @($devHashes.Keys | Where-Object { -not $hubHashes.ContainsKey($_) })
Write-Host "review copy: config/studios.hub-review/"
Write-Host ("hub-only (would be added): " + ($(if ($missing.Count) { $missing -join ", " } else { "(none)" })))
Write-Host ("differing (hub would win): " + ($(if ($differing.Count) { $differing -join ", " } else { "(none)" })))
Write-Host ("dev-only (untouched): " + ($(if ($devOnly.Count) { $devOnly -join ", " } else { "(none)" })))
if (-not $Apply) {
    Write-Host "read-only review; re-run with -Apply to take the hub copy."
    return
}
$backup = Join-Path $review "dev-backup"
foreach ($rel in ($missing + $differing)) {
    $dst = Join-Path $devDir ($rel.Replace("/", "\"))
    if (Test-Path $dst) {
        $stash = Join-Path $backup ($rel.Replace("/", "\"))
        New-Item -ItemType Directory -Path (Split-Path $stash) -Force | Out-Null
        Copy-Item $dst $stash -Force
    }
    New-Item -ItemType Directory -Path (Split-Path $dst) -Force | Out-Null
    Copy-Item (Join-Path $review ($rel.Replace("/", "\"))) $dst -Force
    Write-Host "applied from hub: $rel"
}
Write-Host "dev studios.local now matches the hub for pulled files."
