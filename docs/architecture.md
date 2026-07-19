# Architecture

The pipeline turns public Reddit posts into reviewable short video memes.

```text
Reddit RSS (r/popular, ?limit=100)
  -> progressive curation + candidate selection
  -> source photo download (preview -> i.redd.it upgrade, >=640px gate)
  -> local vision description + source suitability gate
  -> humor candidates (local writer)
  -> deterministic checks + two independent critics (one with real vision)
  -> semantic video script
  -> literal LTX prompt compiler        -> local TTS narration (Piper)
  -> ComfyUI native LTX 2.3 I2V of the real photo (video-only)
  -> measured TTS audio mux
  -> local MP4 validation (Whisper + silencedetect)
  -> local review artifacts
  -> optional Telegram review delivery
```

## Data Flow

1. `reddit_popular_curation.py` progressively curates `r/popular` with a persistent backlog; `reddit_meme_dry_run.py` reads public Reddit RSS feeds (`?limit=100`) and ranks candidates.
2. `daily_reddit_meme_pipeline.py` downloads the source photo, upgrading preview URLs to `i.redd.it` and rejecting images below 640px.
3. A local vision model describes the source image without sending it to a hosted service, and a source suitability gate rejects unusable material deterministically (meaning-carrying embedded captions, multi-photo collages, scenes without motion potential).
4. A local text model proposes meme structures; deterministic checks (e.g. token-overlap against the source description) reject descriptive punchlines before any critic runs, then two independent critics evaluate — one text-only, one with real vision (the source image is sent as base64). Invalid, empty, or off-schema critic responses reject the concept.
5. The semantic script is converted into literal LTX prompts, and the narration is synthesized locally (Piper TTS by default); the measured narration duration determines the render's frame count.
6. ComfyUI renders native video from the checked-in I2V API workflow, animating the real downloaded photo (default `--ltx23-input-mode source`), in 2 segments when the clip exceeds the single-pass memory ceiling (~8s).
7. The measured TTS narration is muxed as the audio track (default `--ltx23-audio-mode tts`).
8. Python polls ComfyUI, downloads the MP4, validates it locally (Whisper transcription + silencedetect for words-to-duration calibration) and records review metadata.
9. Approved videos also get a publish package: locally generated pt-BR title/description/interest topics/hashtags (`publish.json`/`publish.txt`) and a 1080×1920 blur-padded `final_916.mp4` for Shorts/Reels/TikTok; the validated native MP4 is kept unchanged.

The checked-in ComfyUI API workflows are the graph source of truth. Python may parameterize their declared inputs, but must not maintain a separate hand-built LTX 2.3 graph. The default engine is `ltx23` image-to-video with `workflows/05-ltx23-official-i2v-audio-api.json`, converted from the official ComfyUI template `video_ltx2_3_i2v` (distilled regime: CFG 1.0, manual distilled sigmas, half-resolution base pass, x2 latent upsample, 3-step refine); text-to-video remains a technical baseline. The retired hand-built I2V graph (`04`) failed the visual gate because it ran the distilled LoRA with CFG 3.0/7.0 plus STG at a quarter of the reference resolution and no refine pass — a guidance/schedule regime mismatch, not a prompt problem.

The default render recipe (user-validated 2026-07-18) is the "narrated real photo": the I2V pass animates the real downloaded photo (not a re-generated clean base image), follows the source photo's orientation, and carries a locally synthesized Piper narration measured before rendering. Non-default alternatives remain available: `--ltx23-input-mode image` renders from a generated clean base image, `--ltx23-audio-mode native` uses LTX's own audio, `--video-engine photomotion` is a CPU-only engine (real photo, hard cuts per sentence, captions, TTS narration), and `--tts-backend edge` is a hosted legacy voice.

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
