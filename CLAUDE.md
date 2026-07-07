# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A local pipeline that turns public Reddit posts into short video memes: Reddit RSS → candidate selection → local vision description (Ollama) → humor candidates + independent humor critic → semantic video script → clean base image → literal LTX prompt compiler → ComfyUI LTX 2.3 native image-to-video with audio → local MP4 validation → optional Telegram review delivery. Everything runs locally (Ollama, ComfyUI); no hosted AI services. The README is in Portuguese; docs/architecture.md is in English.

This repository is public. Never commit generated media, `data/media-pipeline/`, `.env` files, tokens, Telegram IDs, model files, or run payloads. `HF_TOKEN` is read only from the environment. Run `./scripts/check_public_ready.sh` before publishing changes.

## Commands

```bash
# Dev setup (scripts-only, no models/infra)
python3 -m venv .venv && . .venv/bin/activate
python -m pip install -r requirements.lock

# Tests (unittest-based, run via pytest)
python3 -m pytest tests/
python3 -m pytest tests/test_configuration.py::ServiceUrlTests::test_telegram_is_opt_in  # single test

# Public-readiness gate: secret scan, py_compile, bash -n, git diff --check
./scripts/check_public_ready.sh

# Environment validation (OK / MISSING / OPTIONAL output)
python3 scripts/check_environment.py --mode dry-run   # Python, Reddit, Ollama
python3 scripts/check_environment.py --mode render --homelab-root ../homelab-ai  # + GPU, ComfyUI, LTX models
python3 scripts/check_environment.py --mode full --homelab-root ../homelab-ai    # + n8n

# Candidate selection only (no media, no Telegram)
python3 scripts/reddit_meme_dry_run.py --subreddit popular --limit 10 --cache-on-failure

# Full pipeline without rendering (no ComfyUI needed)
python3 scripts/daily_reddit_meme_pipeline.py --subreddit popular --limit 10 \
  --output-root data/media-pipeline/reddit-dry-run --run-tag dry-run --no-render --no-telegram

# Render test (requires ComfyUI + models; see README for full bootstrap)
python3 scripts/daily_reddit_meme_pipeline.py ... --make-video --video-engine ltx23 \
  --ltx23-input-mode image --only-index 1 --no-telegram

# Container runtime image (ffmpeg + fonts + pinned deps)
docker build -f infra/Dockerfile.runtime -t media-meme-pipeline:local .
```

Full install requires Ubuntu, an NVIDIA GPU with ≥16 GB VRAM, and the sibling `homelab-ai` repo checked out at the exact tag pinned in `infra/models.lock.yaml` (`./scripts/bootstrap.sh --homelab-root ../homelab-ai --homelab-tag v1.0.0`). Bootstrap is idempotent, never runs sudo, and starts only Ollama and ComfyUI.

## Architecture

- **`scripts/daily_reddit_meme_pipeline.py`** (~3800 lines) is the entire pipeline — selection, humor generation/critique, image generation, LTX rendering, validation, Telegram. It imports `reddit_meme_dry_run` as its Reddit selection module. Tests import it via `sys.path` insertion, so keep it importable (no side effects at import time).
- **Checked-in ComfyUI API workflows in `workflows/` are the graph source of truth.** Python only parameterizes their declared inputs, queues via `/prompt`, polls `/history`, then downloads and validates the MP4. Never build a hand-rolled LTX graph in Python. Default path is I2V: `workflows/05-ltx23-official-i2v-audio-api.json`, converted from the official ComfyUI template `video_ltx2_3_i2v` (distilled regime: CFG 1.0, manual sigmas, half-res base pass + x2 latent upsample + 3-step refine — do not reintroduce CFG>1/STG/LTXVScheduler there). T2V (`03-...`) is a technical baseline only; the hand-built I2V graph (`04-...`) is retired (guidance/regime mismatch caused pseudo-text and drift; see docs/experiments/).
- **Service URL precedence is CLI arg → environment variable → localhost default** (`--comfyui-url`/`COMFYUI_URL` → `http://localhost:8188`, likewise `OLLAMA_URL` :11434, `N8N_URL` :5678), implemented in `configure_service_urls()` and covered by tests. n8n/Telegram are opt-in legacy/optional paths; Telegram sends only with an explicit `--telegram`.
- **LTX prompt contract:** prompts sent to the video model must be literal cinematic descriptions — visible action, subject, object, environment, camera, light, sound. Internal metadata (setup/escalation/punchline labels, timestamps like `5-10s`) stays in JSON artifacts and must never reach the model. Enforced by `validate_ltx23_prompts()`.
- **Humor evaluation is adversarial by design:** writer and critic should use distinct models (`--humor-model` vs `--humor-critic-model`). Invalid, empty, or off-schema critic responses reject the concept — never assign artificial scores. `concepts.json` uses a versioned contract (`post`, `joke`, `evaluations`, `production`, `artifacts`, `execution`) with states `pending`/`running`/`approved`/`rejected`/`failed`.
- **Frozen inputs for reproducibility:** `--posts-file` replays a frozen selection without hitting Reddit; `--concepts-file` evaluates pre-curated concept seeds (they still go through the critic and deterministic checks — origin grants no auto-approval). Frozen fixtures and experiment logs live in `docs/experiments/`.
- **Every run does a single preflight** and writes `preflight.json`; missing required dependencies abort before any generation. The pipeline never installs dependencies during a run.
- Run artifacts go to `data/media-pipeline/` (gitignored). Dependencies are pinned in `requirements.lock` (Pillow, PyYAML, requests only); `infra/models.lock.yaml` pins model downloads with sha256 and the homelab tag.
- `agents/comfyui-specialist.md` is a checked-in specialist prompt (in Portuguese) for ComfyUI workflow design and GPU/VRAM troubleshooting — use it as context when working on `workflows/` or render paths.
