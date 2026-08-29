# Design: ComfyUI runbook + infra-change harness + spec-driven video grading

Date: 2026-08-29
Status: approved, implemented on branches
`feat/video-acceptance-comfyui-runbook` (this repo) and
`feat/comfyui-infra-change-harness` (`homelab-ai`).

## Problem

Four related asks landed together:

1. An operational runbook for the ComfyUI render container.
2. Hard, machine-checkable acceptance requirements for generated video.
3. A self-verifying harness for ComfyUI/Docker infra changes, plus a skill that
   mandates it.
4. A spec-driven, self-grading render loop.

They overlap: (2) and (4) are the same requirements at different fidelities;
(3) and (4) both need to submit to the ComfyUI API; (1) and (3) both want a
`docs/runbooks/comfyui.md`.

## Decisions

- **One source of truth for video requirements:** `specs/video-spec.json`.
  `docs/video-acceptance.md` is generated from it by
  `scripts/gen_video_acceptance_doc.py` (`--check` mode keeps them in sync).
- **Repos:** infra harness + `infra-change` skill live in `homelab-ai/infra/scripts`
  and `homelab-ai/.claude/skills` (next to the compose/Dockerfile they operate
  on). Video spec, grading, loop, and the runbook live in `media-meme-pipeline`
  (its operational docs).
- **TTS target:** Edge-TTS pt-BR (`pt-BR-*Neural`). The repo has no Piper; the
  240-char chunk rule is kept as a conservative Edge-TTS chunk size.
- **cu130 / NVRTC:** documented only what is verifiable -- the pinned
  `pytorch/pytorch:2.13.0-cuda13.0` base and its rationale (Blackwell sm_120 /
  nvfp4), the kornia `pad` shim, and a generic CUDA/NVRTC-mismatch diagnosis
  section.
- **Grading is mechanical:** every rule in `grade-video.sh` is a computed number
  (ffmpeg `silencedetect`, ffprobe duration, Whisper language + transcript).
  Missing ASR fails closed, never silently skips.
- **The loop never changes approach.** If it cannot pass within Edge-TTS pt-BR
  in 5 iterations it stops and reports the grade trend + one hypothesis; a human
  decides. The operator (Claude) must use `AskUserQuestion` before any
  approach-level change.

## Components

### media-meme-pipeline

| Path | Responsibility | Depends on |
|---|---|---|
| `specs/video-spec.json` | requirement values | -- |
| `scripts/gen_video_acceptance_doc.py` | render/verify `docs/video-acceptance.md` | stdlib |
| `scripts/lint-prompt.sh` | pre-render forbidden-token + realism gate | jq, spec |
| `scripts/grade-video.sh` | mechanical grade -> JSON verdict | ffmpeg, ffprobe, whisper, jq, python3, spec |
| `scripts/video-accept-loop.sh` | lint -> render -> grade -> mutate, cap 5 | the three above + a `--render-cmd` template |
| `docs/runbooks/comfyui.md` | container operations | -- |

`--render-cmd` is a template (`{prompt_file} {spoken_file} {negative_file}
{target_seconds} {out_mp4}`) so the loop is decoupled from the render
entrypoint and testable with a stub.

### homelab-ai

| Path | Responsibility |
|---|---|
| `infra/scripts/comfy-smoke.sh` | submit a minimal known-good workflow via `/prompt`+`/history`, sleep-poll, non-zero on error / exit 137 / timeout |
| `infra/scripts/gpu-health.sh` | nvidia-smi VRAM + torch/CUDA versions + NVRTC-builtins presence -> JSON |
| `infra/scripts/apply-infra-change.sh` | tag known-good -> apply -> rebuild -> restart -> gpu-health + comfy-smoke -> auto-rollback + diff on failure |
| `.claude/skills/infra-change/SKILL.md` | mandate the harness; no "fixed" without a passing smoke |

## Testing

- `lint-prompt.sh`: clean prompt passes; Spanish + cartoon prompt fails with the
  matched tokens listed.
- `grade-video.sh`: run against a known baseline MP4; verified silence/ratio/
  duration/language rules compute and the verdict flips on a wrong target.
- `video-accept-loop.sh`: run with a stub `--render-cmd`; verified lint gate,
  grade, PASS short-circuit, mutation, and trend report.
- Infra harness: proven by deliberately setting a too-low `memory:` limit,
  confirming `comfy-smoke.sh` catches exit 137 and `apply-infra-change.sh`
  auto-restores the known-good image. **Run on the operator's go, when the GPU
  is free** (per the approved plan).

## Deferred / not done autonomously

- The live infra rollback proof and the 25 s talking-cat loop run -- both need
  the shared GPU idle; handed back to the user.
