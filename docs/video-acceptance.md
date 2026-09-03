<!-- GENERATED from specs/video-spec.json by scripts/gen_video_acceptance_doc.py. Do not edit by hand. -->

# Video acceptance requirements

These are hard, machine-checkable requirements for every generated video.
`scripts/grade-video.sh` computes each one as a number; `scripts/lint-prompt.sh`
enforces the prompt-only rules before a render is ever submitted.

- **Target language:** pt-BR (Whisper language detection must return it).
- **TTS engine:** edge-tts; split narration into chunks of at most 240 characters before synthesis.
- **Realism, not cartoon:** the prompt must contain at least 1 of: `photorealistic`, `realistic`, `natural light`, `live-action`, `cinematic`.
- **Forbidden style keywords** (prompt must contain none): `cartoon`, `anime`, `illustration`, `illustrated`, `cgi`, `3d render`, `3d-render`, `drawing`, `sketch`, `stylized`, `comic`, `pixar`, `disney`, `claymation`, `low poly`, `vector art`.
- **No silent gap longer than 1.5 s** (`ffmpeg silencedetect` noise=-30 dB, min 0.3 s).
- **Speech-to-total ratio at least 0.55** (non-silent time / total duration).
- **Duration within ±10%** of the target seconds.
- **No Spanish-language cue anywhere in the prompt** (word-boundary, case-insensitive):
  - _spanish articles_: `el`, `un`, `los`, `las`, `una`, `unos`, `unas`, `del`, `al`
  - _spanish orthography_: `ción`, `cción`, `ñ`, `¿`, `¡`
  - _spanish words_: `muy`, `mucha`, `mucho`, `pero`, `con`, `sin`, `perro`, `niño`, `niña`, `están`, `hombre`, `mujer`, `gato negro`, `casa blanca`, `ahora`, `siempre`, `también`, `entonces`, `hacia`
  - _spanish phonetic hints_: `lluvia`, `calle`, `ustedes`, `vosotros`

## Grading workflow (every render)

1. `scripts/lint-prompt.sh <prompt-file>` before submitting -- aborts on any forbidden token.
2. Render.
3. `scripts/grade-video.sh <file> --target-seconds <N>` -> JSON verdict.
4. Report **only the failing rules** to the human, with the computed value vs threshold.
5. `grep` the full prompt text for Spanish cues once more before accepting the candidate.

The approach (Edge-TTS pt-BR) is fixed. Do not switch to mute + external dubbing,
or any other approach, without asking first.

