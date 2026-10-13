"""Ask: questions about the collection with a time or a place, and model non-answers.

Found in use on 2026-10-14: "how many images are downloaded in last 1 hour"
went to the language model, which answered "The source does not provide a
direct answer ... However, it mentions that the photo page has no side
preview", shown as a grounded answer with a warning about a number it did
not contain. Three fixes, checked here without the model and without real
files (a fixed clock and made-up dates):

  1. time windows, "downloaded / added / changed", places and newest /
     oldest are answered from the index (app/agent/timeframe.py, library.py);
  2. questions that only look like that still go to the normal Ask flow;
  3. an answer that opens by saying the sources hold no answer is "not
     found", and the file-count warning needs a figure in the answer.

Run with:  backend\\venv\\Scripts\\python.exe backend\\scripts\\check_ask_time.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.library import library_answer  # noqa: E402
from app.agent.loop import _FIGURE_RE, says_no_answer  # noqa: E402
from app.agent.missing import Known, index_answer  # noqa: E402

NOW = time.mktime((2026, 10, 14, 15, 30, 0, 0, 0, -1))  # a Wednesday afternoon
MIN, HOUR, DAY = 60, 3600, 86400
DL, DESK, WORK = "C:/Users/me/Downloads", "C:/Users/me/Desktop", "D:/work/photos"
# path -> (seconds ago it appeared, seconds ago it was changed)
FILES = {
    f"{DL}/a.png": (10 * MIN, 10 * MIN),
    f"{DL}/b.jpg": (40 * MIN, 40 * MIN),
    f"{DL}/c.png": (59 * MIN, 400 * DAY),          # downloaded an hour ago, made long before
    f"{DL}/old.png": (3 * HOUR, 3 * HOUR),
    f"{DL}/report.pdf": (20 * MIN, 20 * MIN),
    f"{DL}/quiz 1.pdf": (5 * HOUR, 5 * HOUR),      # this morning
    f"{DL}/setup.txt": (DAY + 5 * HOUR, DAY + 5 * HOUR),   # yesterday, 10:30
    f"{DL}/movie.mp4": (2 * DAY, 2 * DAY),         # Monday: this week
    f"{DESK}/note.txt": (2 * HOUR, 5 * MIN),       # changed five minutes ago
    f"{DESK}/plan.docx": (8 * DAY, 8 * DAY),       # Tuesday of last week
    f"{DESK}/pic.png": (9 * DAY, 9 * DAY),         # Monday of last week
    f"{WORK}/x.png": (30 * MIN, 30 * MIN),
    f"{WORK}/y.jpg": (35 * DAY, 90 * MIN),         # last month, changed 90 minutes ago
    f"{WORK}/gone.png": None,                      # deleted since it was indexed
}
PATHS = list(FILES)


def dates(path: str) -> tuple[float, float]:
    ages = FILES[path]
    if ages is None:
        raise FileNotFoundError(path)
    return NOW - ages[0], NOW - ages[1]


def ask(question: str, paths: list[str] = PATHS, indexing: bool = False) -> str | None:
    return library_answer(question, paths, indexing=indexing, clock=lambda: NOW, dates=dates)


MISSES: list[str] = []  # every mismatch is listed at the end: one run shows them all


def expect(question: str, *parts: str, paths: list[str] = PATHS, indexing: bool = False) -> str:
    answer = ask(question, paths, indexing)
    if answer is None:
        MISSES.append(f"{question!r} was not answered from the index")
        return ""
    for part in parts:
        if part not in answer:
            MISSES.append(f"{question!r}: expected {part!r} in {answer!r}")
    return answer


def check_time_and_place() -> int:
    asked = 0
    NOTE = "A file saved in the last minute may not be indexed yet."
    # The question that started this, and the same thing said other ways.
    first = expect("how many images are downloaded in last 1 hour", '3 photos in the "downloads" folder were added in the last hour (a.png, b.jpg, c.png)', "out of 4 photos there", NOTE)
    for question in ["how many images did i download in the last hour", "how many pictures have i downloaded in the past 60 minutes", "hw many images were downloaded in last 1 hr",
                     "can you tell me how many photos i downloaded in the last hour", "how many images are downloaded in the last one hour", "how many images in my downloads were added in the last hour"]:
        expect(question, "3 photos", "a.png, b.jpg, c.png")
        asked += 1
    # Rolling windows count back from now; calendar words are the day, week or month itself.
    cases = [
        ("how many files were downloaded in the last 30 minutes", '2 files in the "downloads" folder were added in the last 30 minutes (a.png, report.pdf)', "out of 8 files there"),
        ("how many files did i download today", '6 files in the "downloads" folder were added today', "out of 8 files there"),
        ("how many files did i download yesterday", '1 file in the "downloads" folder was added yesterday (setup.txt)'),
        ("how many files were downloaded this week", '8 files in the "downloads" folder were added this week'),
        ("how many files were added last week", "2 files were added last week (plan.docx, pic.png)", "out of 13 indexed files"),
        ("how many files were added in the last week", "10 files were added in the last week"),
        ("how many photos were added last month", "1 photo was added last month (y.jpg)", "out of 7 indexed photos"),
        ("how many photos were added this month", "6 photos were added this month"),
        ("how many files were added in the past 2 days", "10 files were added in the last 2 days"),
        ("how many pdfs did i download today", '2 PDFs in the "downloads" folder were added today (report.pdf, quiz 1.pdf)', "out of 2 PDFs there"),
        ("how many png files were added in the last hour", "3 PNG files were added in the last hour (a.png, x.png, c.png)", "out of 5 indexed PNG files"),
        ("how many photos and videos were added this week", "added this week", "photos and videos"),
        ("how many videos did i download in the last hour", 'No videos in the "downloads" folder were added in the last hour. The latest is movie.mp4, added 2 days ago.', NOTE),
        ("how many photos today", "5 photos were added today"),
        ("any new photos", "Yes: 5 photos were added in the last 7 days"),
        ("are there any new videos today", "No videos were added today. The latest is movie.mp4, added 2 days ago."),
        # "changed" is the modified time, not the time the file appeared.
        ("how many files were changed in the last hour", "5 files were changed in the last hour", "note.txt"),
        ("how many files were modified in the last 8 minutes", "1 file was changed in the last 8 minutes (note.txt)"),
        ("how many photos were edited in the last 2 hours", "4 photos were changed in the last 2 hours (a.png, x.png, b.jpg and 1 other)"),
        # A place with no time is the folder with that name.
        ("how many images have i downloaded", '4 photos in the "downloads" folder'),
        ("how many files are on the desktop", '3 files are in the "desktop" folder'),
        ("how many photos are on my desktop", '1 photo in the "desktop" folder (pic.png)'),
        ("how many files on the desktop were changed today", '1 file in the "desktop" folder was changed today (note.txt)'),
        # Lists.
        ("what did i download today", '6 files in the "downloads" folder were added today: a.png, report.pdf, b.jpg, c.png, old.png, quiz 1.pdf.'),
        ("which files did i download in the last hour", "4 files", "a.png, report.pdf, b.jpg, c.png"),
        ("list the photos added today", "5 photos were added today: a.png, x.png, b.jpg, c.png, old.png."),
        ("newest files on the desktop", 'The 3 newest files in the "desktop" folder: note.txt (added 2 hours ago), plan.docx (added 8 days ago), pic.png (added 9 days ago).'),
        ("what are the 2 newest photos", "The 2 newest photos: a.png (added 10 minutes ago), x.png (added 30 minutes ago)."),
        ("show the latest file in downloads", 'The newest file in the "downloads" folder: a.png (added 10 minutes ago).'),
        ("list the oldest 2 photos", "The 2 oldest photos: y.jpg (added 35 days ago), pic.png (added 9 days ago)."),
        # A word that is not a kind of file, with "downloaded": counted by name.
        ("how many quizzes did i download today", '1 file with "quiz" in its name in the "downloads" folder was added today (quiz 1.pdf)'),
    ]
    for question, *parts in cases:
        expect(question, *parts)
        asked += 1
    # The folder is not in the index: said so, with what to do, not "0".
    no_downloads = [p for p in PATHS if not p.startswith(DL)]
    answer = expect("how many images are downloaded in last 1 hour", 'No indexed folder is named "downloads"', "Add that folder on the Index page", paths=no_downloads)
    # Indexing still running is the more useful note than "the last minute".
    running = expect("how many images are downloaded in last 1 hour", "3 photos", "Indexing is still running, so newer files may be missing.", indexing=True)
    assert NOTE not in running
    # A file deleted since it was indexed is left out silently (gone.png is the 8th photo).
    expect("how many photos were added in the last 100 days", "7 photos were added in the last 100 days")
    # A place is the user's own Windows folder, not every folder with that name: on the real
    # index "desktop" by name took in a test folder called Desktop. Only when a place was said.
    other = PATHS + ["D:/tests/Desktop/fake one.png", "D:/tests/Desktop/fake two.png", "D:/backup/Downloads/old copy.png"]
    own = {"desktop": "C:\\Users\\me\\Desktop", "downloads": "C:\\Users\\me\\Downloads"}

    def at_home(question: str) -> str:
        return library_answer(question, other, clock=lambda: NOW, dates=lambda p: dates(p) if p in FILES else (NOW - 60, NOW - 60), places=own) or ""

    assert at_home("how many files are on the desktop").startswith('3 files are in the "desktop" folder'), at_home("how many files are on the desktop")
    assert at_home("how many images are downloaded in last 1 hour").startswith("3 photos"), at_home("how many images are downloaded in last 1 hour")
    assert "5 files" in at_home("how many files are in the desktop folder"), "said as a folder name, every folder called Desktop counts, as before"
    assert library_answer("how many files are on the desktop", other, clock=lambda: NOW, dates=dates, places={"desktop": "E:/nowhere/Desktop"}).startswith("5 files"), "own folder not indexed: by name"
    # The same answer through the door Ask uses.
    assert index_answer("how many images are downloaded in last 1 hour", Known(active=[])) is not None
    print(f"1. Time and place: the reported question answers {first!r}; {asked + 4} more wordings and windows (rolling and calendar, added and changed, places, lists, "
          f"newest and oldest, by name) are exact; a folder that is not indexed says so ({answer[:44]}...); a deleted file is left out: OK")
    return asked


def check_content_questions_stay() -> None:
    content = [
        "how many images did the report add last week", "how many photos did we take in lisbon last month", "what did i spend last month", "when is the march invoice due",
        "how many hours did i work this week", "how many days until the dentist appointment", "what happened in the meeting yesterday", "how many files did the backup copy yesterday",
        "how many meetings today", "how many calls did i get yesterday", "what changed in the lease this year", "what is new in the roadmap", "how much did i save this month",
        "who did i meet last week", "what did the doctor say yesterday", "how many euros did i spend today", "what was added to the contract last month",
        "how many steps did i walk today", "what is the latest version of the api design", "which invoice was paid last", "how many people came to the party last year",
        "what did i download the report for", "is the newest deployment checklist approved",
    ]
    for question in content:
        assert ask(question) is None, f"{question!r} is about what a file says, but the index answered: {ask(question)!r}"
    # Without a time or place nothing changed: the earlier collection answers are word for word the same.
    for question in ["how many files are there", "how many photos do i have", "how many files got indexed in the end", "how many png files are there", "list my videos"]:
        assert ask(question) == library_answer(question, PATHS), question
    print(f"2. {len(content)} questions with a time or a 'new' in them that are about what a file says still go to the normal Ask flow; questions without a time are answered as before: OK")


def check_no_answer_and_warning() -> None:
    reported = ('The source does not provide a direct answer to the question "how many images are downloaded in last 1 hour." However, it mentions that the photo page '
                "has no side preview with the picture's text yet, and screens do not have their own Help links.")
    none = [
        reported, "The sources do not mention how many images were downloaded.", "The provided documents do not contain information about the passport number.",
        "Based on the sources, the files do not specify a due date.", "I could not find any mention of a gym membership in the sources.", "There is no information about the airspeed of a swallow.",
        "Unfortunately, the context does not say when the lease ends. However, the deposit is listed.", "I cannot determine that from the sources.", "It is not specified in the sources.",
        "This cannot be determined from the given text.", "None of the sources provide the wifi password.",
    ]
    for answer in none:
        assert says_no_answer(answer), f"not seen as a non-answer: {answer!r}"
    answers = [
        "The March invoice is due on 14 April 2025 and the amount is 1,440 euros.", "The lease does not mention pets, but the deposit is 1,800 euros.", "There are 14 documentaries on the watchlist.",
        "The dough rests for 12 hours. The recipe does not say what flour to use.", "You fly to Lisbon on 12 May.", "The policy covers theft; it does not cover flooding.",
        "No, the report says the tests did not fail.", "The meeting notes say the budget cannot be changed before June.",
    ]
    for answer in answers:
        assert not says_no_answer(answer), f"a real answer was taken for a non-answer: {answer!r}"
    assert not _FIGURE_RE.search(reported.split("However")[1]), "the reported answer holds no figure, so it gets no warning about a number"
    for with_figure in ["The backup copied 143 files.", "Three files were copied.", "None of the files were copied."]:
        assert _FIGURE_RE.search(with_figure), with_figure
    print(f"3. {len(none)} ways a model says it has no answer are 'not found' (the reported one first), {len(answers)} real answers that contain 'does not' are kept; "
          "the file-count warning needs a figure in the answer: OK")


def main() -> int:
    check_time_and_place()
    assert not MISSES, f"{len(MISSES)} mismatches:" + "".join("\n  " + miss for miss in MISSES)
    check_content_questions_stay()
    check_no_answer_and_warning()
    started = time.perf_counter()
    many = [f"D:/big/folder {i % 40}/file {i}.{'png' if i % 3 else 'txt'}" for i in range(12000)]
    library_answer("how many files were added today", many, clock=lambda: NOW, dates=lambda p: (NOW - 100, NOW - 100))
    took = time.perf_counter() - started
    assert took < 2.0, f"a time question over 12,000 files took {took:.2f} s"
    print(f"4. A time question over 12,000 indexed files, without the cost of reading the dates from disk: {took * 1000:.0f} ms: OK")
    print("\nAsk time and place checks OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
