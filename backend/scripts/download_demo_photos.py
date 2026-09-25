"""Dev/demo helper (needs internet — the app itself never does): pulls a
handful of real photographs into the demo folder so visual search can be
tried with recognisable scenes rather than synthetic shapes.

    backend/venv/bin/python backend/scripts/download_demo_photos.py [target_folder]

Default target: ~/Desktop/IntelliFile-Search-Demo. Files are named by
subject (demo_dog_1.jpg ...) so you can check what CLIP finds against
what the picture actually is. Re-running skips files that already exist.
Source: Wikimedia Commons search API (free, no API key, freely licensed
photographs). An earlier version used loremflickr.com, whose keyword
lookup silently returned the same unrelated photo for most subjects — so
verify by eye that a file's picture matches its name before trusting a
search miss. Commons tags are imperfect too: in one run three "sailing
boat" results were black-and-white coastline photos with no boat — a
handful of wrongly named files is realistic for a user's folder anyway.
"""

import io
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

DEFAULT_TARGET = Path.home() / "Desktop" / "IntelliFile-Search-Demo"

# subject -> how many photos of it (~110 total). Varied on purpose —
# animals, places, food, objects, activities — so photo search has
# distinct things to tell apart, and enough of each that ranking
# *within* a subject matters too ("a cat in the snow" vs "a cat indoors").
SUBJECTS = {
    # animals
    "dog": 6, "cat": 6, "horse": 4, "elephant": 3, "bird": 4, "fish underwater": 3, "butterfly": 3,
    # places / nature
    "beach": 5, "mountain": 5, "forest": 4, "waterfall": 3, "desert": 3, "city skyline at night": 4,
    "old castle": 3, "lighthouse": 3, "bridge": 3, "sunset": 4,
    # food
    "pizza": 3, "birthday cake": 3, "sushi": 3, "coffee cup": 3, "fruit basket": 3,
    # objects / vehicles
    "red car": 3, "bicycle": 3, "motorcycle": 3, "airplane": 3, "train": 3, "sailing boat": 3,
    "laptop on a desk": 2, "guitar": 3, "bookshelf": 2,
    # people / activities
    "football stadium": 2, "people playing basketball": 3, "wedding": 3, "children playing": 3,
    "street market": 3, "flowers": 4,
}

THUMB_WIDTH = 1024
TIMEOUT_SECONDS = 30
COMMONS_API = "https://commons.wikimedia.org/w/api.php"


# Commons rate-limits bursts (HTTP 429). A short pause between requests
# plus backoff on 429 keeps a 100+ photo run polite and complete.
PAUSE_SECONDS = 0.6
MAX_RETRIES = 4


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "IntelliFile-demo/1.0 (student project; local file search demo)"})
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


def commons_photo_urls(subject: str, wanted: int) -> list[str]:
    """Direct URLs of up to `wanted` JPEG photographs matching `subject`,
    as 1024px-wide renditions. Searches in the File namespace for
    bitmaps; the query is padded with 'photograph' to steer away from
    maps, diagrams and logos."""
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"{subject} photograph filetype:bitmap", "gsrnamespace": "6", "gsrlimit": str(wanted * 4),
        "prop": "imageinfo", "iiprop": "url|mime|size", "iiurlwidth": str(THUMB_WIDTH),
    }
    data = json.loads(fetch(COMMONS_API + "?" + urllib.parse.urlencode(params)))
    urls = []
    for page in sorted(data.get("query", {}).get("pages", {}).values(), key=lambda p: p.get("index", 0)):
        info = (page.get("imageinfo") or [{}])[0]
        if info.get("mime") == "image/jpeg" and info.get("width", 0) >= 800 and info.get("thumburl"):
            urls.append(info["thumburl"])
        if len(urls) >= wanted:
            break
    return urls


def main() -> int:
    target = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else DEFAULT_TARGET
    target.mkdir(parents=True, exist_ok=True)
    print(f"Downloading demo photos into {target}\n")

    saved, skipped, failed = 0, 0, 0
    for subject, count in SUBJECTS.items():
        slug = subject.replace(" ", "_")
        missing = [n for n in range(1, count + 1) if not (target / f"demo_{slug}_{n}.jpg").exists()]
        skipped += count - len(missing)
        if not missing:
            continue
        try:
            urls = commons_photo_urls(subject, wanted=count)
        except Exception as e:
            print(f"  FAILED to search Commons for {subject!r}: {e}")
            failed += len(missing)
            continue
        for n in missing:
            out = target / f"demo_{slug}_{n}.jpg"
            try:
                data = fetch(urls[n - 1])
                with Image.open(io.BytesIO(data)) as image:
                    image = image.convert("RGB")
                    image.save(out, "JPEG", quality=88)
                    print(f"  saved {out.name}  ({image.size[0]}x{image.size[1]})")
                saved += 1
            except Exception as e:
                print(f"  FAILED {out.name}: {e}")
                failed += 1

    print(f"\nDone: {saved} downloaded, {skipped} already present, {failed} failed.")
    print("Next: in the app, 'Browse for Folder' -> pick that folder (or re-pick it), then try")
    print("  Search photos: 'a dog', 'sunset on the beach', 'snowy mountains', 'a slice of pizza', 'city lights at night'")
    return 1 if failed and not saved else 0


if __name__ == "__main__":
    sys.exit(main())
