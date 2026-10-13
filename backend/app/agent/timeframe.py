"""When and where, in a question about the collection ("how many images are
downloaded in last 1 hour", "what did i add today", "newest files on the
desktop").

Found in use on 2026-10-14: the collection parser (library.py) counted kinds
and types of file but knew no time and no "downloaded", so that question
went to the language model, which wrote 37 seconds of nothing. This module
only takes those words out of the question and says what they meant; the
rest of the question still has to be a collection question for library.py,
so "how many photos did we take in lisbon last month" stays a question
about what a file says.

The index stores no dates. "Added" (downloaded, created, saved, new) is the
file's creation time on this computer; "changed" (modified, edited, updated)
its modified time. library.py reads them with os.stat for the files that
passed every other filter.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
_UNITS = {"minute": 60, "min": 60, "hour": 3600, "hr": 3600, "day": 86400, "week": 7 * 86400, "month": 30 * 86400, "year": 365 * 86400}
_UNIT_NAMES = {"min": "minute", "hr": "hour"}
RECENT_DAYS = 7  # "recently", "new" with no window

_NUMBER = r"\d+|" + "|".join(_NUMBERS)
_UNIT = "|".join(_UNITS)
# "in the last 2 hours", "in last 1 hour", "past 24 hours", "the last week"
_ROLLING = re.compile(rf"\b(?:(in|within|over|during|for)\s+)?(the\s+)?(?:last|past|previous)\s+(?:({_NUMBER})\s+)?({_UNIT})s?\b")
# "today", "since yesterday", "this week"
_CALENDAR = re.compile(r"\b(?:(since|from)\s+)?(today|yesterday|tonight|this (?:morning|afternoon|evening|week|month|year))\b")
_RECENT = re.compile(r"\b(?:recently|lately|recent)\b")
# What happened to the file in that time. "new" only counts with a window or on its own as "recent".
_ADDED = r"downloaded|download|added|add|created|create|saved|save|received|receive|copied|arrived|got|gotten|new|newly"
_CHANGED = r"modified|modify|changed|change|edited|edit|updated|update"
# With the words that only carry the verb ("did i download", "were added"), so what is left
# reads like the plain question: "how many quizzes did i download" becomes "how many quizzes".
_VERB = re.compile(rf"\b(?:(?:did|have|has|had)\s+(?:i|we|you)\s+(?:just\s+|already\s+)?|(?:was|were|been|got|get)\s+)?({_ADDED}|{_CHANGED})\b")
_ORDER = re.compile(r"\b(?:(\d+)\s+)?(newest|latest|most recent|oldest|earliest)(?:\s+(\d+))?\b")
# "in my downloads", "on the desktop": the folder with that name. A word that is also a kind of
# file (documents, pictures, videos, music) is a place only with "my": "how many files in documents"
# keeps counting documents.
_PLACE = re.compile(r"\b(?:in|from|on|inside|into|to|under)\s+(?:(?:the|my)\s+)?(downloads?|desktop)\b(?!\s+folder)|\b(?:in|from|inside)\s+my\s+(documents|pictures|videos|music)\b(?!\s+folder)")
# "what did i download today" lists; the parser's "what" wants the thing listed first.
_WHAT_DID = re.compile(rf"^(?:what|which)(?:\s+files?)?\s+(?:did|have|has)\s+(?:i|we|you)\s+(?=(?:{_ADDED}|{_CHANGED})\b)")
_WHAT_ARE = re.compile(r"^(?:what|which)\s+(?:are|is|were)\s+(?:the\s+|my\s+)?(?=(?:\d+\s+)?(?:newest|latest|most recent|oldest|earliest)\b)")


@dataclass
class Narrowing:
    text: str                       # the question without the time, place and verb words
    since: float | None = None      # files dated at or after this ...
    until: float | None = None      # ... and before this (None: now)
    label: str = ""                 # "in the last hour", "today", "since yesterday"
    changed: bool = False           # the modified time, not the time the file appeared
    verb_said: bool = False         # "downloaded", "added", "changed" was in the question
    order: str | None = None        # "newest" / "oldest"
    limit: int = 10                 # how many to list for newest / oldest
    short: bool = False             # a window of an hour or less: the newest files may not be indexed yet
    placed: bool = False            # a place was said ("downloaded", "on the desktop") and is now "in the X folder"

    @property
    def timed(self) -> bool:
        return self.since is not None or self.order is not None


def _start_of_day(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def _calendar(word: str, since_word: bool, now: datetime) -> tuple[float, float | None, str]:
    today = _start_of_day(now)
    if word == "today":
        return today.timestamp(), None, "today"
    if word == "yesterday":
        start = today - timedelta(days=1)
        return (start.timestamp(), None, "since yesterday") if since_word else (start.timestamp(), today.timestamp(), "yesterday")
    if word == "tonight" or word == "this evening":
        return today.replace(hour=18).timestamp(), None, word
    if word == "this morning":
        return today.timestamp(), today.replace(hour=12).timestamp(), word
    if word == "this afternoon":
        return today.replace(hour=12).timestamp(), today.replace(hour=18).timestamp(), word
    if word == "this week":
        return (today - timedelta(days=today.weekday())).timestamp(), None, word  # weeks start on Monday
    if word == "this month":
        return today.replace(day=1).timestamp(), None, word
    return today.replace(month=1, day=1).timestamp(), None, word  # this year


def _previous(unit: str, now: datetime) -> tuple[float, float, str]:
    """"last week", "last month", "last year" said bare: the calendar one before this one."""
    today = _start_of_day(now)
    if unit == "week":
        this = today - timedelta(days=today.weekday())
        return (this - timedelta(days=7)).timestamp(), this.timestamp(), "last week"
    if unit == "month":
        this = today.replace(day=1)
        before = (this - timedelta(days=1)).replace(day=1)
        return before.timestamp(), this.timestamp(), "last month"
    this = today.replace(month=1, day=1)
    return this.replace(year=this.year - 1).timestamp(), this.timestamp(), "last year"


def narrow(text: str, now: float) -> Narrowing | None:
    """The time, the place and the "added / changed" in a question already
    lower-cased and typo-fixed by library._words; None when it has none."""
    found = Narrowing(text=text)
    moment = datetime.fromtimestamp(now)
    text = _WHAT_DID.sub("list files ", text)
    text = _WHAT_ARE.sub("list ", text)
    rolling = _ROLLING.search(text)
    if rolling:
        lead, the, number, unit = rolling.groups()
        count = int(number) if number and number.isdigit() else _NUMBERS.get(number or "", 1)
        name = _UNIT_NAMES.get(unit, unit)
        # "last week" on its own is the week before this one; "in the last week" and
        # "last 2 weeks" count back from now. Minutes, hours and days always count back.
        if not (lead or the or number) and unit in ("week", "month", "year"):
            found.since, found.until, found.label = _previous(unit, moment)
        else:
            found.since = now - count * _UNITS[unit]
            found.label = f"in the last {name}" if count == 1 else f"in the last {count:,} {name}s"
            found.short = count * _UNITS[unit] <= 3600
        text = text[:rolling.start()] + " " + text[rolling.end():]
    else:
        calendar = _CALENDAR.search(text)
        if calendar:
            found.since, found.until, found.label = _calendar(calendar.group(2), bool(calendar.group(1)), moment)
            text = text[:calendar.start()] + " " + text[calendar.end():]
    order = _ORDER.search(text)
    if order:
        found.order = "oldest" if order.group(2) in ("oldest", "earliest") else "newest"
        asked_for = order.group(1) or order.group(3)
        following = text[order.end():].split()[:1]
        if asked_for:
            found.limit = max(1, min(int(asked_for), 50))
        elif following and not following[0].endswith("s") and following[0] not in ("music", "audio"):
            found.limit = 1  # "the latest file", "the newest photo": one
        text = text[:order.start()] + " " + text[order.end():]
    downloaded = False
    verb = _VERB.search(text)
    if verb:
        word = verb.group(1)
        found.verb_said = True
        found.changed = re.fullmatch(_CHANGED, word) is not None
        downloaded = word.startswith("download")
        text = text[:verb.start()] + " " + text[verb.end():]
    recent = _RECENT.search(text)
    if found.since is None and not found.order and (recent or (verb and verb.group(1) in ("new", "newly"))):
        found.since, found.label = now - RECENT_DAYS * 86400, f"in the last {RECENT_DAYS} days"
    if recent:
        text = text[:recent.start()] + " " + text[recent.end():]
    place = _PLACE.search(text)
    if place:
        raw = place.group(1) or place.group(2)
        name = "downloads" if raw.startswith("download") else raw
        text = text[:place.start()] + f" in the {name} folder " + text[place.end():]
        found.placed = True
    elif downloaded and not re.search(r"(?:folder|directory)", text):
        text += " in the downloads folder"
        found.placed = True
    text = re.sub(r"\s+", " ", text).strip()
    if text == found.text:
        return None
    found.text = text
    return found


def ago(seconds: float, moment: float) -> str:
    """How long ago, for a person: "just now", "5 minutes ago", "3 days ago", "on 12 March 2026"."""
    if seconds < 60:
        return "just now"
    for size, name in ((3600, "minute"), (86400, "hour"), (60 * 86400, "day")):
        if seconds < size:
            n = int(seconds // (size // 60 if name == "minute" else 3600 if name == "hour" else 86400))
            return f"{n:,} {name}{'s' if n != 1 else ''} ago"
    then = datetime.fromtimestamp(moment)
    return f"on {then.day} {then.strftime('%B %Y')}"
