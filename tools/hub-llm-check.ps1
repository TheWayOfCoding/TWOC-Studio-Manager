# SPDX-License-Identifier: AGPL-3.0-or-later
# Hub llama-server health + GPU proof + timing probe (run ON the hub, or via ssh).
$ErrorActionPreference = "Stop"
$port = 8081
$log = "C:\studio\logs\llm.log"
Write-Output "---VRAM---"
nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader
Write-Output "---TASK---"
schtasks /Query /TN StudioLLM /FO LIST | Select-String -Pattern 'TaskName|Status|Last Result'
Write-Output "---PROPS---"
$props = ConvertFrom-Json -InputObject (Out-String -InputObject (curl.exe -s --max-time 10 "http://127.0.0.1:$port/props"))
Write-Output ("SLOTS: " + $props.total_slots + " CTX: " + $props.default_generation_settings.n_ctx + " MODEL: " + $props.model_alias)
Write-Output "---LOG---"
Get-Content $log -Tail 25
Write-Output "---LATENCY---"
Get-Content $log -Tail 500 | Select-String -Pattern 'total time' | Select-Object -Last 3 | ForEach-Object { Write-Output $_.Line }
Write-Output "---PROBE600---"
$content = "Summarize in five words: " + ("lorem " * 600)
$body = @{
    model = "gemma-4-E4B-it-IQ4_NL"
    messages = @(@{role = "user"; content = $content })
    stream = $false
    temperature = 0
} | ConvertTo-Json -Depth 5 -Compress
$bodyFile = Join-Path ([IO.Path]::GetTempPath()) "llm-probe600.json"
Set-Content -Path $bodyFile -Value $body
$sw = [Diagnostics.Stopwatch]::StartNew()
$resp = curl.exe -s --max-time 600 -H "Content-Type: application/json" -d "@$bodyFile" "http://127.0.0.1:$port/v1/chat/completions"
$sw.Stop()
$r = ConvertFrom-Json -InputObject $resp
Write-Output ("WALL_S: " + [math]::Round($sw.Elapsed.TotalSeconds, 1))
Write-Output ("PROMPT_TOKENS: " + $r.usage.prompt_tokens)
Write-Output ("PROMPT_MS: " + $r.timings.prompt_ms)
Write-Output ("PREDICTED_PER_S: " + $r.timings.predicted_per_second)
Write-Output ("CONTENT: " + $r.choices[0].message.content)
Write-Output "CHECK_DONE"
