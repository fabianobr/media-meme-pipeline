# Runbook: ComfyUI render container

Operational reference for the ComfyUI service that renders LTX 2.3 video. The
container is defined in the sibling `homelab-ai` repo
(`infra/docker/docker-compose.yml`, service `comfyui`; image built from
`infra/docker/comfyui/Dockerfile`). This repo only talks to it over HTTP at
`http://localhost:8188` (precedence: `--comfyui-url` -> `COMFYUI_URL` -> that default).

For any change to the Dockerfile, a memory limit, or a systemd unit, use the
self-verifying harness in `homelab-ai` (`infra/scripts/apply-infra-change.sh`)
and the `infra-change` skill -- never edit-and-restart by hand, and never call a
fix done without a passing `comfy-smoke.sh` run.

---

## 1. Model file directory map

The container mounts the ComfyUI source at `/comfyui` and the shared model store
at `/mnt/models`. Many entries under `/comfyui/models/` are **symlinks** into
`/mnt/models/comfyui/...`; without the `/mnt/models` bind mount those symlinks
dangle inside the container and every model load fails.

| ComfyUI dir (`/comfyui/models/`) | Holds | `models.lock.yaml` id |
|---|---|---|
| `checkpoints/` | LTX 2.3 base checkpoint `ltx-2.3-22b-dev-fp8.safetensors` | `ltx-2.3-checkpoint` |
| `text_encoders/` | `gemma_3_12B_it_fp4_mixed.safetensors` (Gemma-3 12B text encoder) | `ltx-2.3-text-encoder` |
| `loras/` | `ltx_2.3_22b_distilled_1.1_lora_dynamic_*.safetensors` (distilled LoRA) | `ltx-2.3-distilled-lora-dynamic` |
| `latent_upscale_models/` | `ltx-2.3-spatial-upscaler-x2-1.1.safetensors` | `ltx-2.3-spatial-upscaler-x2` |
| `vae/`, `diffusion_models/` | Qwen-Image / ACE assets for the Wan S2V experiments (symlinked to `/mnt/models`) | -- |

`models.lock.yaml` (this repo) pins every download URL, revision, sha256 and the
`homelab` tag. `scripts/check_environment.py --mode render` verifies each
declared file and custom node is present before a run.

### Misplaced model files

A file in the wrong subdirectory presents as **"model not loaded" / node cannot
find the checkpoint**, not as a missing-file error. Checklist:

- Checkpoint loaders read `models/checkpoints/`; a separate text encoder must be
  in `models/text_encoders/` (not `clip/`); the spatial upscaler must be in
  `models/latent_upscale_models/`.
- The workflow JSON is the source of truth for the exact filename each loader
  expects -- grep the workflow for `.safetensors` and compare to `ls`.
- A broken symlink (`ls -l` shows a red target) means the `/mnt/models` mount is
  missing or the target moved. Historic example:
  `models/vae/qwen_image_vae.safetensors -> /mnt/models/comfyui/vae/...` dangled
  on a host without that mount; the Wan experiment worked around it by generating
  base images with `flux1-schnell-fp8` instead of Qwen-Image.

---

## 2. Memory limits and the exit 137 OOM diagnosis path

The `comfyui` service in `docker-compose.yml`:

```yaml
memswap_limit: 29g          # total RAM + swap for the container (finite on purpose)
deploy.resources.limits.memory: 27g
deploy.resources.limits.cpus: "4.0"
```

**History:** with the earlier `22g` / `24g` ceiling the LTX workflow (Gemma-3-12B
encoder + LTXAV, both offloaded) peaked around **27 GB combined RAM + CUDA pinned**
and Docker killed ComfyUI on **every** run of that workflow -- confirmed on
2026-08-02 via `docker events` showing `oom` plus container `exitCode=137`.
`memswap_limit` is kept finite (not `-1`) because unlimited swap previously let
`systemd-oomd` kill the host graphical session instead.

### Two independent memory ceilings -- do not conflate

| Ceiling | Symptom | Lever |
|---|---|---|
| **VRAM (16 GB)** | `CUDA out of memory` during the **refine** pass at final resolution (e.g. "allocation of 234 MiB failed") | lower `--ltx23-width/height`, fewer frames, free the GPU from Ollama |
| **Host RAM (~27 GB container / 32 GB box)** | ComfyUI process **killed with no traceback** shortly after `got prompt`; `exitCode=137` | fewer frames per single render; use `--ltx23-segments 2` for >8 s instead of a larger frame count |

Practical single-shot ceiling on the RTX 5060 Ti 16 GB + 27 GB container:
**~201 frames (8 s) @ 768x448** or **129 frames (5 s) @ 1024x576**. A 25 s
(257+ frame) single-shot render reliably trips the host-RAM ceiling and the
ComfyUI process dies silently -- go through segmented extension, not a bigger
frame count.

### Diagnosis path for a killed render

1. `docker inspect comfyui --format '{{.State.ExitCode}} {{.State.OOMKilled}}'`
   -- `137` and/or `OOMKilled=true` -> container memory limit.
2. `docker events --since 10m --filter container=comfyui` -- look for `oom`.
3. `dmesg -T | grep -iE 'killed process|oom'` -- kernel OOM killer (host-wide).
4. ComfyUI `/history/<id>` traceback mentioning `CUDA out of memory` -> VRAM, not
   container RAM.
5. `nvidia-smi` -- is Ollama (or `qwen*-coder`) still holding VRAM on the shared
   GPU? `OLLAMA_KEEP_ALIVE=300s` frees it eventually; the `OllamaFlushVRAM` /
   `ComfyUnloadModels` nodes force it inside a graph.
