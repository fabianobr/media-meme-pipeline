#!/usr/bin/env python3
"""Queue the smallest validated LTX 2.3 native A/V workflow in ComfyUI."""

from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

import daily_reddit_meme_pipeline as pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--comfyui-url", default="http://127.0.0.1:8188")
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--seed", type=int, default=2028062801)
    return parser


def main() -> int:
    smoke = build_parser().parse_args()
    args = pipeline.build_parser().parse_args([])
    args.comfyui_url = smoke.comfyui_url
    args.ollama_url = None
    args.n8n_url = None
    pipeline.configure_service_urls(args)

    post = pipeline.reddit.RedditPost(
        subreddit="smoke-test",
        id="native-av-smoke",
        title="A cat's nickname has evolved until nobody remembers its original name",
        author="local",
        url="local://native-av-smoke",
        updated="",
        summary="The owner now calls the cat Beanster.",
        rank=1,
        media_type="text",
        media_url="",
    )
    concept = {
        "top_text": "MEU GATO TINHA NOME",
        "bottom_text": "AGORA É BEANSTER",
        "video_script": {
            "timeline": ["The owner looks at the cat, then looks at the camera with quiet disbelief"],
            "scene": "A simple Brazilian living room with one sofa and a plain wall",
            "character": "One fictional Brazilian adult sitting beside a calm house cat",
            "main_prop": "One cat resting beside the owner",
            "camera": "locked medium shot with a very slow push in",
            "dialogue": "MEU GATO TINHA NOME. AGORA É BEANSTER.",
            "audio": "Natural Brazilian Portuguese narration with dry comic timing",
        },
    }
    prompt = pipeline.compose_ltx23_segment_prompts(post, concept)[0]
    prefix = f"native-av-smoke/{int(time.time())}"
    prompt_id = pipeline.queue_comfy_ltx23_native_video(
        concept,
        post,
        prefix,
        smoke.seed,
        args,
        video_prompt_override=prompt,
        frames_override=args.ltx23_frames,
    )
    ref = pipeline.wait_for_comfy_video(prompt_id, smoke.timeout, 3.0)
    smoke.output.parent.mkdir(parents=True, exist_ok=True)
    pipeline.download_comfy_file(ref, smoke.output)
    metadata = (
        pipeline.probe_video_artifact(smoke.output)
        if shutil.which("ffprobe")
        else {"validation": "pending", "reason": "ffprobe unavailable in this client runtime"}
    )
    report = {
        "prompt_id": prompt_id,
        "workflow": concept["ltx23_workflow"],
        "output": str(smoke.output),
        "metadata": metadata,
        "prompt": prompt,
    }
    smoke.output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
