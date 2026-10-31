# SPDX-License-Identifier: AGPL-3.0-or-later
# Push to the Pi remote and verify the service answers its health endpoint.
# Optionally restarts the service first when $env:SUDO_PASSWORD is set in this session.
# Target host comes from -TargetHost or STUDIO_HUB_HOST (no hardcoded IPs).
param([string]$TargetHost = $env:STUDIO_HUB_HOST)
$ErrorActionPreference = "Stop"
if (-not $TargetHost) { throw "no Hub host: pass -TargetHost <ip> or set STUDIO_HUB_HOST on this PC" }

git push pi main

if ($env:SUDO_PASSWORD) {
    # Password via stdin: never appears in the remote process list, and shell
    # metacharacters in it cannot break the remote command line.
    $env:SUDO_PASSWORD | ssh -o BatchMode=yes pi@$TargetHost "sudo -S systemctl restart studio-hub"
}

$health = $null
for ($i = 0; $i -lt 12; $i++) {
    try {
        $health = Invoke-RestMethod http://${TargetHost}:8000/api/health -TimeoutSec 3
        break
    } catch {
        Start-Sleep 5
    }
}
if (-not $health) { throw "health check failed after push" }
$health | ConvertTo-Json
