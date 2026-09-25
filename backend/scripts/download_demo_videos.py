"""Dev/demo helper (needs internet — the app itself never does): pulls a
handful of short, freely licensed video clips into the demo folder so
Phase 8b video search can be tried on real footage rather than the
generated red-circle/blue-square clip.

    backend/venv/bin/python backend/scripts/download_demo_videos.py [target_folder]

Default target: ~/Desktop/IntelliFile-Search-Demo. Files are named by
subject (demo_video_dog_1.webm ...) so a search hit can be checked against
what the clip actually shows. Re-running skips files that already exist.
Source: Wikimedia Commons search API. Commons stores most video as WebM
(VP8/VP9), which PyAV decodes with its bundled codecs. As with the photo
helper, Commons tags are imperfect — check a clip by eye before trusting
a search miss.
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "IntelliFile-demo/1.0 (student project; local file search demo)"
TIMEOUT_SECONDS = 120
PAUSE_SECONDS = 0.6
MAX_RETRIES = 4
MAX_BYTES = 25_000_000
MIN_SECONDS, MAX_SECONDS = 5, 120

SUBJECTS = {
    "dog running": 1,
    "cat": 1,
    "steam train": 1,
    "ocean waves beach": 1,
    "city street traffic": 1,
    "fireworks": 1,
    "cooking food kitchen": 1,
    "birds flying": 1,
    "waterfall": 1,
    "airplane landing": 1,
}


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                data = response.read()
            time.sleep(PAUSE_SECONDS)
            return data
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == MAX_RETRIES - 1:
                raise
            wait = 5 * (attempt + 1)
            print(f"    rate-limited, waiting {wait}s ...")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def commons_video_urls(subject: str, wanted: int) -> list[tuple[str, str]]:
    """(direct URL, extension) of up to `wanted` short WebM/MP4 clips."""
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{subject} filetype:video", "gsrnamespace": "6", "gsrlimit": str(wanted * 8),
        "prop": "videoinfo", "viprop": "url|mime|size|duration",
    }
    data = json.loads(fetch(COMMONS_API + "?" + urllib.parse.urlencode(params)))
    found = []
    for page in sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0)):
        info = (page.get("videoinfo") or [{}])[0]
        mime, size, duration = info.get("mime", ""), info.get("size", 0), info.get("duration") or 0
        ext = {"video/webm": "webm", "video/mp4": "mp4"}.get(mime)
        if ext and 0 < size <= MAX_BYTES and MIN_SECONDS <= duration <= MAX_SECONDS and info.get("url"):
            found.append((info["url"], ext))
        if len(found) >= wanted:
            break
    return found


def main() -> int:
    target = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.home() / "Desktop" / "IntelliFile-Search-Demo"
    target.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    for subject, wanted in SUBJECTS.items():
        slug = subject.replace(" ", "_")
        print(f"{subject}:")
        try:
            clips = commons_video_urls(subject, wanted)
        except Exception as e:
            print(f"    search failed: {e}")
            continue
        if not clips:
            print("    no suitable clip found")
        for i, (url, ext) in enumerate(clips, start=1):
            out = target / f"demo_video_{slug}_{i}.{ext}"
            if out.exists():
                print(f"    {out.name} exists, skipping")
                continue
            try:
                out.write_bytes(fetch(url))
                print(f"    {out.name} ({out.stat().st_size / 1_000_000:.1f} MB)")
                downloaded += 1
            except Exception as e:
                print(f"    failed {url}: {e}")
    print(f"\n{downloaded} clip(s) downloaded to {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
