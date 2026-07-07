# Architecture

The pipeline turns public Reddit posts into reviewable short video memes.

```text
Reddit RSS
  -> candidate selection
  -> source image download when available
  -> local vision description
  -> humor candidates
  -> humor critic
  -> semantic video script
  -> clean base image
  -> LTX 2.3 prompt compiler
  -> validated ComfyUI native I2V audio/video workflow
  -> local review artifacts
  -> optional Telegram review delivery
```

## Data Flow

1. `reddit_meme_dry_run.py` reads public Reddit RSS feeds and ranks candidates.
2. `daily_reddit_meme_pipeline.py` downloads source media when present.
3. A local vision model describes the source image without sending it to a hosted service.
4. A local text model proposes meme structures and reviews the humor.
5. A clean base image is generated without visible text, logos, UI or captions.
6. The semantic script is converted into literal LTX prompts.
7. ComfyUI renders and encodes native video and audio from the checked-in I2V API workflow.
8. Python polls ComfyUI, downloads the MP4, validates it and records review metadata.

The checked-in ComfyUI API workflows are the graph source of truth. Python may parameterize their declared inputs, but must not maintain a separate hand-built LTX 2.3 graph. The default `ltx23` mode is image-to-video with `workflows/05-ltx23-official-i2v-audio-api.json`, converted from the official ComfyUI template `video_ltx2_3_i2v` (distilled regime: CFG 1.0, manual distilled sigmas, half-resolution base pass, x2 latent upsample, 3-step refine); text-to-video remains a technical baseline. The retired hand-built I2V graph (`04`) failed the visual gate because it ran the distilled LoRA with CFG 3.0/7.0 plus STG at a quarter of the reference resolution and no refine pass — a guidance/schedule regime mismatch, not a prompt problem. External TTS and FFmpeg composition are legacy/review fallbacks, not the publishable default.

## LTX Prompt Contract

LTX prompts are literal cinematic descriptions. They must describe visible action, subject, object, environment, camera, light, and sound. They must not ask the model to "make a meme", understand timing labels, or interpret abstract story beats.

Internal metadata such as setup, escalation, punchline, and timestamps stays in JSON artifacts and is not sent directly to the video model.

## Public Repo Boundaries

The repository tracks code, workflows, docs, and safe examples only. It does not track:

- generated videos or images;
- local Reddit/media run artifacts;
- `.env` files;
- Telegram IDs or tokens;
- model files;
- ComfyUI outputs;
- private logs.

## Backlog

Not implemented in the current extraction:

- comic background music;
- more expressive/comic voice acting.
