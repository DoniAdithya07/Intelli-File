"""DEFECT (security QA, 2026-11-02): an NTFS junction planted inside a
watched folder, pointing outside every root the user consented to index,
used to be walked straight through at scan time (os.walk's own
followlinks=False guard never fires for a junction — os.path.islink()
checks specifically for IO_REPARSE_TAG_SYMLINK, and junctions use the
different IO_REPARSE_TAG_MOUNT_POINT tag). Content outside the watched
folder would then be indexed and served (search/passages/file-text) as if
it belonged to the watched folder. No admin privilege is needed to create
a junction (mklink /J), so this was reachable by any folder a user later
points IntelliFile at.

Fixed in two independent places:
  1. files/discovery.py's discover_files() now prunes any subdirectory
     whose real (reparse-resolved) path escapes the root being walked.
  2. routes/files.py's /passages and /file-text now apply the same
     realpath-containment check /is-indexed and /thumbnail already had,
     as a second backstop.

This check verifies (1) directly, with a real junction (requires no
elevation on a normal Windows account). Run:
    backend\\venv\\Scripts\\python.exe backend\\scripts\\check_junction_escape.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.files.discovery import discover_files  # noqa: E402

root = Path(tempfile.mkdtemp(prefix="intellifile_junction_escape_"))
try:
    watched = root / "watched"
    watched.mkdir()
    outside = root / "outside_secret"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP_SECRET_MARKER", encoding="utf-8")
    (watched / "normal.txt").write_text("ordinary watched content", encoding="utf-8")

    junction = watched / "escape_junction"
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(outside)], capture_output=True, text=True)
    assert made.returncode == 0, f"could not create test junction (unexpected on a normal account): {made.stderr}"

    found = [p.name for p in discover_files([str(watched)])]
    assert "secret.txt" not in found, f"junction escape regressed: outside content discovered: {found}"
    assert "normal.txt" in found, f"normal watched content must still be discovered: {found}"
    print(f"1. Junction pointing outside the watched folder is not walked (found only: {sorted(found)}): OK")

    print("\ncheck_junction_escape OK: a planted junction cannot pull content from outside the watched folder into the index.")
finally:
    shutil.rmtree(root, ignore_errors=True)
