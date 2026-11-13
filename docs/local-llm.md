# Local LLM on the hub (GTX 1060 6GB)

The orchestrator's `local` provider talks to a llama-server sidecar on
the hub over plain HTTP (`studios.local/orchestrator.json`, `local`
section). No new Python dependency: the hub posts `/chat/completions`
with stdlib HTTP.

## Sizing

The 4B model at IQ4 is about 2.5GB of weights, so it fits the 6GB card
with room for 8k context (the q8 key-value cache costs roughly half a
gigabyte of VRAM even when prompts are short). The 1060 is Pascal (no
tensor cores, no Flash Attention), so
expect modest speed — fine for our tiny one-JSON prompts. Keep the
context small and watch VRAM while OBS and the browser are also up.
Hub verified: b12340 CUDA 12.4 build, 8k ctx, single slot, q8 KV,
~3.6GB VRAM resident, ~25 tok/s sustained generation. Repeat directs
prefill nearly free via prompt-cache reuse (the server keeps the shared
manifest prefix hot). First request after a restart pays graph warmup.

## Hub setup

1. Confirm the driver: run `nvidia-smi` on the hub (one command per
   line). Hub verified baseline: GTX 1060 6GB, driver 581.57, CUDA
   11.8 toolkit present, ~5.6GB VRAM free at idle. Re-check with the
   full stream stack up (OBS + browser eat VRAM too).
2. Get a llama.cpp CUDA release (the `llama-server` binary) and put it
   somewhere permanent next to the model folder. If the prebuilt CUDA
   binary won't offload to the 1060, use the Vulkan build instead (no
   CUDA needed) — or build from source against the installed 11.8
   toolkit with Pascal (sm_61) enabled.
3. Serve the model (example folder layout):

```bat
llama-server --model c:\LocalLLMs\gemma-4-E4B-it-IQ4_NL.gguf --alias gemma-4-E4B-it-IQ4_NL --port 8081 --ctx-size 8192 --n-gpu-layers 99 --parallel 1 --cache-type-k q8_0 --cache-type-v q8_0
```

Single slot, full GPU offload, lean KV cache: directs run strictly one
at a time, so everything serves that one request.

4. Verify from the hub: `curl.exe -s http://127.0.0.1:8081/health`
   should answer ok, and a chat probe should return a `choices` body.
5. Write the hub-local `config\studios.local\orchestrator.json`
   (git-ignored, ships with the normal deploy):

```json
{"local": {"base_url": "http://127.0.0.1:8081/v1", "model": "gemma-4-E4B-it-IQ4_NL"}}
```

6. On the dashboard, pick provider `local` and Direct a small look
   with explicit scope; eyeball every targeted fixture.

## Failure modes

- Dashboard 503 "not configured" = the hub has no `orchestrator.json`
  `local` section (write the file, redeploy or restart the hub task).
- Dashboard 502 "unreachable" = llama-server is not running (start it,
  then retry; nothing moved).
- Slow first look = cold model load; later looks reuse the loaded
  server. If looks time out, raise `timeout_s` in the `local` section.
