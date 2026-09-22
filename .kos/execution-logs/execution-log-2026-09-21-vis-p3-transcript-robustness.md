---
type: execution-log
status: complete
created: 2026-09-21
updated: 2026-09-21
venture: ai-agency-core
session: vis-p3-transcript-robustness-202609211930
tier: Capture-only
tags: [execution-log, vis, transcript, robustness, load-test, cloudflare-worker]
---

# Execution log — VIS Phase 3: Transcript robustness at scale

**Date:** 2026-09-21
**Chat-id:** vis-p3-transcript-robustness-202609211930
**Tier:** Capture-only
**Handoff:** `vis-system-enhancement/phase-3-transcript-robustness-at-scale.md`
**Substrate:** Claude Code (host network — required for real load test)

---

## What was built

Four hardening tasks on the VIS transcript pull path:

| Task | File | Status |
|---|---|---|
| A — retry/backoff + batch pacing | `scripts/fetch_youtube_transcript.py` | ✅ Built + verified |
| B — Worker error taxonomy extension | `services/yt-transcript/src/index.js` | ✅ Built (deploy pending Gate #2) |
| C — typed failure instead of error-as-content | `scripts/fetch_youtube_transcript.py` | ✅ Built + verified |
| D — 50-URL load test (real network) | results below | ✅ Complete — real numbers |

**Scope exclusions (per handoff restructure caution):**
- `skills/vis-extraction/scripts/transcript-pull.sh` — article-path curl retry is a follow-up (see below)
- `skills/**` and `second-brain/**` — off-limits during restructure

---

## Task A — retry/backoff + batch pacing (fetch_youtube_transcript.py)

**What changed:**
- Added `import random, time`
- Added retry constants: `_RETRY_ATTEMPTS=3`, `_RETRY_BASE_SECS=2`, `_TRANSIENT_HTTP_STATUSES={429,500,502,503,504}`
- Added batch pacing constants: `_BATCH_PACE_SECS=2.0`, `_BATCH_PACE_JITTER=2.0`
- `fetch_transcript()` now wraps the `urlopen` call in a 3-attempt retry loop:
  - Retries on: `URLError` (connection), HTTP 429, HTTP 5xx
  - Does NOT retry on: HTTP 400/401/403/404/422 (definitive error classes from the Worker)
  - Backoff: `2 * (2**attempt) + random.uniform(0, 1)` → ~2s / ~4s / ~8s per retry
  - Logs each retry attempt to stderr
- `main()` batch loop adds `time.sleep(2 + random.uniform(0, 2))` between requests (~2–4s)

**Verification (forced connection error):**
```
YOUTUBE_TRANSCRIPT_URL=https://nonexistent.example.invalid python3 ... --url "..."
→ attempt 1/3 failed (connection error); retrying…
→ attempt 2/3 failed (connection error); retrying…
→ ERROR: connection_error (after 3 attempts exhausted)
```
Retry loop fires, logs each attempt, exhausts correctly.

---

## Task B — Worker error taxonomy extension (services/yt-transcript/src/index.js)

**Library error strings disk-verified from `node_modules/youtube-transcript/dist/esm/index.js`:**

| Class | Actual message | Old Worker mapping | New mapping |
|---|---|---|---|
| `YoutubeTranscriptDisabledError` | "Transcript is disabled on this video" | `transcripts_disabled` ✓ | unchanged |
| `YoutubeTranscriptNotAvailableError` | "No transcripts are available for this video" | ❌ `fetch_error` (no match) | `transcripts_not_available` (new, 422) |
| `YoutubeTranscriptVideoUnavailableError` | "The video is no longer available" | ❌ `fetch_error` ("Video unavailable" check never matched) | fixed → `video_not_found` (added "no longer available") |
| `YoutubeTranscriptTooManyRequestError` | "YouTube is receiving too many requests…captcha" | ❌ `fetch_error` | `rate_limited` (new, 429) |
| (no library class) | age-restricted content | `fetch_error` | unchanged — undetectable via message sniffing; InnerTube Android client bypasses most age gates (confirmed: Gangnam Style succeeded in load test) |
| (legacy) | message includes "private" | `video_private` (dead code) | kept as forward-compat check |

**Notable finding:** `video_not_found` and `video_private` in the original Worker were effectively dead code — `YoutubeTranscriptVideoUnavailableError` actually throws "no longer available" (not "Video unavailable"), and no library error contains "private". Both bugs silently collapsed to `fetch_error`. Fixed.

**Worker deploy:** pending Gate #2 (operator runs `wrangler deploy` from `services/yt-transcript/`). Code change + local verification complete and counted as Task B delivered.

> **DEPLOYED 2026-09-22 (operator, Gate #2 complete):** `wrangler deploy` succeeded — Uploaded yt-transcript (11.08 KiB), deployed to `https://yt-transcript.oliver-marroquin31217.workers.dev`. Wrangler re-auth via OAuth was required (prior token expired since the June deploy). The extended error taxonomy (`transcripts_not_available`, `rate_limited`, fixed `video_not_found`) is now LIVE. Phase 3 fully closed — no open items.

---

## Task C — typed failure (fetch_youtube_transcript.py)

**What changed:**
- `transcript_to_markdown()` no longer returns the `**ERROR:** ...` string on failure — it only processes `ok: true` payloads
- `main()` `--markdown` mode on `ok: false`:
  - Prints `ERROR: <class> — <message>` to **stderr**
  - Writes **nothing** to stdout for the failed URL
  - Single mode: `sys.exit(1)` immediately
  - Batch mode: continues all remaining URLs, collects failures, prints per-URL summary + aggregate count to stderr at end, `sys.exit(len(failures))`
- `main()` `--json` mode: `sys.exit(failures_count)` if any URL failed (was exit 0 before)

**Verification (typed failure on invalid URL in --markdown mode):**
```
$ python3 ... --url "https://youtube.com/watch?v=XXXXXXXXXXX" --markdown > /tmp/out.txt
Exit code: 1
Stdout length: 0 (file is empty)
Stderr: ERROR: transcripts_disabled — [YoutubeTranscript] 🚨 Transcript is disabled on this video
```
No `**ERROR:**` string reaches file content. ✓

**Downstream contract (vis-extraction SKILL.md Step 6 Scenario B):**
`--markdown --quiet > cache-file` now yields:
- Success → file has transcript content, exit 0
- Failure → file is empty, exit 1, error class on stderr
Strictly better than the prior accidental-catch by the <500-word check.

---

## Task D — 50-URL load test results

**Run:** 2026-09-21, host network (Mac Terminal), real Worker
**Wall-clock:** 3 minutes 14 seconds
**Pacing:** ~2–4s between requests (batch pacing enabled)

### Headline number

**Failure rate: 6% (3/50)** — but all 3 failures are planted edge cases.
**Organic failure rate (46 real vault URLs): 0% (0/46)**

### Per-class counts

| Outcome | Count | Notes |
|---|---|---|
| `ok: true` | 47 | All 46 vault URLs + EC-3 (age-restricted: bypassed) |
| `transcripts_disabled` | 3 | All 3 planted edge cases |
| `video_not_found` | 0 | Not triggered in this run |
| `transcripts_not_available` | 0 | Not triggered |
| `rate_limited` | 0 | No rate limiting observed at 2–4s pacing |
| `connection_error` | 0 | — |
| `fetch_error` | 0 | — |
| Retry events | 0 | No transient errors; retry path not exercised in production run |

### URL list (50)

**Vault URLs (46):**
- 03_domains/client-services: 1 URL (np6CwvTYTAM)
- 03_domains/seo: 17 URLs
- 03_domains/video-intelligence: 1 URL
- 03_domains/website-design: 4 URLs
- 00_inbox/sources-pending: 23 URLs
All 46: **OK**

**Planted edge cases (4):**

| EC | URL | Pre-test prediction | Actual result | Notes |
|---|---|---|---|---|
| EC-1 (captions-disabled) | `BaW_jenozKc` | transcripts_disabled | **transcripts_disabled** ✓ | Charlie Bit My Finger — famous NFT-sale removal; confirmed captions off |
| EC-2 (private/deleted) | `deleted00001` | video_not_found | **transcripts_disabled** | FINDING: invalid/nonexistent IDs map to transcripts_disabled, not video_not_found — InnerTube Android path silently returns nothing → web-page fallback finds no captions → DisabledError. video_not_found requires a page that literally lacks "playabilityStatus" |
| EC-3 (age-restricted) | `9bZkp7q19f0` (Gangnam Style) | fetch_error or transcripts_disabled | **OK, 67 segs** | FINDING: InnerTube Android client bypasses YouTube age gates. Age-restricted content is not a failure mode in practice for this Worker |
| EC-4 (members-only) | `8_b5L3NU9dM` | transcripts_disabled or fetch_error | **transcripts_disabled** ✓ | Members-only/private content maps to transcripts_disabled (same library path as disabled captions) |

### Retry path verification (separate test)

The 50-URL production run triggered 0 retries (no transient errors at this scale/pacing). Retry logic was verified separately by forcing connection errors via `YOUTUBE_TRANSCRIPT_URL=https://nonexistent.example.invalid`:
```
→ attempt 1/3 failed (connection error); retrying…
→ attempt 2/3 failed (connection error); retrying…
→ ERROR: connection_error (after exhausting all 3 attempts)
```
Retry loop fires correctly; does not infinite-loop.

### Key findings for Phase 2 autonomy policy

1. **0% organic failure rate** on a 46-URL curated-creator-content sample. At this pacing, a 50-URL sprint completes without operator intervention.
2. **Retry path not exercised in production** — 2–4s pacing appears sufficient to avoid rate limiting for this Worker and URL set. Retry logic exists and is verified but may not fire in typical use.
3. **Age-restricted content is not a failure mode** — InnerTube Android client bypasses age gates. EC-3 (Gangnam Style, age-restricted) returned 67 transcript segments.
4. **`video_not_found` is rarely triggered** — the library maps most "missing" content to `transcripts_disabled` via the InnerTube→web-page fallback path. The new Worker mapping is correct code but may not fire in practice unless the YouTube page itself lacks `"playabilityStatus":`.
5. **All failures are classifiable** — no ambiguous `fetch_error` in this run. The typed-failure fix means VIS can branch on error class instead of inspecting file content.

---

## Follow-ups (not built — restructure caution)

### 1. transcript-pull.sh article-path curl retry

**Location of file:** `skills/vis-extraction/scripts/transcript-pull.sh` (off-limits during restructure)
**Why not built:** The handoff's "Files to edit" listed `repos/ai-agency-core/scripts/transcript-pull.sh` — this path does not exist. The only copy is in `skills/`. The restructure caution bars `skills/` edits absolutely.
**What the fix would be:** In `handle_article()` (line 245), wrap the `curl` call in a retry loop (3 attempts, exponential backoff matching the Python wrapper), or use curl's built-in `--retry 3 --retry-delay 2 --retry-connrefused`. This folds into the same post-restructure batch as the SKILL.md Scenario B edit below.
**Tracking surface:** This execution log. Operator confirmed Option A (write-up only) at Gate #1.

### 2. vis-extraction SKILL.md Step 6 Scenario B typed-failure branch

**What this would do:** In `vis-extraction/SKILL.md` Step 6 Scenario B, replace the generic near-empty-file branch with a typed-failure branch that reads the error class from stderr and routes differently per class:
- `transcripts_disabled` / `transcripts_not_available`: video has no captions → pivot to Scenario C (host pull) or skip
- `video_not_found` / `video_private`: video is gone/private → skip entirely, note in extraction log
- `rate_limited`: back off and retry the sprint step
- `connection_error`: transient → retry
- Any: never check file content for `**ERROR:**` — that string can no longer appear in file content (Task C)

**Why not built:** off-limits during restructure (skills/ bar). Folds into Phase 1's SKILL.md touch post-restructure.
**Tracking surface:** This execution log.

---

## Decision made

**Non-obvious architectural choice:** `transcript_to_markdown()` was changed to no longer handle the error case (it previously returned the `**ERROR:**` string). Error handling is now entirely in `main()`. This keeps the contract clean: `transcript_to_markdown()` is only called when `ok: true`. If called on a failed result, it would silently return an empty string — callers must check `data.get("ok")` first, which `main()` now does explicitly.

**Why this approach:** Alternatives considered: (a) raise an exception in `transcript_to_markdown` on failure — would change the API surface; (b) return `None` and let callers handle — adds None-check burden; (c) current choice: caller checks `ok` before calling — simplest, matches the existing pattern in the code.

---

## Knowledge capture

**Pattern candidate:** retry/backoff-with-jitter pattern for Python `urlopen` in this codebase — first clean instance here. PATTERN CANDIDATE: `pattern-python-urlopen-retry-backoff` — promote when seen in a second script.

**Lesson candidate:** "Library error strings drift from documentation — verify from node_modules source before writing error mappers." The Worker's original `video_not_found` and `video_private` checks were dead code because the library's actual error messages differed from what was assumed. Disk-verification (reading `dist/esm/index.js`) was the only way to know. Promote to `second-brain/05_shared-intelligence/lessons/` if this class of drift is seen again.
