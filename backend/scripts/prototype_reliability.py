"""Phase 11 regression: reliability and the file lifecycle.

- corrupt / unsupported / odd files (bad PDF, bad DOCX, bad XLSX, bad
  PPTX, zero bytes, binary disguised as .txt, a 3 MB single line) never
  abort a scan: they are counted, listed with a reason, and the good
  files around them are indexed;
- a file that vanishes between the walk and the read is skipped;
- an unreadable file (permissions) is a counted failure, and becomes
  indexed on the next scan once readable;
- a crash mid-index (record written, chunks not) is repaired by the next
  scan without rescanning anything else.

Run with:  backend/venv/bin/python backend/scripts/prototype_reliability.py
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.indexing import Indexer, index_folder  # noqa: E402
from app.indexing import folder_scan  # noqa: E402
from app.search import SearchService  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402

MODEL_DIR = default_model_dir(Path(__file__).resolve().parents[1] / "models")


def main() -> None:
    workdir = Path(tempfile.mkdtemp())
    try:
        folder = workdir / "docs"
        folder.mkdir()
        good = {
            "good_one.txt": "The quarterly budget meeting moved to Thursday because the projector broke.",
            "good_two.md": "Sourdough needs a long cold ferment for flavour; bake at 230 C in a dutch oven.",
            "good_three.txt": "Car insurance renewal is due on 30 September; the excess is 350 euros.",
        }
        for name, text in good.items():
            (folder / name).write_text(text)
        bad = {
            "broken.pdf": b"%PDF-1.4 garbage \x00\x01\x02 not really a pdf",
            "broken.docx": b"PK\x03\x04 this is not a zip",
            "broken.xlsx": b"PK\x03\x04 nor is this",
            "broken.pptx": b"\x00\x00\x00 slides? no",
            "empty.txt": b"",
            "binary_disguised.txt": bytes(range(256)) * 200,
            "csv_injection.csv": b"name,amount\n=cmd|' /C calc'!A0,10\n=HYPERLINK(\"http://evil\"),20\n",
        }
        for name, data in bad.items():
            (folder / name).write_bytes(data)
        (folder / "one_long_line.txt").write_text("x" * (3 * 1024 * 1024))

        model = EmbeddingModel(MODEL_DIR)
        vector_store = LanceDBVectorStore(str(workdir / "vectors"))
        keyword_store = KeywordStore(workdir / "keyword.db")
        record_store = FileRecordStore(workdir / "files.db")
        indexer = Indexer(model, vector_store, keyword_store, record_store)
        search = SearchService(model, vector_store, keyword_store, record_store)

        # --- 1. Corrupt / odd files never abort the scan; good files are searchable ---
        folder_scan.RECENT_FAILURES.clear()
        progress = {}
        def on_progress(done, total, current, failed):
            progress.update(done=done, total=total, failed=failed)
        index_folder(indexer, str(folder), progress=on_progress)
        active = {Path(r.path).name for r in record_store.list_active()}
        assert set(good) <= active, active
        assert search.search("projector broke thursday", top_k=3)[0]["filename"] == "good_one.txt"
        assert search.search("insurance excess", top_k=3)[0]["filename"] == "good_three.txt"
        failures = {Path(f["path"]).name: f["error"] for f in folder_scan.RECENT_FAILURES}
        assert {"broken.pdf", "broken.docx", "broken.xlsx", "broken.pptx"} <= set(failures), failures
        assert all(err for err in failures.values())
        # The CSV formula cells are indexed as plain text — never evaluated.
        hit = search.search('"cmd"', top_k=3)
        assert hit and hit[0]["filename"] == "csv_injection.csv" and "=cmd" in hit[0]["chunk_text"]
        assert progress["failed"] == len({"broken.pdf", "broken.docx", "broken.xlsx", "broken.pptx"}), progress
        print(f"1. {progress['failed']} corrupt files counted and listed with reasons ({', '.join(sorted(failures))}); empty, binary-as-text, 3 MB line and formula-injection CSV handled; good files searchable: OK")

        # --- 2. A file that vanishes between the walk and the read is skipped, not fatal ---
        (folder / "fleeting.txt").write_text("this file will be gone before it is read " * 5)
        real_discover = folder_scan.discover_files
        def discover_then_delete(roots, extensions=None, on_error=None):
            paths = list(real_discover(roots, extensions, on_error=on_error))
            (folder / "fleeting.txt").unlink()
            return iter(paths)
        folder_scan.discover_files = discover_then_delete
        try:
            index_folder(indexer, str(folder))
        finally:
            folder_scan.discover_files = real_discover
        assert record_store.get_by_path(str(folder / "fleeting.txt")) is None
        assert record_store.get_by_path(str(folder / "good_one.txt")).indexed
        print("2. A file deleted between the walk and the read is skipped; the scan finishes and the rest stays indexed: OK")

        # --- 3. An unreadable file is a counted failure, then indexed once readable ---
        locked = folder / "locked.txt"
        locked.write_text("secret notes about the venue booking for the reunion " * 3)
        os.chmod(locked, 0)
        folder_scan.RECENT_FAILURES.clear()
        if os.geteuid() == 0:
            print("3. (running as root — permission check skipped)")
        else:
            index_folder(indexer, str(folder))
            assert any(Path(f["path"]).name == "locked.txt" and "Permission" in f["error"] for f in folder_scan.RECENT_FAILURES), folder_scan.RECENT_FAILURES
            assert record_store.get_by_path(str(locked)) is None or not record_store.get_by_path(str(locked)).indexed
            os.chmod(locked, 0o644)
            index_folder(indexer, str(folder))
            assert record_store.get_by_path(str(locked)).indexed
            assert search.search("venue booking reunion", top_k=3)[0]["filename"] == "locked.txt"
            print("3. Unreadable file: counted with 'PermissionError', not fatal; indexed on the next scan once readable: OK")

        # --- 4. Crash mid-index: record saved, chunks never stored → repaired by the next scan, nothing else rescanned ---
        victim = record_store.get_by_path(str(folder / "good_two.txt")) or record_store.get_by_path(str(folder / "good_two.md"))
        indexer.delete_file(victim.file_id)              # simulate: chunks lost…
        record_store.mark_indexed(victim.file_id, False)  # …and the record left "pending", as a crash between the two would
        assert search.search("sourdough cold ferment", top_k=3) == [] or search.search("sourdough cold ferment", top_k=3)[0]["filename"] != "good_two.md"
        assert [r.file_id for r in record_store.list_unfinished()] == [victim.file_id]
        hashed = []
        real_hash = folder_scan.compute_file_hash
        folder_scan.compute_file_hash = lambda p: (hashed.append(p.name), real_hash(p))[1]
        try:
            index_folder(indexer, str(folder))
        finally:
            folder_scan.compute_file_hash = real_hash
        assert "good_two.md" in hashed and "good_one.txt" not in hashed, f"only the unfinished file should be re-read: {hashed}"
        assert record_store.list_unfinished() == []
        assert search.search("sourdough cold ferment", top_k=3)[0]["filename"] == "good_two.md"
        print("4. Crash mid-index repaired by the next scan: only the unfinished file was re-read, the rest skipped on size+mtime: OK")

        print("\nPhase 11 reliability OK: corrupt files, vanishing files, unreadable files and a crash mid-index are all survived and reported.")
    finally:
        for p in workdir.rglob("*"):
            try:
                os.chmod(p, 0o644)
            except OSError:
                pass
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
