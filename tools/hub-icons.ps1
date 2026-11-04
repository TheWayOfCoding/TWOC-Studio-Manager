# SPDX-License-Identifier: AGPL-3.0-or-later
# Install Start/Stop Studio Hub shortcuts for the interactive logon:
# Desktop + Start Menu, both flagged Run-as-administrator (the StudioHub
# task runs as SYSTEM at HIGHEST, so schtasks End/Run needs elevation;
# the flag makes Windows prompt, which works fine over VNC).
# Run once on the hub as the desktop user:
#   powershell -ExecutionPolicy Bypass -File tools\hub-icons.ps1
param([string]$HubRoot = "C:\studio\studio-hub")
$ErrorActionPreference = "Stop"

function Set-RunAsAdmin([string]$lnkPath) {
    $bytes = [IO.File]::ReadAllBytes($lnkPath)
    $bytes[21] = $bytes[21] -bor 32
    [IO.File]::WriteAllBytes($lnkPath, $bytes)
}

function New-HubShortcut([string]$folder, [string]$name, [string]$script, [string]$desc) {
    $shell = New-Object -ComObject WScript.Shell
    $lnkPath = Join-Path $folder "$name.lnk"
    $lnk = $shell.CreateShortcut($lnkPath)
    $lnk.TargetPath = Join-Path $HubRoot "tools\$script"
    $lnk.WorkingDirectory = $HubRoot
    $lnk.Description = $desc
    $lnk.Save()
    Set-RunAsAdmin $lnkPath
    Write-Host "created $lnkPath"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
foreach ($folder in @($desktop, $startMenu)) {
    New-HubShortcut $folder "Start Studio Hub" "hub-start.cmd" "Start the Studio Hub program (website + Bluetooth)"
    New-HubShortcut $folder "Stop Studio Hub" "hub-stop.cmd" "Stop the Studio Hub program and pause its watchdog"
}
Write-Host "ICONS_OK"