6. GPU health snapshot: `homelab-ai/infra/scripts/gpu-health.sh`.

---

## 3. Pinned Torch / CUDA and the runtime patch

The image is built **FROM** `pytorch/pytorch:2.13.0-cuda13.0-cudnn9-runtime`
(pinned by digest). Rationale, recorded in the Dockerfile:

- The base already ships Torch + CUDA 13.0 aligned. Installing a second Torch
  stack over a CUDA 12.8 image is what we are avoiding -- a mismatched Torch vs
  CUDA runtime is the classic source of `libnvrtc` / NVRTC-builtins load errors
  and `sm_120` "no kernel image" failures.
- CUDA 13.0 is required for **Blackwell (sm_120)** support, which the LTX / Gemma
  nvfp4 optimized dequant path needs on the RTX 5060 Ti.
- PyTorch 2.13 enforces PEP 668 on the image Python, so ComfyUI + custom-node
  deps go into a venv created with `--system-site-packages` at
  `/opt/comfyui-venv` -- it inherits the official Torch/CUDA stack without
  touching the system-managed environment. `PATH` puts that venv first.
- `PYTHONPATH=/opt/comfyui-python` adds a `sitecustomize.py` that shims
  `kornia.geometry.transform.pyramid.pad` to `torch.nn.functional.pad` for the
  mounted custom nodes (kornia dropped the symbol).
- The container runs with `--disable-cuda-malloc` (set in both the Dockerfile
  `CMD` and the compose `command`).

### CUDA / NVRTC library mismatch -- generic diagnosis

Symptoms: `undefined symbol` on a `libnvrtc*`/`libcudart*` call, `no kernel image
is available for execution on the device` (sm_120), or a node importing a CUDA
extension that fails only at runtime.

1. `homelab-ai/infra/scripts/gpu-health.sh` -- captures nvidia-smi, `torch.__version__`,
   `torch.version.cuda`, and whether the NVRTC builtins library is present.
2. Inside the container: `python -c "import torch; print(torch.__version__, torch.version.cuda); print(torch.cuda.get_device_capability())"`
   -- Torch CUDA must match the base image CUDA (13.0) and the capability must be
   `(12, 0)` for this GPU.
3. A custom node that pip-installed its own `torch`/`nvidia-*` wheels into the
   venv is the usual culprit -- reinstall the node without letting it pull Torch,
   or rebuild the image.
4. Never "fix" this by adding a second Torch index URL to `requirements.txt`;
   change the base image tag instead and run the harness.

---

## 4. Custom nodes with hardcoded hosts

These nodes assume Docker-network service names, not `localhost`. They work
**inside** the compose network and break if the node (or a workflow's default)
is pointed at a bare `localhost`:

| Node | Hardcoded default | Notes |
|---|---|---|
| `ComfyUI-OllamaFlushVRAM` (`homelab-ai/infra/docker/comfyui/custom_nodes/`) | `http://ollama:11434` | `ollama_url` input defaults to the service name; failures to reach Ollama are non-fatal (nothing to flush) |
| `comfyui-ollama` (OllamaGenerate / OllamaVision, third-party, in the image) | -- | workflows `07`, `08`, `09` hardcode `"http://ollama:11434"` in the node's `url` field |
| `ComfyUI` -> n8n callbacks | `http://comfyui:8188/prompt` | seen in the frontend workflow JSON; only valid from inside the network |

The pipeline itself never uses these -- it submits from the host and reads
`COMFYUI_URL` / `OLLAMA_URL` with a `localhost` default. If you run a workflow
that contains an Ollama node **from the host UI**, edit the node's `url` to
`http://localhost:11434` for that session, or run it from inside the container.

Locally-versioned helper nodes live in this repo at
`infra/comfyui-custom-nodes/` (`ComfyUI-ComfyUnloadModels`,
`ComfyUI-EdgeTTS-PTBR`) so the render path does not depend on nodes that only
exist hand-installed on one host.

---

## 5. Known-deprecated / regime-incompatible nodes

The checked-in API workflows are the graph source of truth. Do **not**
reintroduce these into the LTX 2.3 distilled graphs (`03`, `05`):

| Node / setting | Why it is out |
|---|---|
| `LTXVScheduler` | Graph `05` uses the official distilled sigma schedules (nodes 19 base / 30 refine). Replacing them with `LTXVScheduler` breaks the distilled regime. Contract is stated inline in `workflows/05-...json` (`sigma_contract`). |
| `CFGGuider` with `cfg > 1` / `STGGuiderAdvanced` / perturbed attention (STG) | The distilled LoRA is a **CFG 1.0** regime. CFG>1 + STG produced pseudo-text and identity drift regardless of prompt (`guidance_contract` in `workflows/05`). In CFG 1.0 the negative prompt is inert -- prohibitions in the positive prompt tend to render on screen. |
| `LTXVLoopingSampler` | Requires `STGGuiderAdvanced`, incompatible with the validated CFG 1.0 regime. For >8 s use `--ltx23-segments 2` (segment 2 anchored on segment 1's last frame, MP4s concatenated without re-encode). |
| Hand-built I2V graph `workflows/04-ltx23-native-i2v-audio-api.json` | **Retired.** Ran the distilled LoRA at CFG 3.0/7.0 + STG at a quarter of reference resolution with no refine pass -- a guidance/schedule mismatch, not a prompt problem. Kept only as a historical artifact; `03` (T2V) and `05` (official I2V) are the live graphs. |

Unrelated but adjacent -- the n8n container re-enables the `Execute Command` node
(disabled by default since n8n v2) for the `agents/youtube-etl` workflow; that is
n8n, not ComfyUI, and is documented in `homelab-ai`.
