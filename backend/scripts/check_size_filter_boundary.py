"""Regression for the size:>=/<= filter boundary bug (2026-10-29 review):
_parse_size() only distinguished "less than" from everything else, so
QueryFilters.matches()'s strict > / < comparison wrongly excluded a file of
exactly the stated size for size:>=N and size:<=N (size:>N and size:<N were
already correct by design). Pure in-process check, no backend needed.
Exits non-zero on failure."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.search.query_parsing import parse_query

TEN_MB = 10 * 1024 * 1024


def main() -> None:
    ge, le = parse_query("size:>=10mb").filters, parse_query("size:<=10mb").filters
    gt, lt = parse_query("size:>10mb").filters, parse_query("size:<10mb").filters

    assert ge.matches("a.txt", TEN_MB, 0), "size:>=10mb must include an exact 10MB file"
    assert le.matches("a.txt", TEN_MB, 0), "size:<=10mb must include an exact 10MB file"
    assert not gt.matches("a.txt", TEN_MB, 0), "size:>10mb must still exclude an exact 10MB file"
    assert not lt.matches("a.txt", TEN_MB, 0), "size:<10mb must still exclude an exact 10MB file"
    assert ge.matches("a.txt", TEN_MB + 1, 0) and not ge.matches("a.txt", TEN_MB - 1, 0)
    assert le.matches("a.txt", TEN_MB - 1, 0) and not le.matches("a.txt", TEN_MB + 1, 0)

    print("check_size_filter_boundary: OK")


if __name__ == "__main__":
    main()
