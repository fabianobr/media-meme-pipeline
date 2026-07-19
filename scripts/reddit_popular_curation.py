#!/usr/bin/env python3
"""Curate an accumulating backlog of source-gate-approved r/popular posts.

r/popular is the fixed, non-negotiable source (project decision, 2026-07-15) instead of
the visual-heavy subreddits the main pipeline defaults to. Its RSS feed returns a fixed
page of ~25 entries with no pagination, and mixes image/video/text posts, most of which
fail the existing source-suitability gate (screenshots, scoreboards, text-only content).

This script does NOT try to reach a target backlog size in one run. It fetches the
current feed, skips posts already evaluated in a prior run (tracked by id), runs new
image posts through the same vision description + source-suitability gate the main
pipeline uses, and appends approvals to a persistent backlog file. Run it repeatedly
(e.g. daily) until the backlog reaches --target.

Video and text posts are skipped, not evaluated: the render engine is I2V-only today
(see CLAUDE.md), so there is no path yet to turn a video or text-only post into a
video-meme. Extending that is a separate, larger piece of work.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import reddit_meme_dry_run as reddit  # noqa: E402
import daily_reddit_meme_pipeline as pipeline  # noqa: E402


DEFAULT_BACKLOG_FILE = Path("data/media-pipeline/popular-curated-backlog.json")
DEFAULT_MEDIA_DIR = Path("data/media-pipeline/popular-curation-media")
SUBREDDIT = "popular"


def load_backlog(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"target": 0, "approved": [], "seen_ids": []}


def save_backlog(path: Path, backlog: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(backlog, ensure_ascii=False, indent=2), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Curate r/popular into a source-gate-approved backlog, accumulating across runs."
    )
    parser.add_argument("--target", type=int, default=20, help="Stop once the backlog reaches this many approvals.")
    parser.add_argument("--backlog-file", type=Path, default=DEFAULT_BACKLOG_FILE)
    parser.add_argument("--media-dir", type=Path, default=DEFAULT_MEDIA_DIR)
    parser.add_argument("--vision-model", default=pipeline.DEFAULT_VISION_MODEL)
    parser.add_argument("--source-critic-model", default="gemma3:12b")
    parser.add_argument("--vision-timeout", type=int, default=90)
    parser.add_argument(
        "--min-resolution",
        type=int,
        default=640,
        help="Reject sources whose downloaded image has a shorter side below this (thumbnails are unusable as the real-photo video foundation).",
    )
    parser.add_argument("--max-age-hours", type=int, default=72)
    parser.add_argument("--include-automoderator", action="store_true")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds for the RSS fetch.")
    parser.add_argument(
        "--rss-limit",
        type=int,
        default=100,
        help="Entries requested per RSS fetch via ?limit= (Reddit's own ceiling is 100; higher gets rate-limited).",
    )
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--backoff-base", type=float, default=5.0)
    parser.add_argument("--backoff-max", type=float, default=120.0)
    parser.add_argument("--jitter", type=float, default=1.5)
    parser.add_argument("--ollama-url", default=None, help="Overrides OLLAMA_URL env var and localhost default.")
    return parser


def prioritize_portrait(approved: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Portrait sources first (they fill the 9:16 canvas without blur-pad) among sources
    that are not a known LTX drift-attractor risk, preserving curation order within each
    group. Both priorities are soft only — landscape and drift-risk sources stay eligible,
    never dropped."""

    return sorted(
        approved,
        key=lambda item: (1 if item.get("drift_risk") else 0, 0 if item.get("portrait") else 1),
    )


