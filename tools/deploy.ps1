# SPDX-License-Identifier: AGPL-3.0-or-later
# Push to the Pi remote and verify the service answers its health endpoint.
# Optionally restarts the service first when $env:SUDO_PASSWORD is set in this session.
$ErrorActionPreference = "Stop"

git push pi main

if ($env:SUDO_PASSWORD) {
    ssh -o BatchMode=yes pi@192.168.1.150 "echo $env:SUDO_PASSWORD | sudo -S systemctl restart studio-hub"
}

$health = $null
for ($i = 0; $i -lt 12; $i++) {
    try {
        $health = Invoke-RestMethod http://192.168.1.150:8000/api/health -TimeoutSec 3
        break
    } catch {
        Start-Sleep 5
    }
}
if (-not $health) { throw "health check failed after push" }
$health | ConvertTo-Json
