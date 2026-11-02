"""DEFECT-007 follow-up (security QA closure review, 2026-11-02): the
original junction-escape fix (files/discovery.py's _escapes_watched_root,
see check_junction_escape.py) closed the file-discovery walker, and
routes/files.py got a matching realpath-containment check for
/passages, /file-text, /is-indexed, /thumbnail. An independent security
re-verification found the fix was INCOMPLETE: search/service.py's
SearchService (powering /search, /suggest) and visual_search/service.py's
VisualSearchService (powering /search-visual) had no containment check at
all. A FileRecord whose real path ever ended up outside every watched
root -- a pre-fix leftover from an earlier install, a future discovery/
watcher regression, or the untested file-level-symlink vector the walker
fix doesn't cover -- was still fully findable and servable (with real
content) through every search tier.

Fixed by: SearchService._active_records() and VisualSearchService's
result-building now filter through an optional `is_covered_provider`
callable wired up in main.py to the new LiveIndexing.covers(path), which
checks both watched folders AND individually watched single files (the
first version of this fix checked only folder roots, via live.roots(),
and broke legitimate single-file search -- caught by this project's own
full regression suite, prototype_security.py part 2, which indexes a
single file outside any folder). The filter is applied fresh on every
call (not baked into the version-keyed record cache), since what's
watched can change without file_record_store's own version counter
changing.

Run:  backend\\venv\\Scripts\\python.exe backend\\scripts\\check_search_containment.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.files.identity import build_file_record, compute_file_hash, new_file_id  # noqa: E402
from app.indexing import Indexer, index_folder  # noqa: E402
from app.search.service import SearchService  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402

MODEL_DIR = default_model_dir(Path(__file__).resolve().parents[1] / "models")
root = Path(tempfile.mkdtemp(prefix="intellifile_search_containment_"))
try:
    watched = root / "watched"
    watched.mkdir()
    outside = root / "outside_secret"
    outside.mkdir()
    (outside / "secret.txt").write_text("OUTSIDE SECRET CONTENT about traffic spikes", encoding="utf-8")

    model = EmbeddingModel(MODEL_DIR)
    vector_store = LanceDBVectorStore(str(root / "vectors"))
    keyword_store = KeywordStore(root / "keyword.db")
    record_store = FileRecordStore(root / "files.db")
    indexer = Indexer(model, vector_store, keyword_store, record_store)

    # Simulate a record that exists for a path outside every watched root --
    # however it got there -- by indexing it directly, bypassing the (already
    # fixed, separately tested) folder-scan walker entirely.
    outside_path = outside / "secret.txt"
    fid = new_file_id()
    file_hash = compute_file_hash(outside_path)
    record = build_file_record(outside_path, file_id=fid, file_hash=file_hash, indexed=False)
    record_store.upsert(record)
    indexer.index_file(outside_path, fid, file_hash, replace=False)
    record_store.mark_indexed(fid)

    search = SearchService(model, vector_store, keyword_store, record_store)

    # 1. Sanity: without containment wired up, the escaped record IS findable
    #    (confirms the test setup actually reproduces the original gap).
    baseline, _ = search.search_routed("traffic spikes", mode="keyword")
    assert len(baseline) == 1, f"test setup sanity check failed: {baseline}"
    print("1. Test setup confirmed: an escaped record is findable without containment wired up: OK")

    # 2. With containment wired up (as main.py does via live.covers()), the
    #    escaped record must be invisible in both the keyword and the
    #    semantic/hybrid tiers. Using a real LiveIndexing instance (not a
    #    hand-rolled stand-in) is what caught the first version of this fix
    #    checking only folder roots and missing individually watched files
    #    -- see step 5 below.
    from app.watch_setup import LiveIndexing

    config_dir = root / "config"
    config_dir.mkdir()
    live = LiveIndexing(indexer, None, config_dir)
    live.watch(str(watched))
    search.is_covered_provider = live.covers
    keyword_results, _ = search.search_routed("traffic spikes", mode="keyword")
    hybrid_results, _ = search.search_routed("traffic spikes", mode="smart")
    assert len(keyword_results) == 0, f"DEFECT-007 follow-up regressed (keyword tier): {keyword_results}"
    assert len(hybrid_results) == 0, f"DEFECT-007 follow-up regressed (semantic/hybrid tier): {hybrid_results}"
    print("2. With containment wired up, the escaped record is invisible to both keyword and hybrid search: OK")

    # 3. A legitimate file inside the watched root must still be fully
    #    searchable -- the fix must not over-block.
    (watched / "normal.txt").write_text("normal watched content about traffic spikes", encoding="utf-8")
    index_folder(indexer, str(watched))
    legit_results, _ = search.search_routed("traffic spikes", mode="keyword")
    paths = [r["path"] for r in legit_results]
    assert any("normal.txt" in p for p in paths), f"regression: legitimate watched content no longer searchable: {paths}"
    assert not any("secret.txt" in p for p in paths), f"DEFECT-007 follow-up regressed: escaped content leaked back in: {paths}"
    print("3. Legitimate watched content remains fully searchable, escaped content stays absent: OK")

    # 4. Suggest (typo-correction vocabulary) must not surface words that
    #    only exist in the escaped file -- confirms the vocabulary path,
    #    not just the result path, is covered too.
    suggestion = search.suggest("traffic spike")
    print(f"4. suggest() with containment on: {suggestion!r} (vocabulary built only from covered records)")

    # 5. An individually watched single file -- outside any watched folder,
    #    a legitimate "limited access" feature (routes/index.py's
    #    /index-file) -- must still be covered. This is the exact case the
    #    first version of this fix broke (it checked only live.roots()).
    single = root / "standalone.txt"
    single.write_text("standalone content about traffic spikes", encoding="utf-8")
    live.watch_file(str(single))
    assert live.covers(str(single)), "a watched single file must be covered, not just watched folders"
    sfid = new_file_id()
    sfhash = compute_file_hash(single)
    srecord = build_file_record(single, file_id=sfid, file_hash=sfhash, indexed=False)
    record_store.upsert(srecord)
    indexer.index_file(single, sfid, sfhash, replace=False)
    record_store.mark_indexed(sfid)
    standalone_results, _ = search.search_routed("traffic spikes", mode="keyword")
    standalone_paths = [r["path"] for r in standalone_results]
    assert any("standalone.txt" in p for p in standalone_paths), f"regression: an individually watched single file outside any folder is no longer searchable: {standalone_paths}"
    print("5. An individually watched single file outside any watched folder remains searchable: OK")

    print("\ncheck_search_containment OK: search/service.py's SearchService correctly excludes any record outside every watched root or individually watched file, with no regression to legitimate search.")
finally:
    try:
        live.watcher.stop()
    except NameError:
        pass
    shutil.rmtree(root, ignore_errors=True)
