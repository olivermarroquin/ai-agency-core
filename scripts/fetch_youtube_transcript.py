#!/usr/bin/env python3
"""Fetch YouTube transcripts via the deployed Cloudflare Worker.

Usage:
    python fetch_youtube_transcript.py --url "https://www.youtube.com/watch?v=VIDEO_ID"
    python fetch_youtube_transcript.py --batch urls.txt
    python fetch_youtube_transcript.py --url "..." --markdown
    python fetch_youtube_transcript.py --url "..." --json --quiet

The token is resolved from:
  1. $YOUTUBE_TRANSCRIPT_TOKEN (env var, if set)
  2. ~/workspace/second-brain-tier3/automation/secrets/youtube-transcript.key (Mac Terminal)
  3. /mnt/user/second-brain-tier3/automation/secrets/youtube-transcript.key (Cowork sandbox)

The Worker URL is resolved from:
  1. $YOUTUBE_TRANSCRIPT_URL (env var, if set)
  2. Hard-coded default (deployed Worker URL)
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

WORKER_URL = "https://yt-transcript.oliver-marroquin31217.workers.dev/"

# Resolve from either execution context:
#   - Mac Terminal:    ~/workspace/second-brain-tier3/automation/secrets/youtube-transcript.key
#   - Cowork sandbox:  ~/mnt/workspace/second-brain-tier3/automation/secrets/youtube-transcript.key
# Sandbox mounts the operator's ~/workspace at ~/mnt/workspace.
# Mirrors perplexity_sonar.py path-resolution pattern.
_TIER3_TAIL = Path("second-brain-tier3") / "automation" / "secrets" / "youtube-transcript.key"
TOKEN_PATHS = [
    Path.home() / "workspace" / _TIER3_TAIL,
    Path.home() / "mnt" / "workspace" / _TIER3_TAIL,
]

# Retry configuration (Tasks A)
_RETRY_ATTEMPTS = 3          # max attempts per URL
_RETRY_BASE_SECS = 2         # base backoff seconds; doubles per retry + jitter
# Retry only on transient failures. 4xx errors from the Worker are definitive
# classifications (disabled/private/not-found) that won't change on retry.
_TRANSIENT_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})

# Batch pacing — sleep between requests to avoid self-inflicted upstream rate limiting
_BATCH_PACE_SECS = 2.0
_BATCH_PACE_JITTER = 2.0


def resolve_token() -> str:
    env_val = os.environ.get("YOUTUBE_TRANSCRIPT_TOKEN")
    if env_val:
        return env_val.strip()
    for p in TOKEN_PATHS:
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    print(
        "ERROR: YouTube transcript token not found.\n"
        "  - $YOUTUBE_TRANSCRIPT_TOKEN is not set.\n"
        f"  - Checked paths: {', '.join(str(p) for p in TOKEN_PATHS)}\n"
        "Either export YOUTUBE_TRANSCRIPT_TOKEN or create the key file.",
        file=sys.stderr,
    )
    sys.exit(1)


def resolve_worker_url() -> str:
    return os.environ.get("YOUTUBE_TRANSCRIPT_URL", WORKER_URL).rstrip("/")


def _sleep_backoff(attempt: int) -> None:
    """Exponential backoff with jitter before a retry."""
    delay = _RETRY_BASE_SECS * (2 ** attempt) + random.uniform(0, 1)
    time.sleep(delay)


def fetch_transcript(video_url: str, token: str, worker_url: str) -> dict:
    from urllib.parse import quote
    req_url = f"{worker_url}/?url={quote(video_url, safe='')}"
    req = Request(req_url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": "fetch-youtube-transcript/1.0",
    })
    last_result: dict = {"error": "unknown", "message": "no attempts made", "video_url": video_url}
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            with urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                result = json.loads(body)
            except json.JSONDecodeError:
                result = {"error": "http_error", "status": e.code, "message": body, "video_url": video_url}
            if e.code in _TRANSIENT_HTTP_STATUSES and attempt < _RETRY_ATTEMPTS - 1:
                last_result = result
                print(
                    f"  → attempt {attempt + 1}/{_RETRY_ATTEMPTS} failed (HTTP {e.code}); retrying…",
                    file=sys.stderr,
                )
                _sleep_backoff(attempt)
                continue
            return result
        except URLError as e:
            last_result = {"error": "connection_error", "message": str(e.reason), "video_url": video_url}
            if attempt < _RETRY_ATTEMPTS - 1:
                print(
                    f"  → attempt {attempt + 1}/{_RETRY_ATTEMPTS} failed (connection error); retrying…",
                    file=sys.stderr,
                )
                _sleep_backoff(attempt)
                continue
    return last_result


def transcript_to_markdown(data: dict) -> str:
    lines = []
    for seg in data.get("transcript", []):
        offset_sec = seg["offset"] / 1000
        mins, secs = divmod(int(offset_sec), 60)
        lines.append(f"[{mins:02d}:{secs:02d}] {seg['text']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Fetch YouTube transcripts via Cloudflare Worker")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="Single YouTube video URL")
    group.add_argument("--batch", help="File containing one YouTube URL per line")
    parser.add_argument("--json", action="store_true", dest="output_json", help="Output raw JSON (default)")
    parser.add_argument("--markdown", action="store_true", help="Output as timestamped markdown")
    parser.add_argument("--quiet", action="store_true", help="Suppress status messages")
    args = parser.parse_args()

    token = resolve_token()
    worker_url = resolve_worker_url()

    urls = []
    if args.url:
        urls.append(args.url)
    elif args.batch:
        batch_path = Path(args.batch)
        if not batch_path.exists():
            print(f"ERROR: Batch file not found: {args.batch}", file=sys.stderr)
            sys.exit(1)
        urls = [line.strip() for line in batch_path.read_text().splitlines() if line.strip() and not line.startswith("#")]

    if not urls:
        print("ERROR: No URLs to process.", file=sys.stderr)
        sys.exit(1)

    results = []
    for i, url in enumerate(urls):
        if not args.quiet:
            print(f"[{i+1}/{len(urls)}] Fetching: {url}", file=sys.stderr)
        data = fetch_transcript(url, token, worker_url)
        results.append(data)
        if not args.quiet and data.get("ok"):
            print(f"  → {data.get('segments', '?')} segments", file=sys.stderr)
        elif not args.quiet:
            print(f"  → ERROR: {data.get('error', 'unknown')}: {data.get('message', '')}", file=sys.stderr)
        # Batch pacing: sleep between requests (not after the last one)
        if len(urls) > 1 and i < len(urls) - 1:
            time.sleep(_BATCH_PACE_SECS + random.uniform(0, _BATCH_PACE_JITTER))

    if args.markdown:
        failures: list[tuple[str, str]] = []
        for data in results:
            if len(results) > 1:
                # In batch mode, always print the URL header so failures are identifiable
                print(f"\n## {data.get('video_url', 'unknown')}\n")
            if not data.get("ok"):
                # Typed failure (Task C): error to stderr, nothing to stdout for this URL.
                # --markdown NEVER writes the error string as file content.
                error_class = data.get("error", "unknown")
                error_detail = data.get("message", "")
                print(f"ERROR: {error_class} — {error_detail}", file=sys.stderr)
                failures.append((data.get("video_url", "unknown"), error_class))
                if len(results) == 1:
                    # Single mode: exit immediately; caller sees empty stdout + nonzero exit
                    sys.exit(1)
                # Batch mode: continue processing remaining URLs
                continue
            print(transcript_to_markdown(data))
        if failures:
            print(
                f"\nBatch result: {len(results) - len(failures)}/{len(results)} succeeded, "
                f"{len(failures)} failed.",
                file=sys.stderr,
            )
            for url, error_class in failures:
                print(f"  FAILED {error_class}: {url}", file=sys.stderr)
            sys.exit(len(failures))
    else:
        # JSON mode: output full results; exit nonzero if any URL failed
        failures_count = sum(1 for d in results if not d.get("ok"))
        output = results[0] if len(results) == 1 else results
        print(json.dumps(output, indent=2, ensure_ascii=False))
        if failures_count > 0:
            sys.exit(failures_count)


if __name__ == "__main__":
    main()
