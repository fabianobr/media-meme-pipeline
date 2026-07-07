# LTX 2.3 native A/V smoke test — 2026-06-28

## Scope

Validate the checked-in ComfyUI native audio/video graph before scaling duration or rendering Reddit candidates.

Preset: 384×224, 49 frames, 24 fps, 8 steps, dynamic distilled LoRA, native audio and H.264 output.

## Results

All three technical outputs were valid MP4 files with H.264 video and AAC audio. Each duration was 2.042 seconds. No CUDA OOM occurred on the 16 GB GPU.

Visual quality did not pass:

1. Baseline speech prompt generated pseudo-subtitles and logo-like objects.
2. Short off-screen PT-BR speech removed extra products/logos but still generated pseudo-subtitles.
3. Ambience-only audio, with no speech, still generated pseudo-text and interface-like marks.

The last two controlled attempts used the same conservative preset and stopped at the planned limit.

## Conclusions

- The checked-in native A/V graph executes successfully on the installed ComfyUI nodes and models.
- Native speech was not the sole cause of visible text artifacts.
- Repeating negative-prompt variations is not justified.
- Free text-to-video does not provide enough composition control for this meme format at the tested preset.
- Model loading/offload dominates runtime: controlled tests took roughly five minutes despite producing only two seconds of video.
- ComfyUI must unload cached models after a completed/interrupted job; otherwise the next text-encoder stage can stall under RAM/swap pressure.

## Decision

Do not scale this T2V setup to 15 seconds or three candidates. The next experiment should use a clean reference image and a validated ComfyUI image-to-video graph. The reference image becomes the composition/text-artifact gate before GPU video generation.

Readable meme text should be added deliberately only after a clean I2V result. No local text-overlay node was available in the inspected ComfyUI inventory, and ElevenLabs nodes were excluded because they require an external service and credentials.
