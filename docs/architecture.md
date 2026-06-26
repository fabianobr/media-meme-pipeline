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
  -> LTX 2.3 prompt compiler
  -> 3 video segments
  -> PT-BR narration mix
  -> local review artifacts
  -> optional Telegram review delivery
```

## Data Flow

1. `reddit_meme_dry_run.py` reads public Reddit RSS feeds and ranks candidates.
2. `daily_reddit_meme_pipeline.py` downloads source media when present.
3. A local vision model describes the source image without sending it to a hosted service.
4. A local text model proposes meme structures and reviews the humor.
5. The semantic script is converted into literal LTX prompts.
6. ComfyUI renders a first T2V segment, then two I2V continuations using the previous segment's final frame.
7. Edge TTS creates PT-BR narration, then `ffmpeg` mixes it with the rendered video.

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

