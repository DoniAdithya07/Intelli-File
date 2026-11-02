"""New/modified/renamed/unchanged/deleted counters added to the fresh-scan
(folder_scan.SCAN_STATS), for the Agent Manager / Status screen summary.
Minimal check: not part of run_all_phases.py's 35, run by hand or added
there later if it earns a permanent slot.

Run:  backend\\venv\\Scripts\\python.exe backend\\scripts\\check_scan_stats.py
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("INTELLIFILE_OFFLINE_GUARD", "1")

from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.files.identity import compute_file_hash  # noqa: E402
from app.indexing import Indexer, index_folder  # noqa: E402
from app.indexing.folder_scan import SCAN_STATS  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402

MODEL_DIR = default_model_dir(Path(__file__).resolve().parents[1] / "models")
root = Path(tempfile.mkdtemp(prefix="intellifile_scanstats_"))
try:
    model = EmbeddingModel(MODEL_DIR)
    vector_store = LanceDBVectorStore(str(root / "vectors"))
    keyword_store = KeywordStore(root / "keyword.db")
    record_store = FileRecordStore(root / "files.db")
    indexer = Indexer(model, vector_store, keyword_store, record_store)
    folder = root / "watched"
    folder.mkdir()

    (folder / "a.txt").write_text("alpha content about traffic spikes", encoding="utf-8")
    index_folder(indexer, str(folder))
    assert SCAN_STATS[str(folder)]["new"] == 1, SCAN_STATS[str(folder)]
    print("1. First scan counts the new file: OK")

    index_folder(indexer, str(folder))
    assert SCAN_STATS[str(folder)]["unchanged"] == 1, SCAN_STATS[str(folder)]
    assert SCAN_STATS[str(folder)].get("new", 0) == 0
    print("2. Re-scan with nothing changed counts it as unchanged, not new: OK")

    time.sleep(0.05)
    (folder / "a.txt").write_text("alpha content, edited", encoding="utf-8")
    os.utime(folder / "a.txt", None)
    index_folder(indexer, str(folder))
    assert SCAN_STATS[str(folder)]["modified"] == 1, SCAN_STATS[str(folder)]
    print("3. Editing the file counts it as modified: OK")

    (folder / "a.txt").rename(folder / "b.txt")
    index_folder(indexer, str(folder))
    assert SCAN_STATS[str(folder)]["renamed"] == 1, SCAN_STATS[str(folder)]
    print("4. Renaming counts it as renamed, not new+deleted: OK")

    (folder / "b.txt").unlink()
    index_folder(indexer, str(folder))
    assert SCAN_STATS[str(folder)]["deleted"] == 1, SCAN_STATS[str(folder)]
    print("5. Removing the file counts it as deleted: OK")

    # DEFECT-001 (2026-11-02): a same-byte-length edit with its mtime
    # restored to the original value (cp -p, robocopy /COPYALL, some
    # backup/restore and sync tools) used to be reported "unchanged" and
    # never re-indexed, because the stat-only fast path trusted size+mtime
    # alone. Fixed in folder_scan.py by only trusting that fast path for
    # files above MAX_DOCUMENT_FILE_BYTES; bounded-size files always hash.
    (folder / "c.txt").write_text("same length AAAA", encoding="utf-8")
    index_folder(indexer, str(folder))
    stat_before = (folder / "c.txt").stat()
    hash_before = record_store.get_by_path(str(folder / "c.txt")).hash
    new_content = "same length BBBB"
    assert len(new_content) == len("same length AAAA")  # the defect only reproduces when byte length is unchanged
    (folder / "c.txt").write_text(new_content, encoding="utf-8")
    os.utime(folder / "c.txt", (stat_before.st_atime, stat_before.st_mtime))  # restore the original mtime exactly
    index_folder(indexer, str(folder))
    assert SCAN_STATS[str(folder)]["modified"] == 1, f"DEFECT-001 regressed: {SCAN_STATS[str(folder)]}"
    assert SCAN_STATS[str(folder)].get("unchanged", 0) == 0, "DEFECT-001 regressed: reported unchanged despite different content"
    hash_after = record_store.get_by_path(str(folder / "c.txt")).hash
    assert hash_after != hash_before, "DEFECT-001 regressed: stored hash did not change despite different content"
    print("6. DEFECT-001 fix: same-size edit with mtime restored is detected as modified, not unchanged: OK")

    # DEFECT-002 (2026-11-02): compute_file_hash() (before extraction) and
    # the extraction read inside Indexer.index_file() are two separate
    # reads. If the file's bytes change in between, the previously-stored
    # hash could describe content different from what was actually
    # extracted and indexed, with no self-healing. Fixed in folder_scan.py
    # by re-hashing after extraction and retrying with the fresh hash
    # (bounded by MAX_RACE_RETRIES) until the hash and the indexed content
    # agree, instead of ever committing a mismatch.
    from app.indexing.folder_scan import MAX_RACE_RETRIES  # noqa: E402

    original_index_file = indexer.index_file
    recovers_after = {"calls": 0}

    def racy_once(path, file_id, file_hash, replace=False):
        result = original_index_file(path, file_id, file_hash, replace=replace)
        recovers_after["calls"] += 1
        if recovers_after["calls"] == 1:
            # Simulate another process rewriting the file right after
            # extraction read it — the race window DEFECT-002 closes.
            Path(path).write_text("raced content after extraction", encoding="utf-8")
        return result

    indexer.index_file = racy_once
    try:
        (folder / "d.txt").write_text("initial content for race test", encoding="utf-8")
        index_folder(indexer, str(folder))
    finally:
        indexer.index_file = original_index_file
    assert recovers_after["calls"] == 2, f"expected exactly one retry to recover, got {recovers_after['calls']} attempts"
    d_record = record_store.get_by_path(str(folder / "d.txt"))
    assert d_record is not None and d_record.indexed, "DEFECT-002 regressed: file never reached indexed=True after recovering from the race"
    assert d_record.hash == compute_file_hash(folder / "d.txt"), "DEFECT-002 regressed: stored hash does not match the file's actual current content"
    print("7. DEFECT-002 fix: a race during extraction is detected and recovered by retrying with the fresh hash: OK")

    # A file that keeps changing on every single attempt must not hang,
    # crash, or ever commit a hash that doesn't match real content — it
    # should give up gracefully after MAX_RACE_RETRIES and leave nothing
    # active and inconsistent behind.
    always_racy_calls = {"n": 0}

    def always_racy(path, file_id, file_hash, replace=False):
        result = original_index_file(path, file_id, file_hash, replace=replace)
        always_racy_calls["n"] += 1
        Path(path).write_text(f"raced content v{always_racy_calls['n']}", encoding="utf-8")
        return result

    indexer.index_file = always_racy
    try:
        (folder / "e.txt").write_text("initial content for exhaustion test", encoding="utf-8")
        index_folder(indexer, str(folder))  # must return normally, not raise
    finally:
        indexer.index_file = original_index_file
    assert always_racy_calls["n"] == MAX_RACE_RETRIES, f"expected exactly {MAX_RACE_RETRIES} attempts before giving up, got {always_racy_calls['n']}"
    e_record = record_store.get_by_path(str(folder / "e.txt"))
    assert e_record is None or not e_record.indexed, "DEFECT-002 regressed: a never-stabilized file was left marked as indexed"
    print("8. DEFECT-002 fix: a file that never stabilizes is abandoned gracefully after MAX_RACE_RETRIES attempts, no crash, no mismatched metadata: OK")

    print("\ncheck_scan_stats OK: new/modified/renamed/unchanged/deleted all counted correctly, and both DEFECT-001/DEFECT-002 fixes hold.")
finally:
    shutil.rmtree(root, ignore_errors=True)
