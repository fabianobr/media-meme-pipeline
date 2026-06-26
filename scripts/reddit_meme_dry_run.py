#!/usr/bin/env python3
"""Dry-run selector for Reddit-based meme candidates.

This script is intentionally read-first and safe by default:
- no image generation
- no Telegram send
- no Hermes cron creation
- no cache writes unless --write-cache is passed

It uses Reddit Atom/RSS feeds because the unauthenticated JSON endpoints are
often blocked or credit/rate-limit hostile in this environment.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


ATOM = "{http://www.w3.org/2005/Atom}"
DEFAULT_SUBREDDITS = ["brasil"]
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "media-meme-pipeline" / "reddit-rss"


@dataclass
class RedditPost:
    subreddit: str
    id: str
    title: str
    author: str
    url: str
    updated: str
    summary: str
    rank: int
    media_type: str = "text"
    media_url: str = ""


def clean_text(value: str | None) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(value.split())


def preview(value: str, limit: int = 150) -> str:
    value = " ".join((value or "").split())
    return value[:limit] + ("..." if len(value) > limit else "")


def extract_media_url(raw_html: str, fallback_url: str) -> tuple[str, str]:
    candidates = re.findall(r'href="([^"]+)"', raw_html or "")
    candidates.extend(re.findall(r'src="([^"]+)"', raw_html or ""))
    decoded = [html.unescape(candidate) for candidate in candidates]

    for url in decoded:
        lower = url.lower()
        if any(lower.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
            return "image", url
        if "i.redd.it" in lower or "preview.redd.it" in lower:
            return "image", url

    for url in decoded:
        lower = url.lower()
        if any(lower.endswith(ext) for ext in (".mp4", ".mov", ".webm")) or "v.redd.it" in lower:
            return "video", url

    lower_fallback = fallback_url.lower()
    if "i.redd.it" in lower_fallback or "preview.redd.it" in lower_fallback:
        return "image", fallback_url
    if "v.redd.it" in lower_fallback:
        return "video", fallback_url
    return "text", ""


def feed_url(subreddit: str) -> str:
    return f"https://www.reddit.com/r/{urllib.parse.quote(subreddit)}/.rss"


def cache_path(cache_dir: Path, subreddit: str) -> Path:
    digest = hashlib.sha256(subreddit.encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"{subreddit}-{digest}.xml"


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def should_retry(status: int | None) -> bool:
    return status is None or status == 429 or 500 <= status <= 599


def fetch_feed_once(subreddit: str, timeout: int) -> tuple[int | None, str, dict[str, str]]:
    request = urllib.request.Request(
        feed_url(subreddit),
        headers={
            "User-Agent": "Mozilla/5.0 media-meme-pipeline-reddit-rss-dry-run/0.2",
            "Accept": "application/atom+xml,application/rss+xml,text/xml;q=0.9,*/*;q=0.5",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace"), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")[:500], dict(exc.headers)
    except Exception as exc:  # noqa: BLE001 - dry-run diagnostics should not hide transport errors
        return None, str(exc), {}


def fetch_feed(
    subreddit: str,
    timeout: int,
    retries: int,
    backoff_base: float,
    backoff_max: float,
    jitter: float,
) -> tuple[int | None, str, dict[str, str], list[str]]:
    attempts: list[str] = []
    max_attempts = max(1, retries + 1)
    last_status: int | None = None
    last_body = ""
    last_headers: dict[str, str] = {}

    for attempt in range(1, max_attempts + 1):
        status, body, headers = fetch_feed_once(subreddit, timeout=timeout)
        last_status, last_body, last_headers = status, body, headers
        attempts.append(f"attempt={attempt} status={status}")

        if not should_retry(status) or attempt == max_attempts:
            break

        retry_after = parse_retry_after(headers.get("retry-after"))
        exponential = min(backoff_max, backoff_base * (2 ** (attempt - 1)))
        sleep_seconds = retry_after if retry_after is not None and retry_after > 0 else exponential
        if jitter > 0:
            sleep_seconds += random.uniform(0, jitter)
        attempts[-1] += f" sleep={sleep_seconds:.1f}s"
        time.sleep(sleep_seconds)

    return last_status, last_body, last_headers, attempts


def parse_feed(subreddit: str, xml_text: str) -> list[RedditPost]:
    root = ET.fromstring(xml_text)
    posts: list[RedditPost] = []
    for rank, entry in enumerate(root.findall(f"{ATOM}entry"), 1):
        title = clean_text(entry.findtext(f"{ATOM}title"))
        if not title:
            continue

        author_el = entry.find(f"{ATOM}author")
        author = "?"
        if author_el is not None:
            author = clean_text(author_el.findtext(f"{ATOM}name")) or "?"

        link = ""
        for link_el in entry.findall(f"{ATOM}link"):
            href = link_el.attrib.get("href", "")
            if link_el.attrib.get("rel") == "alternate" or not link:
                link = href

        updated = clean_text(entry.findtext(f"{ATOM}updated"))
        raw_content = entry.findtext(f"{ATOM}content") or entry.findtext(f"{ATOM}summary") or ""
        summary = clean_text(raw_content)
        if "nsfw" in title.lower() or "over 18" in summary.lower():
            continue
        media_type, media_url = extract_media_url(raw_content, link)

        posts.append(
            RedditPost(
                subreddit=subreddit,
                id=clean_text(entry.findtext(f"{ATOM}id")) or link,
                title=title,
                author=author,
                url=link,
                updated=updated,
                summary=summary,
                rank=rank,
                media_type=media_type,
                media_url=media_url,
            )
        )
    return posts


def parse_updated(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def filter_posts(posts: list[RedditPost], max_age_hours: int, include_automoderator: bool) -> list[RedditPost]:
    now = datetime.now(timezone.utc)
    filtered: list[RedditPost] = []
    for post in posts:
        if not include_automoderator and post.author.lower().endswith("automoderator"):
            continue
        updated = parse_updated(post.updated)
        if updated is not None:
            age_hours = (now - updated).total_seconds() / 3600
            if age_hours > max_age_hours:
                continue
        filtered.append(post)
    return filtered


def load_cached_feed(cache_dir: Path, subreddit: str) -> str | None:
    path = cache_path(cache_dir, subreddit)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def write_cached_feed(cache_dir: Path, subreddit: str, xml_text: str) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path(cache_dir, subreddit).write_text(xml_text, encoding="utf-8")


def select_posts(posts: list[RedditPost], limit: int, max_per_subreddit: int) -> list[RedditPost]:
    by_subreddit: dict[str, list[RedditPost]] = {}
    for post in posts:
        by_subreddit.setdefault(post.subreddit, []).append(post)

    selected: list[RedditPost] = []
    seen: set[str] = set()
    per_subreddit: dict[str, int] = {}

    # First pass: interleave sources with a diversity cap.
    max_depth = max((len(items) for items in by_subreddit.values()), default=0)
    for depth in range(max_depth):
        for subreddit in sorted(by_subreddit):
            if len(selected) >= limit:
                return selected
            items = by_subreddit[subreddit]
            if depth >= len(items):
                continue
            post = items[depth]
            if post.id in seen:
                continue
            if per_subreddit.get(subreddit, 0) >= max_per_subreddit:
                continue
            seen.add(post.id)
            per_subreddit[subreddit] = per_subreddit.get(subreddit, 0) + 1
            selected.append(post)

    # Fallback pass: if only one or two feeds worked, fill the target from all available posts.
    for post in posts:
        if len(selected) >= limit:
            break
        if post.id in seen:
            continue
        seen.add(post.id)
        selected.append(post)

    return selected


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run Reddit RSS meme candidate selector.")
    parser.add_argument("--subreddit", action="append", dest="subreddits", help="Subreddit to read. Repeatable.")
    parser.add_argument("--limit", type=int, default=10, help="Number of candidates to select.")
    parser.add_argument("--max-per-subreddit", type=int, default=3, help="Diversity cap before fallback fill.")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between Reddit feed requests.")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout in seconds.")
    parser.add_argument("--retries", type=int, default=3, help="Retries per subreddit for 429/5xx/network errors.")
    parser.add_argument("--backoff-base", type=float, default=5.0, help="Initial retry backoff in seconds.")
    parser.add_argument("--backoff-max", type=float, default=120.0, help="Maximum retry backoff in seconds.")
    parser.add_argument("--jitter", type=float, default=1.5, help="Random jitter added to retry sleeps.")
    parser.add_argument("--max-age-hours", type=int, default=72, help="Ignore posts older than this many hours.")
    parser.add_argument("--include-automoderator", action="store_true", help="Include AutoModerator posts.")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="RSS cache directory.")
    parser.add_argument("--write-cache", action="store_true", help="Write successful RSS responses to cache.")
    parser.add_argument(
        "--cache-on-failure",
        action="store_true",
        help="Use cached RSS when live fetch is rate-limited or blocked.",
    )
    parser.add_argument("--json", action="store_true", help="Print selected candidates as JSON.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    subreddits = args.subreddits or DEFAULT_SUBREDDITS

    print("# Dry-run: Reddit RSS Meme Pipeline")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print("Mode: RSS + selecao, no images, no Telegram, no cron")
    print(f"Subreddits: {', '.join('r/' + s for s in subreddits)}")
    print()

    all_posts: list[RedditPost] = []
    warnings: list[str] = []

    print("## Fetch feeds")
    for index, subreddit in enumerate(subreddits, 1):
        if index > 1 and args.delay > 0:
            time.sleep(args.delay)

        status, body, headers, attempts = fetch_feed(
            subreddit,
            timeout=args.timeout,
            retries=args.retries,
            backoff_base=args.backoff_base,
            backoff_max=args.backoff_max,
            jitter=args.jitter,
        )
        source = "live"
        if status != 200:
            warning = f"r/{subreddit}: status={status}"
            retry_after = headers.get("retry-after") or headers.get("x-ratelimit-reset")
            if retry_after:
                warning += f" retry_after={retry_after}"
            if attempts:
                warning += " attempts=[" + "; ".join(attempts) + "]"
            cached = load_cached_feed(args.cache_dir, subreddit) if args.cache_on_failure else None
            if cached:
                body = cached
                source = "cache"
                warnings.append(warning + " using_cache=true")
            else:
                warnings.append(warning)
                print(f"- {warning}")
                continue

        try:
            parsed_posts = parse_feed(subreddit, body)
            posts = filter_posts(
                parsed_posts,
                max_age_hours=args.max_age_hours,
                include_automoderator=args.include_automoderator,
            )
        except Exception as exc:  # noqa: BLE001 - report parse failure in dry-run
            warnings.append(f"r/{subreddit}: parse_error={exc}")
            print(f"- r/{subreddit}: parse_error={exc}")
            continue

        if source == "live" and args.write_cache:
            write_cached_feed(args.cache_dir, subreddit, body)

        all_posts.extend(posts)
        attempt_note = " attempts=[" + "; ".join(attempts) + "]" if attempts else ""
        print(f"- r/{subreddit}: source={source} usable={len(posts)}{attempt_note}")

    selected = select_posts(all_posts, limit=args.limit, max_per_subreddit=args.max_per_subreddit)

    print()
    print("## Select candidates")
    if len(selected) < args.limit:
        print(f"WARN: selected only {len(selected)} usable posts; target is {args.limit}.")
    if warnings:
        print("Warnings:")
        for warning in warnings:
            print(f"- {warning}")
    print()

    if args.json:
        print(json.dumps([asdict(post) for post in selected], ensure_ascii=False, indent=2))
    else:
        for idx, post in enumerate(selected, 1):
            print(f"### Candidate {idx:02d}")
            print(f"source: r/{post.subreddit}")
            print(f"post_id: {post.id}")
            print(f"author: {post.author}")
            print(f"url: {post.url}")
            print(f"updated: {post.updated}")
            print(f"media: {post.media_type}" + (f" {post.media_url}" if post.media_url else ""))
            print(f"title: {preview(post.title)}")
            if post.summary:
                print(f"summary_preview: {preview(post.summary)}")
            print(
                "meme placeholder: "
                f"meme BR leve inspirado no tema '{preview(post.title, 80)}'; "
                "punchline curta, sem copiar texto integral e sem atacar pessoa privada."
            )
            print()

    print("## Dry-run result")
    print(f"subreddits_checked={len(subreddits)} candidates_found={len(all_posts)} selected={len(selected)}")
    print("No images generated. No Telegram sent. No cron created.")
    if args.write_cache:
        print(f"Cache writes enabled: {args.cache_dir}")
    else:
        print("No cache written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
