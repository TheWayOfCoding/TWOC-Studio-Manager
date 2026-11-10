# SPDX-License-Identifier: AGPL-3.0-or-later
# Hub-side deploy step. Runs ON the hub (via deploy-win.ps1, after the new
# tree lands): backs up studios.local timestamped (keeps 5), then restarts
# the StudioHub task. Backup failure warns but never blocks the restart —
# by this point the tree is already replaced, so restarting runs the only
# consistent code left.
param(
    [string]$InstallDir = "C:\studio\studio-hub",
    [string]$BackupRoot = "C:\studio\backups"
)
$ErrorActionPreference = "Stop"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$src = Join-Path $InstallDir "config\studios.local"
$dst = Join-Path $BackupRoot "studios.local-$stamp"
if (Test-Path $src) {
    try {
        New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
        Copy-Item $src $dst -Recurse -Force
        Write-Host "backup: $dst"
        Get-ChildItem $BackupRoot -Directory -Filter "studios.local-*" |
            Sort-Object Name -Descending | Select-Object -Skip 5 |
            Remove-Item -Recurse -Force
    } catch {
        Write-Warning "studios.local backup failed, restarting anyway: $_"
    }
} else {
    Write-Host "no studios.local to back up (first deploy)"
}
schtasks /End /TN StudioHub | Out-Null
Start-Sleep 4
schtasks /Run /TN StudioHub | Out-Null
Write-Host "StudioHub restarted"
