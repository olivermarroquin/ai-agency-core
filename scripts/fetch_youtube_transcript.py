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
import sys
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


def fetch_transcript(video_url: str, token: str, worker_url: str) -> dict:
    from urllib.parse import quote
    req_url = f"{worker_url}/?url={quote(video_url, safe='')}"
    req = Request(req_url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": "fetch-youtube-transcript/1.0",
    })
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"error": "http_error", "status": e.code, "message": body, "video_url": video_url}
    except URLError as e:
        return {"error": "connection_error", "message": str(e.reason), "video_url": video_url}


def transcript_to_markdown(data: dict) -> str:
    if not data.get("ok"):
        return f"**ERROR:** {data.get('error', 'unknown')} — {data.get('message', '')}"
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

    if args.markdown:
        for data in results:
            if len(results) > 1:
                print(f"\n## {data.get('video_url', 'unknown')}\n")
            print(transcript_to_markdown(data))
    else:
        output = results[0] if len(results) == 1 else results
        print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
