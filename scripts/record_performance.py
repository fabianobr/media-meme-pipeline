"""Record performance metrics for a published video.

Appends performance data (views, likes, etc.) to a central append-only JSON log,
keyed by publish_id and platform. Multiple measurements over time for the same
video are supported (each call adds a new timestamped entry).

Usage:
    python3 scripts/record_performance.py --publish-id <id> --platform <platform> \\
      --metric views=1200 --metric likes=84
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_metric(metric_str: str) -> tuple[str, Any] | None:
    """Parse a metric string 'key=value' into (key, parsed_value).

    Values are parsed as int if they parse cleanly as integers,
    float if they parse cleanly as floats, else kept as strings.

    Returns None if the string does not contain '='.
    """
    if "=" not in metric_str:
        return None

    key, value_str = metric_str.split("=", 1)

    # Try to parse as int
    try:
        return key, int(value_str)
    except ValueError:
        pass

    # Try to parse as float
    try:
        return key, float(value_str)
    except ValueError:
        pass

    # Keep as string
    return key, value_str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish-id", type=str, required=True, help="Unique identifier for the published video.")
    parser.add_argument("--platform", type=str, required=True, help="Platform where the video was published (e.g., youtube_shorts, tiktok).")
    parser.add_argument(
        "--metric",
        type=str,
        action="append",
        default=[],
        help="Metric in key=value format. Repeatable. Values parsed as int/float when possible, else string.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Path to the append-only JSON log file. Default: data/media-pipeline/performance-log.json relative to repo root.",
    )
    return parser


def main_with_args(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    # Determine log file path: use --log-file if given, else the repo-relative default
    if args.log_file is None:
        # Default to data/media-pipeline/performance-log.json relative to repo root
        repo_root = Path(__file__).resolve().parents[1]
        args.log_file = repo_root / "data" / "media-pipeline" / "performance-log.json"

    # Parse metrics
    metrics: dict[str, Any] = {}
    for metric_arg in args.metric:
        parsed = parse_metric(metric_arg)
        if parsed is None:
            print(f"ERROR malformed metric (missing '='): {metric_arg}")
            return 1
        key, value = parsed
        metrics[key] = value

    # Read existing log file or start fresh
    if args.log_file.is_file():
        data = json.loads(args.log_file.read_text(encoding="utf-8"))
    else:
        data = []

    # Append new record
    record = {
        "publish_id": args.publish_id,
        "platform": args.platform,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
    }
    data.append(record)

    # Write back (create parent directory if needed)
    args.log_file.parent.mkdir(parents=True, exist_ok=True)
    args.log_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # Print confirmation
    metric_count = len(metrics)
    metric_word = "metric" if metric_count == 1 else "metrics"
    print(f"Recorded performance for {args.publish_id} ({args.platform}): {metric_count} {metric_word}.")

    return 0


def main() -> int:
    import sys

    return main_with_args(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
