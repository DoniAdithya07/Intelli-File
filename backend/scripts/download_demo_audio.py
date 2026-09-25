"""Dev/demo helper: puts a set of spoken voice notes into the demo folder
so audio search (Phase 7: files are transcribed, then searchable by what
was *said* in them) can be tried against a real collection.

    backend/venv/bin/python backend/scripts/download_demo_audio.py [target_folder]

Default target: ~/Desktop/IntelliFile-Search-Demo. Re-running skips files
that already exist.

The notes are synthesised with macOS's built-in `say` (several voices,
including Indian-English ones), exported as AAC `.m4a` — the same format
a phone's voice-memo app produces — and given deliberately *uninformative*
names (voice_note_01.m4a …) so that finding one proves the search is
reading the speech, not the file name. The table below is the answer key.
macOS only (needs `say`/`afconvert`); prints instructions elsewhere.

Real human recordings would be better still — see the note at the end of
`main()` for why they are not fetched automatically.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_TARGET = Path.home() / "Desktop" / "IntelliFile-Search-Demo"

# (voice, what is said). Topics are deliberately far apart so each note has
# its own vocabulary, and a few share a theme (two are about travel, two
# about money) so ranking between related notes can be judged too.
VOICE_NOTES = [
    ("Samantha", "Dentist appointment is on Thursday at four thirty. Remember to bring the insurance card and the old x-rays."),
    ("Daniel", "Ideas for the birthday party: rent the bouncy castle, order a chocolate cake for twenty people, and ask Priya to bring her speaker."),
    ("Rishi", "Note to self: the train to Hyderabad leaves at six fifteen in the morning from platform two. Book a cab for five o'clock."),
    ("Karen", "Recipe reminder. For the lemon rice you need two cups of cooked rice, one lemon, mustard seeds, curry leaves, turmeric and peanuts."),
    ("Aman", "Meeting summary: the client wants the dashboard redesigned by the end of the month, and they will send the new logo files tomorrow."),
    ("Tessa", "Packing list for the trek: rain jacket, headlamp, two litres of water, sunscreen, and the first aid kit from the garage."),
    ("Moira", "Budget check. Rent is twelve thousand, electricity was about nine hundred, and I still owe Rahul six hundred for the concert tickets."),
    ("Tara", "Study plan for the exam: revise operating systems on Monday, computer networks on Tuesday, and do the past papers over the weekend."),
    ("Samantha", "Car service is due. The garage said the brake pads are worn and the oil change is overdue by about two thousand kilometres."),
    ("Rishi", "Gift ideas for mom: a saree from the shop near the temple, or the book on Indian classical music she mentioned last week."),
]


def synthesise(voice: str, text: str, out_m4a: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        aiff = Path(tmp) / "note.aiff"
        subprocess.run(["say", "-v", voice, "-o", str(aiff), text], check=True, capture_output=True)
        # AAC in an .m4a container, 22.05 kHz mono — what a phone voice memo looks like.
        subprocess.run(
            ["afconvert", "-f", "m4af", "-d", "aac", "-b", "64000", "--src-complexity", "bats", "-r", "127",
             str(aiff), str(out_m4a)],
            check=True, capture_output=True,
        )


def main() -> int:
    if shutil.which("say") is None or shutil.which("afconvert") is None:
        print("This helper synthesises speech with macOS `say`/`afconvert`, which aren't available here.")
        print("On another OS, drop a few .m4a/.mp3/.wav voice notes into the demo folder by hand.")
        return 1
    target = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else DEFAULT_TARGET
    target.mkdir(parents=True, exist_ok=True)
    print(f"Creating demo voice notes in {target}\n")

    made = skipped = 0
    print(f"{'file':20s} {'voice':10s} what it says")
    for n, (voice, text) in enumerate(VOICE_NOTES, start=1):
        out = target / f"voice_note_{n:02d}.m4a"
        if out.exists():
            skipped += 1
        else:
            synthesise(voice, text, out)
            made += 1
        print(f"{out.name:20s} {voice:10s} {text}")

    print(f"\nDone: {made} created, {skipped} already present.")
    print("Next: in the app, re-pick the folder (or wait for the watcher, ~30 s), then search for things that were SAID,")
    print("  e.g. 'dentist appointment', 'bouncy castle', 'train to Hyderabad', 'lemon rice', 'brake pads', 'gift for mom'.")
    print("\nNote: these are synthetic voices. They exercise the whole pipeline (decode -> Whisper -> index -> search)")
    print("but not accent/noise robustness — for that, record a few real notes on your phone and drop them in the folder.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
