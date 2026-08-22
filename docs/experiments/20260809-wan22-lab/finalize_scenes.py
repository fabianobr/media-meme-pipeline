#!/usr/bin/env python3
"""Finish the lab's actual deliverable: narração (Piper PT-BR, local) + legenda queimada
por cena, sobre os clipes LTX 2.3 já renderizados, depois concatenados em um reel único
9:16. Reaproveita as funções de produção do pipeline (TTS, desenho de texto, ffmpeg) via
sys.path insertion -- mesmo padrão que os testes do repo usam -- em vez de reimplementar
narração/legenda do zero.
"""
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path("/home/fabiano/code/media-meme-pipeline")
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
import daily_reddit_meme_pipeline as p  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

LAB = PROJECT_ROOT / "docs" / "experiments" / "20260809-wan22-lab"
SCENES_DIR = PROJECT_ROOT / "data" / "media-pipeline" / "20260809-1958-wan22-lab" / "scenes"
OUT_DIR = SCENES_DIR / "final"
OUT_DIR.mkdir(parents=True, exist_ok=True)
FONT = Path("/usr/share/fonts/truetype/noto/NotoSansDisplay-Bold.ttf")
WIDTH, HEIGHT = 512, 896
CAPTION_BG = (255, 209, 0, 255)  # solid yellow
CAPTION_TEXT = (20, 20, 20)  # near-black, for contrast on yellow

# Piper diction tuning: default voice inference is noise_scale=0.667, length_scale=1.0,
# noise_w=0.8 (see the .onnx.json). Slowing slightly + reducing noise variance makes
# consonants/vowel boundaries crisper instead of blurring together.
PIPER_LENGTH_SCALE = "1.12"
PIPER_NOISE_SCALE = "0.55"
PIPER_NOISE_W_SCALE = "0.55"

SCENES = [
    {
        "name": "scene1-microtears",
        "clip": SCENES_DIR / "ltx" / "scene1-microtears" / "scene1-microtears-ltx_00001_.mp4",
        "cc": "Sabe aquela dorzinha no dia seguinte da academia? Na real, seu músculo sofreu microlesões!",
        "narration": "Sabe aquela dorzinha gostosa no dia seguinte do treino? Relaxa, é normal! O que aconteceu foi que você causou microlesões nas fibras do músculo.",
    },
    {
        "name": "scene2-repair",
        "clip": SCENES_DIR / "ltx" / "scene2-repair" / "scene2-repair-ltx_00001_.mp4",
        "cc": "É na hora de dormir e comer que os aminoácidos entram pra reformar tudo!",
        "narration": "E é aí que entra a mágica: quando você come proteína e dorme bem, seu corpo manda tijolinhos de aminoácidos pra reformar essa estrutura.",
    },
    {
        "name": "scene3-result",
        "clip": SCENES_DIR / "ltx" / "scene3-result" / "scene3-result-ltx_00001_.mp4",
        "cc": "O músculo reconstrói mais forte pra aguentar o tranco da próxima!",
        "narration": "O corpo é esperto: ele não só conserta o músculo, mas reconstrói ele mais forte pra aguentar mais peso no próximo treino!",
    },
]


def draw_caption_block(draw: ImageDraw.ImageDraw, text: str, y: int, width: int, size: int) -> int:
    """Same wrapping/shrink-to-fit logic as the pipeline's draw_video_text_block, but with
    a fill color parameter (production version hardcodes white-on-black-stroke, unsuitable
    on a solid yellow background) and no stroke -- the yellow block itself gives contrast."""

    text = text.upper().strip()
    margin = max(28, width // 20)
    max_width = width - 2 * margin
    local_size = size
    while local_size >= 24:
        font = p.load_font(local_size, FONT)
        lines = p.wrap_text(draw, text, font, max_width)
        line_height = int(local_size * 1.15)
        block_height = line_height * len(lines)
        if block_height <= width * 0.32:
            break
        local_size -= 4
    for idx, line in enumerate(lines):
        font = p.load_font(local_size, FONT)
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (width - (bbox[2] - bbox[0])) / 2
        draw.text((x, y + idx * line_height), line, fill=CAPTION_TEXT, font=font)
    return block_height


def make_caption_overlay(text: str, out_path: Path) -> Path:
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    text_size = max(24, WIDTH // 13)
    margin = max(16, WIDTH // 28)

    # Measure first (on a scratch draw) so the yellow block height matches the text exactly.
    scratch = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    block_height = draw_caption_block(scratch, text, 0, WIDTH, text_size)
    band_height = block_height + 2 * margin
    draw.rectangle((0, 0, WIDTH, band_height), fill=CAPTION_BG)
    draw_caption_block(draw, text, margin, WIDTH, text_size)
    overlay.save(out_path)
    return out_path


PIPER_VOICE_PATH = Path.home() / ".local" / "share" / "piper-voices" / "pt_BR-cadu-medium.onnx"


def synthesize_clear_narration(text: str, output_path: Path) -> Path:
    """cadu voice (user A/B pick over faber/jeff for consonant-cluster clarity: 'treino',
    'estrutura muscular'), with length_scale/noise_scale/noise_w tuned for diction."""

    piper_bin = p.resolve_piper_binary()
    piper_voice = PIPER_VOICE_PATH
    if not piper_bin or not piper_voice.is_file():
        raise RuntimeError("Piper TTS (cadu voice) unavailable")
    wav_path = output_path.with_suffix(".wav")
    subprocess.run(
        [
            str(piper_bin), "-m", str(piper_voice), "-f", str(wav_path),
            "--length-scale", PIPER_LENGTH_SCALE,
            "--noise-scale", PIPER_NOISE_SCALE,
            "--noise-w-scale", PIPER_NOISE_W_SCALE,
        ],
        input=p.normalize_piper_text(text).encode("utf-8"),
        check=True,
    )
    return wav_path


def finish_scene(scene: dict) -> Path:
    name = scene["name"]
    workdir = OUT_DIR
    print(f"[{name}] synthesizing narration (Piper, tuned diction)...", flush=True)
    narration_raw = synthesize_clear_narration(scene["narration"], workdir / f"{name}-nar.mp3")
    narration_seconds = p.audio_duration_seconds(narration_raw)
    clip_seconds = p.audio_duration_seconds(scene["clip"])
    print(f"[{name}] narration={narration_seconds:.2f}s clip={clip_seconds:.2f}s", flush=True)

    caption_png = make_caption_overlay(scene["cc"], workdir / f"{name}-caption.png")

    target = max(narration_seconds + 0.4, clip_seconds)
    extend = max(0.0, target - clip_seconds)

    out_path = workdir / f"{name}-final.mp4"
    vf = f"[0:v]tpad=stop_mode=clone:stop_duration={extend:.3f}[base];[base][1:v]overlay=0:0:format=auto[vout]"
    p.run_ffmpeg(
        [
            "-i", str(scene["clip"]),
            "-i", str(caption_png),
            "-i", str(narration_raw),
            "-filter_complex", vf,
            "-map", "[vout]", "-map", "2:a:0",
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-t", f"{target:.3f}",
            "-movflags", "+faststart",
            str(out_path),
        ]
    )
    narration_raw.unlink(missing_ok=True)
    caption_png.unlink(missing_ok=True)
    return out_path


def main():
    finished = [finish_scene(scene) for scene in SCENES]
    final_reel = OUT_DIR / "muscle-recovery-final-reel.mp4"
    p.concatenate_video_segments(finished, final_reel)
    print(f"FINAL REEL: {final_reel}")
    for f in finished:
        print(f"  scene: {f}")


if __name__ == "__main__":
    main()
