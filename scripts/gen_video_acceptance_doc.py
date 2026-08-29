#!/usr/bin/env python3
"""Generate docs/video-acceptance.md from specs/video-spec.json.

The JSON is the source of truth. This renders a human-readable companion so the
prose can never drift from the machine-checkable rules. Run after editing the
spec; CI/check_public_ready stays green only if the committed doc matches.

    python3 scripts/gen_video_acceptance_doc.py            # write the doc
    python3 scripts/gen_video_acceptance_doc.py --check     # exit 1 if stale
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = ROOT / "specs" / "video-spec.json"
DOC = ROOT / "docs" / "video-acceptance.md"


def render(spec: dict) -> str:
    audio = spec["audio"]
    style = spec["style"]
    forbidden = spec["forbidden_prompt_tokens"]
    lines = [
        "<!-- GENERATED from specs/video-spec.json by scripts/gen_video_acceptance_doc.py. Do not edit by hand. -->",
        "",
        "# Video acceptance requirements",
        "",
        "These are hard, machine-checkable requirements for every generated video.",
        "`scripts/grade-video.sh` computes each one as a number; `scripts/lint-prompt.sh`",
        "enforces the prompt-only rules before a render is ever submitted.",
        "",
        f"- **Target language:** {spec['target_language']} (Whisper language detection must return it).",
        f"- **TTS engine:** {spec['tts_engine']}; split narration into chunks of at most "
        f"{spec['max_tts_chars_per_chunk']} characters before synthesis.",
        f"- **Realism, not cartoon:** the prompt must contain at least "
        f"{style['required_realism_keyword_min_hits']} of: "
        + ", ".join(f"`{k}`" for k in style["required_realism_keywords"])
        + ".",
        "- **Forbidden style keywords** (prompt must contain none): "
        + ", ".join(f"`{k}`" for k in style["forbidden_style_keywords"])
        + ".",
        f"- **No silent gap longer than {audio['max_silent_gap_seconds']} s** "
        f"(`ffmpeg silencedetect` noise={audio['silencedetect_noise_db']} dB, "
        f"min {audio['silencedetect_min_duration_seconds']} s).",
        f"- **Speech-to-total ratio at least {audio['min_speech_to_total_ratio']:.2f}** "
        "(non-silent time / total duration).",
        f"- **Duration within ±{spec['duration']['tolerance_pct']}%** of the target seconds.",
        "- **No Spanish-language cue anywhere in the prompt** (word-boundary, case-insensitive):",
    ]
    for group, tokens in forbidden.items():
        if group.startswith("$"):
            continue
        lines.append(f"  - _{group.replace('_', ' ')}_: " + ", ".join(f"`{t}`" for t in tokens))
    lines += [
        "",
        "## Grading workflow (every render)",
        "",
        "1. `scripts/lint-prompt.sh <prompt-file>` before submitting -- aborts on any forbidden token.",
        "2. Render.",
        "3. `scripts/grade-video.sh <file> --target-seconds <N>` -> JSON verdict.",
        "4. Report **only the failing rules** to the human, with the computed value vs threshold.",
        "5. `grep` the full prompt text for Spanish cues once more before accepting the candidate.",
        "",
        "The approach (Edge-TTS pt-BR) is fixed. Do not switch to mute + external dubbing,",
        "or any other approach, without asking first.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="exit 1 if the doc is stale")
    args = ap.parse_args()

    spec = json.loads(SPEC.read_text())
    want = render(spec)

    if args.check:
        have = DOC.read_text() if DOC.exists() else ""
        if have.strip() != want.strip():
            print(f"{DOC} is stale; run: python3 scripts/gen_video_acceptance_doc.py", file=sys.stderr)
            return 1
        print(f"{DOC} is up to date.")
        return 0

    DOC.write_text(want + "\n")
    print(f"wrote {DOC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
