#!/usr/bin/env python3
"""Transcribe a recorded client call into a diarized vault transcript.

Engine for the `client-call-ingest` skill.

Primary provider: ElevenLabs Scribe v1 (best measured accuracy on accented
English + speaker diarization). Fallback: AssemblyAI Universal-2.

Usage:
    python3 transcribe_call.py <audio-file> --client <slug> [--topic <slug>]
                               [--date YYYY-MM-DD] [--provider elevenlabs|assemblyai]

Writes:
    second-brain/04_projects/clients/_active/<slug>/communications/transcripts/
        transcript-<date>-<topic>.md      (diarized, speaker-labelled)
        transcript-<date>-<topic>.json    (raw provider payload)

API keys are read from the environment, or from
    second-brain-tier3/personal/business-keelworks.md
as `ELEVENLABS_API_KEY` / `ASSEMBLYAI_API_KEY` lines. Never hardcode keys.
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import date as _date
from pathlib import Path

import requests

SCRIPTS = Path(__file__).resolve().parent
WORKSPACE = SCRIPTS.parents[2]
TIER3_KEYS = WORKSPACE / "second-brain-tier3/personal/business-keelworks.md"

ELEVEN_URL = "https://api.elevenlabs.io/v1/speech-to-text"
AAI_BASE = "https://api.assemblyai.com/v2"


def load_key(name: str) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    if TIER3_KEYS.exists():
        for line in TIER3_KEYS.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.search(rf"{name}\s*[:=]\s*[`\"']?([A-Za-z0-9_\-\.]+)", line)
            if m:
                return m.group(1)
    return None


def transcribe_elevenlabs(audio: Path, key: str) -> dict:
    print(f"  provider: ElevenLabs Scribe v1 ({audio.stat().st_size / 1e6:.1f} MB)")
    with audio.open("rb") as fh:
        r = requests.post(
            ELEVEN_URL,
            headers={"xi-api-key": key},
            files={"file": (audio.name, fh, "application/octet-stream")},
            data={
                "model_id": "scribe_v1",
                "diarize": "true",
                "tag_audio_events": "true",
                "timestamps_granularity": "word",
            },
            timeout=1800,
        )
    r.raise_for_status()
    return r.json()


def transcribe_assemblyai(audio: Path, key: str) -> dict:
    print(f"  provider: AssemblyAI Universal-2 ({audio.stat().st_size / 1e6:.1f} MB)")
    h = {"authorization": key}
    up = requests.post(f"{AAI_BASE}/upload", headers=h, data=audio.read_bytes(), timeout=1800)
    up.raise_for_status()
    job = requests.post(
        f"{AAI_BASE}/transcript",
        headers=h,
        json={
            "audio_url": up.json()["upload_url"],
            "speaker_labels": True,
            "speech_model": "universal",
            "language_detection": True,
        },
        timeout=120,
    )
    job.raise_for_status()
    tid = job.json()["id"]
    while True:
        got = requests.get(f"{AAI_BASE}/transcript/{tid}", headers=h, timeout=60).json()
        if got["status"] == "completed":
            return got
        if got["status"] == "error":
            raise RuntimeError(got.get("error"))
        print("    ...processing")
        time.sleep(5)


def segments_from_elevenlabs(payload: dict) -> list[tuple[str, str]]:
    words = payload.get("words") or []
    if not words:
        return [("SPEAKER", payload.get("text", ""))]
    segs, cur_spk, buf = [], None, []
    for w in words:
        spk = w.get("speaker_id") or "SPEAKER"
        txt = w.get("text", "")
        if spk != cur_spk and buf:
            segs.append((cur_spk, "".join(buf).strip()))
            buf = []
        cur_spk = spk
        buf.append(txt if txt.startswith(" ") or not buf else " " + txt)
    if buf:
        segs.append((cur_spk, "".join(buf).strip()))
    return [(s, t) for s, t in segs if t]


def segments_from_assemblyai(payload: dict) -> list[tuple[str, str]]:
    utts = payload.get("utterances") or []
    if utts:
        return [(f"SPEAKER {u['speaker']}", u["text"]) for u in utts]
    return [("SPEAKER", payload.get("text", ""))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--client", required=True)
    ap.add_argument("--topic", default="call")
    ap.add_argument("--date", default=_date.today().isoformat())
    ap.add_argument("--provider", choices=["elevenlabs", "assemblyai"], default="elevenlabs")
    a = ap.parse_args()

    audio = Path(a.audio).expanduser().resolve()
    if not audio.exists():
        sys.exit(f"audio not found: {audio}")

    out_dir = (
        WORKSPACE
        / f"second-brain/04_projects/clients/_active/{a.client}/communications/transcripts"
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"transcript-{a.date}-{a.topic}"

    provider = a.provider
    key = load_key("ELEVENLABS_API_KEY" if provider == "elevenlabs" else "ASSEMBLYAI_API_KEY")
    if not key and provider == "elevenlabs":
        key = load_key("ASSEMBLYAI_API_KEY")
        if key:
            print("  ELEVENLABS_API_KEY missing — falling back to AssemblyAI")
            provider = "assemblyai"
    if not key:
        sys.exit("No API key found. Set ELEVENLABS_API_KEY or ASSEMBLYAI_API_KEY.")

    print(f"Transcribing {audio.name} for {a.client}...")
    if provider == "elevenlabs":
        payload = transcribe_elevenlabs(audio, key)
        segs = segments_from_elevenlabs(payload)
        model = "ElevenLabs Scribe v1"
    else:
        payload = transcribe_assemblyai(audio, key)
        segs = segments_from_assemblyai(payload)
        model = "AssemblyAI Universal-2"

    (out_dir / f"{stem}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    speakers = sorted({s for s, _ in segs})
    body = [
        "---",
        "type: call-transcript",
        f"client: {a.client}",
        f"created: {a.date}",
        "status: raw",
        f"source-audio: {audio.name}",
        f"transcription-model: {model}",
        f"speakers-detected: {len(speakers)}",
        f"tags: [transcript, call, {a.client}]",
        "---",
        "",
        f"# Call transcript — {a.client}, {a.date} ({a.topic})",
        "",
        "> Machine transcription. Speaker labels are provider-assigned and may need",
        "> a manual pass to map to real names. Verify any figure before quoting it.",
        "",
        "## Speaker key",
        "",
    ]
    body += [f"- `{s}` — _(unmapped)_" for s in speakers]
    body += ["", "---", "", "## Transcript", ""]
    for spk, txt in segs:
        body += [f"**{spk}:** {txt}", ""]

    md = out_dir / f"{stem}.md"
    md.write_text("\n".join(body), encoding="utf-8")
    words = sum(len(t.split()) for _, t in segs)
    print(f"  {len(segs)} segments, {len(speakers)} speakers, ~{words} words")
    print(f"  -> {md}")
    print(f"  -> {out_dir / f'{stem}.json'}")


if __name__ == "__main__":
    main()
