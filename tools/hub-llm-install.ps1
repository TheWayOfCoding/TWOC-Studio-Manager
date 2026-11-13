# SPDX-License-Identifier: AGPL-3.0-or-later
# Install + register the hub llama-server sidecar (run ON the hub, or via ssh).
# Downloads the latest llama.cpp CUDA-12 Windows build, registers the
# StudioLLM ONSTART task (SYSTEM), starts it, and verifies inference.
# Re-runnable: re-downloads, re-registers the task, restarts the server.
$ErrorActionPreference = "Stop"

function Invoke-Schtasks($arguments) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $out = schtasks $arguments 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return @{ Output = @($out); Code = $code }
}

$binDir = "C:\LocalLLMs\bin"
$destDir = "$binDir\llama"
$zip = "$binDir\llama.zip"
$serveCmd = "$binDir\serve-llm.cmd"
$logDir = "C:\studio\logs"
$gguf = "c:\LocalLLMs\gemma-4-E4B-it-IQ4_NL.gguf"
$port = 8081

if (-not (Test-Path $gguf)) { throw "model file missing: $gguf" }
$json = curl.exe -s https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=20 | Out-String
$rels = ConvertFrom-Json -InputObject $json
$asset = $null
$relOfAsset = $null
foreach ($pattern in @('^llama-.*bin-win-cuda-12\.4.*x64\.zip$', '^llama-.*bin-win-cuda-12.*x64\.zip$')) {
    foreach ($rel in $rels) {
        foreach ($a in $rel.assets) {
            if ($a.name -match $pattern) { $asset = $a; $relOfAsset = $rel; break }
        }
        if ($asset) { break }
    }
    if ($asset) { break }
}
if (-not $asset) {
    Write-Output "NO_CUDA_ASSET"
    Write-Output ("API_HEAD: " + $json.Substring(0, [Math]::Min(300, $json.Length)))
    exit 1
}
Write-Output ("ASSET: " + $asset.name)
$support = $null
foreach ($a in $relOfAsset.assets) {
    if ($a.name -match 'cudart-llama-bin-win-cuda-12.*x64\.zip$') { $support = $a; break }
}
if ($support) { Write-Output ("SUPPORT: " + $support.name) } else { Write-Output "SUPPORT: none" }
$null = New-Item -ItemType Directory -Force -Path $binDir
$null = New-Item -ItemType Directory -Force -Path $logDir
curl.exe -sS -L -o $zip $asset.browser_download_url
if ((Get-Item $zip).Length -lt 1000000) { throw "download too small, refusing to expand" }
Invoke-Schtasks @('/End', '/TN', 'StudioLLM', '/F') | Out-Null
$prevErr = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
taskkill /F /IM llama-server.exe 2>&1 | Out-Null
$ErrorActionPreference = $prevErr
Start-Sleep 2
if (Test-Path $destDir) { Remove-Item $destDir -Recurse -Force }
Expand-Archive -Path $zip -DestinationPath $destDir -Force
$server = Get-ChildItem $destDir -Recurse -Include llama-server.exe |
    Select-Object -First 1 -ExpandProperty FullName
if (-not $server) { throw "llama-server.exe not found after expand" }
Write-Output ("SERVER: " + $server)
if ($support) {
    $szip = "$binDir\cudart.zip"
    curl.exe -sS -L -o $szip $support.browser_download_url
    Expand-Archive -Path $szip -DestinationPath (Split-Path $server) -Force
    Write-Output "SUPPORT_EXPANDED"
}
Set-Content -Path $serveCmd -Value "@echo off", `
    "`"$server`" --model $gguf --alias gemma-4-E4B-it-IQ4_NL --host 127.0.0.1 --port $port --ctx-size 8192 --n-gpu-layers 99 --parallel 1 --cache-type-k q8_0 --cache-type-v q8_0 >> `"$logDir\llm.log`" 2>&1"
Invoke-Schtasks @('/Delete', '/TN', 'StudioLLM', '/F') | Out-Null
$create = Invoke-Schtasks @('/Create', '/TN', 'StudioLLM', '/TR', $serveCmd, '/SC', 'ONSTART', '/RU', 'SYSTEM', '/RL', 'HIGHEST', '/F')
if ($create.Code -ne 0) { throw ("task registration failed: " + ($create.Output -join ' ')) }
$run = Invoke-Schtasks @('/Run', '/TN', 'StudioLLM')
if ($run.Code -ne 0) { throw ("task start failed: " + ($run.Output -join ' ')) }
$ok = $false
for ($i = 0; $i -lt 40; $i++) {
    try {
        $h = curl.exe -s --max-time 3 "http://127.0.0.1:$port/health" 2>$null
        if ($h -match '"status":"ok"') { $ok = $true; break }
    } catch { }
    Start-Sleep 3
}
if (-not $ok) { throw "server never reported healthy (see $logDir\llm.log)" }
Write-Output "LLM_HEALTHY"
$bodyFile = Join-Path ([IO.Path]::GetTempPath()) "llm-probe.json"
Set-Content -Path $bodyFile -Value '{"model":"gemma-4-E4B-it-IQ4_NL","messages":[{"role":"user","content":"Reply with the single word: ready"}],"stream":false,"temperature":0}'
$probe = curl.exe -s --max-time 180 -H "Content-Type: application/json" -d "@$bodyFile" "http://127.0.0.1:$port/v1/chat/completions"
Write-Output ("PROBE: " + $probe)
Get-Content "$logDir\llm.log" -Tail 60 |
    Select-String -Pattern 'CUDA|offload|n_gpu_layers|BLAS|CPU_Mapped' |
    Select-Object -Last 8 |
    ForEach-Object { Write-Output ("GPU: " + $_.Line) }
if ($probe -notmatch 'ready') { throw "inference probe did not answer ready" }
Write-Output "LLM_READY"
