"""Accented filename matching check (2026-10-29): searching an unaccented
guess at a filename ("cafe resume") did not find a file named "Café
résumé...", because match_filenames tokenized names and the query without
folding accents the way the content index already does (search/service.py).
Pure in-process check, no backend to start. Exits non-zero on failure."""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.files.identity import FileRecord
from app.search.filenames import match_filenames


def record(path: str) -> FileRecord:
    return FileRecord(file_id=hash(path) & 0xFFFF, path=path, size=10, modified_time=time.time(), hash="a" * 64)


def main() -> None:
    accented = record("C:/docs/Café résumé final.pdf")
    unrelated = record("C:/docs/unrelated.pdf")

    matches = match_filenames("cafe resume", [accented, unrelated])
    assert [m.record.path for m in matches] == [accented.path], matches

    # An accented query must still find the plain-ASCII file it already found.
    plain = record("C:/docs/Rasmalai.txt")
    matches = match_filenames("rásmalái", [plain])
    assert [m.record.path for m in matches] == [plain.path], matches

    print("check_filename_accents: OK")


if __name__ == "__main__":
    main()
