#!/usr/bin/env python3
"""Cheap visual smoke test for the official-template LTX 2.3 I2V graph.

Queues workflows/05-ltx23-official-i2v-audio-api.json with a short duration,
downloads the MP4, validates it with ffprobe and extracts still frames so a
human can check for pseudo-text and drift before any real meme render.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

import daily_reddit_meme_pipeline as pipeline

WORKFLOW = pipeline.PROJECT_ROOT / "workflows" / "05-ltx23-official-i2v-audio-api.json"
STILL_POSITIONS = (0.0, 0.25, 0.5, 0.75, 0.95)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True, help="Reference image (e.g. approved base image).")
    parser.add_argument("--output", type=Path, required=True, help="Destination MP4 path.")
    parser.add_argument(
        "--prompt",
        default=(
            "A calm house cat sits on a plain sofa in a simple living room and slowly turns its head "
            "toward the camera, holding a steady unimpressed stare. The camera stays locked. "
            "Soft indoor daylight. Quiet room tone."
        ),
    )
    parser.add_argument("--negative", default="pc game, console game, video game, cartoon, childish, ugly")
    parser.add_argument("--comfyui-url", default="http://127.0.0.1:8188")
    parser.add_argument("--seed", type=int, default=2028070701)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=25)
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--timeout", type=int, default=1800)
    return parser


def parameterize(graph: dict, args: argparse.Namespace, uploaded_image: str, prefix: str) -> dict:
    frames = int(args.fps * args.seconds) + 1
    graph["5"]["inputs"]["text"] = args.prompt
    graph["6"]["inputs"]["text"] = args.negative
    graph["7"]["inputs"]["frame_rate"] = float(args.fps)
    graph["8"]["inputs"]["image"] = uploaded_image
    graph["9"]["inputs"].update({"width": args.width, "height": args.height})
    graph["12"]["inputs"].update({"width": args.width // 2, "height": args.height // 2, "length": frames})
    graph["14"]["inputs"].update({"frames_number": frames, "frame_rate": args.fps})
    graph["16"]["inputs"]["noise_seed"] = args.seed
    graph["35"]["inputs"]["fps"] = float(args.fps)
    graph["36"]["inputs"]["filename_prefix"] = prefix
    return graph


def extract_stills(video_path: Path, duration: float) -> list[Path]:
    stills: list[Path] = []
    for position in STILL_POSITIONS:
        timestamp = max(0.0, duration * position)
        still = video_path.with_name(f"{video_path.stem}-still-{int(position * 100):03d}.png")
        pipeline.run_ffmpeg(
            ["-ss", f"{timestamp:.3f}", "-i", str(video_path), "-frames:v", "1", "-y", str(still)]
        )
        if still.is_file():
            stills.append(still)
    return stills


def main() -> int:
    smoke = build_parser().parse_args()
    args = pipeline.build_parser().parse_args([])
    args.comfyui_url = smoke.comfyui_url
    args.ollama_url = None
    args.n8n_url = None
    pipeline.configure_service_urls(args)

    document = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    graph = document["prompt"]
    uploaded_image = pipeline.upload_comfy_image(smoke.image)
    prefix = f"ltx23-official-smoke/{int(time.time())}"
    graph = parameterize(graph, smoke, uploaded_image, prefix)

    data = pipeline.request_json("POST", f"{pipeline.COMFYUI_URL}/prompt", json={"prompt": graph}, timeout=90)
    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"ComfyUI did not return prompt_id: {data}")
    print(f"queued prompt_id={prompt_id} prefix={prefix}")

    ref = pipeline.wait_for_comfy_video(prompt_id, smoke.timeout, 3.0)
    smoke.output.parent.mkdir(parents=True, exist_ok=True)
    pipeline.download_comfy_file(ref, smoke.output)

    if shutil.which("ffprobe"):
        metadata = pipeline.probe_video_artifact(smoke.output)
    else:
        metadata = {"validation": "pending", "reason": "ffprobe unavailable in this client runtime"}
    duration = float(metadata.get("duration") or smoke.seconds)
    stills = extract_stills(smoke.output, duration) if shutil.which("ffmpeg") else []

    report = {
        "prompt_id": prompt_id,
        "workflow": str(WORKFLOW.relative_to(pipeline.PROJECT_ROOT)),
        "output": str(smoke.output),
        "stills": [str(path) for path in stills],
        "metadata": metadata,
        "prompt": smoke.prompt,
        "seed": smoke.seed,
        "resolution": f"{smoke.width}x{smoke.height}",
        "frames": int(smoke.fps * smoke.seconds) + 1,
        "review_checklist": [
            "no pseudo-text, glyphs or UI marks in any still",
            "subject and composition match the reference image",
            "no identity/scene drift between first and last still",
        ],
    }
    smoke.output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
