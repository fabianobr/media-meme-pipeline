#!/usr/bin/env python3
"""Finish the infographic-style reel: per-scene narration (Piper) + burned caption
(same as finalize_scenes.py), then combine the 3 scenes with a smooth crossfade
transition (ffmpeg xfade/acrossfade) instead of a hard cut, addressing the "photos
glued together" feedback.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import daily_reddit_meme_pipeline as p  # noqa: E402
import finalize_scenes as fs  # noqa: E402

SCENES_DIR = fs.SCENES_DIR
OUT_DIR = SCENES_DIR / "final"
TRANSITION = 0.8  # seconds
XFADE_STYLE = "smoothleft"  # slide-style transition; reads as intentional, not a cross-dissolve of unrelated content

SCENES = [
    {
        "name": "infog-scene1",
        "clip": SCENES_DIR / "ltx-infog" / "infog-scene1" / "infog-scene1-infog-ltx_00001_.mp4",
        "cc": "Sabe aquela dorzinha no dia seguinte da academia? Na real, seu músculo sofreu microlesões!",
        "narration": "Sabe aquela dorzinha gostosa no dia seguinte do treino? Relaxa, é normal! O que aconteceu foi que você causou microlesões nas fibras do músculo.",
    },
    {
        "name": "infog-scene2d",
        "clip": SCENES_DIR / "ltx-infog" / "infog-scene2d" / "infog-scene2d-infog-ltx_00001_.mp4",
        "cc": "É na hora de dormir e comer que os aminoácidos entram pra reformar tudo!",
        "narration": "E é aí que entra a mágica: quando você come proteína e dorme bem, seu corpo manda tijolinhos de aminoácidos pra reformar essa estrutura.",
    },
    {
        "name": "infog-scene3b",
        "clip": SCENES_DIR / "ltx-infog" / "infog-scene3b" / "infog-scene3b-infog-ltx_00001_.mp4",
        "cc": "O músculo reconstrói mais forte pra aguentar o tranco da próxima!",
        "narration": "O corpo é esperto: ele não só conserta o músculo, mas reconstrói ele mais forte pra aguentar mais peso no próximo treino!",
    },
]


def duration(path: Path) -> float:
    return p.audio_duration_seconds(path)


def crossfade_concat(clips: list[Path], out_path: Path, transition: float = TRANSITION) -> Path:
    durs = [duration(c) for c in clips]
    inputs = []
    for c in clips:
        inputs += ["-i", str(c)]

    vlabels = [f"{i}:v" for i in range(len(clips))]
    alabels = [f"{i}:a" for i in range(len(clips))]
    vfilters = []
    afilters = []
    cum = durs[0]
    vprev = vlabels[0]
    aprev = alabels[0]
    for i in range(1, len(clips)):
        voff = cum - transition
        vout = f"v{i}" if i < len(clips) - 1 else "vout"
        aout = f"a{i}" if i < len(clips) - 1 else "aout"
        vfilters.append(f"[{vprev}][{vlabels[i]}]xfade=transition={XFADE_STYLE}:duration={transition}:offset={voff:.3f}[{vout}]")
        afilters.append(f"[{aprev}][{alabels[i]}]acrossfade=d={transition}[{aout}]")
        cum = cum + durs[i] - transition
        vprev, aprev = vout, aout

    filter_complex = ";".join(vfilters + afilters)
    p.run_ffmpeg(
        inputs
        + [
            "-filter_complex", filter_complex,
            "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            str(out_path),
        ]
    )
    return out_path


def main():
    finished = [fs.finish_scene(scene) for scene in SCENES]
    final_reel = OUT_DIR / "muscle-recovery-infographic-reel.mp4"
    crossfade_concat(finished, final_reel)
    print(f"FINAL REEL: {final_reel}")


if __name__ == "__main__":
    main()
