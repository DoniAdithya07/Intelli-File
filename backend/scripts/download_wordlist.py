"""One-time setup step (dev machine only, needs internet): fetches a
frequency-ranked English word list into backend/data/, for photo-search
query checking (app/search/dictionary.py). Like the models, this ships
inside the installer — the app itself never downloads it.

Source: hermitdave/FrequencyWords (OpenSubtitles-derived, CC BY-SA 4.0),
the 2018 English "full" list. Kept: the most frequent alphabetic words.
Frequencies are kept too, so a typo is corrected to the *common* word
("dgo" -> "dog"), not an obscure one.

Run with:
    backend/venv/bin/python backend/scripts/download_wordlist.py
"""

import sys
import urllib.request
from pathlib import Path

SOURCE_URL = "https://raw.githubusercontent.com/hermitdave/FrequencyWords/master/content/2018/en/en_full.txt"
TARGET = Path(__file__).resolve().parents[1] / "data" / "english_words.txt"
KEEP = 100_000


def main() -> int:
    print(f"Downloading {SOURCE_URL} ...")
    with urllib.request.urlopen(SOURCE_URL, timeout=60) as resp:
        raw = resp.read().decode("utf-8")

    kept: list[tuple[str, int]] = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        word, count = parts[0], int(parts[1])
        if len(word) >= 2 and word.isalpha() and word.isascii() and word.islower():
            kept.append((word, count))
        if len(kept) >= KEEP:
            break

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with TARGET.open("w", encoding="utf-8") as f:
        for word, count in kept:
            f.write(f"{word} {count}\n")

    print(f"Wrote {len(kept):,} words to {TARGET} ({TARGET.stat().st_size / 1_000_000:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
