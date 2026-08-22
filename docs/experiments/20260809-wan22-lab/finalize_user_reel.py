#!/usr/bin/env python3
"""Finish the user-prompted reel (3D anatomical / hologram / photoreal style, Wan 2.2):
per-scene narration (Piper) + burned caption, combined with the same slide-style
crossfade used for the infographic reel.
"""
import sys
from pathlib import Path

sys.path.insert(0, "/home/fabiano/code/media-meme-pipeline/scripts")
import finalize_scenes as fs  # noqa: E402
import finalize_infographic_reel as fi  # noqa: E402

SCENES_DIR = fs.SCENES_DIR
OUT_DIR = SCENES_DIR / "final"

SCENES = [
    {
        "name": "user-scene1",
        "clip": SCENES_DIR / "wan-user" / "user-scene1" / "user-scene1-wan_00001_.mp4",
        "cc": "Sabe aquela dorzinha no dia seguinte da academia? Na real, seu músculo sofreu microlesões!",
        "narration": "Sabe aquela dorzinha gostosa no dia seguinte do treino? Relaxa, é normal! O que aconteceu foi que você causou microlesões nas fibras do músculo.",
    },
    {
        "name": "user-scene2",
        "clip": SCENES_DIR / "wan-user" / "user-scene2" / "user-scene2-wan_00001_.mp4",
        "cc": "É na hora de dormir e comer que os aminoácidos entram pra reformar tudo!",
        "narration": "E é aí que entra a mágica: quando você come proteína e dorme bem, seu corpo manda tijolinhos de aminoácidos pra reformar essa estrutura.",
    },
    {
        "name": "user-scene3",
        "clip": SCENES_DIR / "wan-user" / "user-scene3" / "user-scene3-wan-retry_00001_.mp4",
        "cc": "O músculo reconstrói mais forte pra aguentar o tranco da próxima!",
        "narration": "O corpo é esperto: ele não só conserta o músculo, mas reconstrói ele mais forte pra aguentar mais peso no próximo treino!",
    },
]


def main():
    finished = [fs.finish_scene(scene) for scene in SCENES]
    final_reel = OUT_DIR / "muscle-recovery-user-reel.mp4"
    fi.crossfade_concat(finished, final_reel)
    print(f"FINAL REEL: {final_reel}")


if __name__ == "__main__":
    main()