def main() -> int:
    args = build_parser().parse_args()
    args.comfyui_url = None
    args.n8n_url = None
    pipeline.configure_service_urls(args)

    status, body, headers, attempts = reddit.fetch_feed(
        SUBREDDIT,
        timeout=args.timeout,
        retries=args.retries,
        backoff_base=args.backoff_base,
        backoff_max=args.backoff_max,
        jitter=args.jitter,
        limit=args.rss_limit,
    )
    if status != 200:
        print(f"ERROR could not fetch r/{SUBREDDIT}: status={status} attempts=[{'; '.join(attempts)}]")
        return 3

    parsed_posts = reddit.parse_feed(SUBREDDIT, body)
    posts = reddit.filter_posts(
        parsed_posts,
        max_age_hours=args.max_age_hours,
        include_automoderator=args.include_automoderator,
    )

    backlog = load_backlog(args.backlog_file)
    backlog["target"] = args.target
    seen_ids = set(backlog.get("seen_ids", []))
    approved = backlog.get("approved", [])

    print(f"r/{SUBREDDIT}: fetched {len(parsed_posts)} entries, {len(posts)} usable after filters.", flush=True)
    new_posts = [post for post in posts if post.id not in seen_ids]
    print(
        f"New (unseen) posts this run: {len(new_posts)}. Backlog before this run: {len(approved)}/{args.target}.",
        flush=True,
    )

    skipped_media_type = 0
    evaluated = 0
    approved_this_run = 0

    for i, post in enumerate(new_posts, 1):
        if len(approved) >= args.target:
            print(f"Backlog already at target ({args.target}); stopping before evaluating more posts.", flush=True)
            break
        print(f"[{i}/{len(new_posts)}] {post.media_type}: {post.title[:70]}", flush=True)
        if post.media_type != "image":
            skipped_media_type += 1
            seen_ids.add(post.id)
            backlog["seen_ids"] = sorted(seen_ids)
            save_backlog(args.backlog_file, backlog)
            continue

        args.media_dir.mkdir(parents=True, exist_ok=True)
        slug = pipeline.slugify(post.title)
        media_path = pipeline.download_source_media(post, args.media_dir / f"{post.rank:02d}-{slug}-source")
        if not media_path:
            seen_ids.add(post.id)
            backlog["seen_ids"] = sorted(seen_ids)
            save_backlog(args.backlog_file, backlog)
            continue

        # Resolution gate: the "narrated real photo" pipeline uses the source photo itself
        # as the video foundation, so a thumbnail-sized download (RSS sometimes only carries
        # a 140px preview, and external-preview URLs cannot be upgraded) is unusable.
        from PIL import Image as _Image
        with _Image.open(media_path) as _im:
            width, height = _im.size
        if min(width, height) < args.min_resolution:
            seen_ids.add(post.id)
            backlog["seen_ids"] = sorted(seen_ids)
            save_backlog(args.backlog_file, backlog)
            print(f"  rejected: fonte em baixa resolucao ({width}x{height}, minimo {args.min_resolution}px no lado menor)", flush=True)
            continue

        description = pipeline.describe_source_image(Path(media_path), args.vision_model, args.vision_timeout)
        evaluated += 1
        review = pipeline.assess_source_suitability(
            post, Path(media_path), description, args.source_critic_model, args.vision_timeout
        )
        seen_ids.add(post.id)
        status_word = "approved" if review.get("approved") else "rejected"
        print(f"  {status_word}: {review.get('reason', '')[:120]}", flush=True)

        if review.get("approved"):
            approved.append(
                {
                    "post": asdict(post),
                    "media_path": media_path,
                    "media_resolution": [width, height],
                    "portrait": height > width,
                    "drift_risk": bool(review.get("resting_domestic_animal_scene")),
                    "visual_description": description,
                    "review": review,
                    "curated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            approved_this_run += 1

        # Save after every evaluated post, not just at the end: vision-model calls are slow
        # (each image needs two Ollama round-trips) and a run can be interrupted or time out
        # partway through — losing unsaved progress means redoing those calls for nothing.
        backlog["approved"] = prioritize_portrait(approved)
        backlog["seen_ids"] = sorted(seen_ids)
        save_backlog(args.backlog_file, backlog)

    print(flush=True)
    print(
        f"Evaluated {evaluated} image posts this run ({skipped_media_type} video/text posts skipped, "
        "not eligible until the render engine supports non-image sources).",
        flush=True,
    )
    print(f"Approved this run: {approved_this_run}. Backlog total: {len(approved)}/{args.target}.", flush=True)
    if len(approved) < args.target:
        print(
            f"Run again later (e.g. daily) to accumulate the remaining {args.target - len(approved)}.", flush=True
        )
    print(f"Backlog file: {args.backlog_file}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
