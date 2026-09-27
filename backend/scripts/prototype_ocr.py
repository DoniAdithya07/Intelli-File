"""Next-round improvement 4 regression: offline OCR with Windows' engine.

Builds, on the fly:
  - a scanned lecture handout: a 2-page PDF made only of page images (no
    text layer — what a photocopier or phone scanner produces);
  - a screenshot and a photographed-whiteboard-style image with text;
  - three images with no text (flat colour, shapes, noise) standing in
    for ordinary photos;
  - two ordinary text files as distractors;
then indexes them through the real pipeline and checks:

1. Windows' OCR is available and reads rendered text word for word;
2. the scanned PDF, which had no text at all before, now yields one block
   per page with the right page numbers;
3. search finds the scanned PDF and the screenshot by phrases that only
   exist as pixels, on the right page;
4. images without text get no text chunks (OCR_MIN_WORDS), so ordinary
   photos add nothing to text search;
5. OCR time per image / per page is reported.

Windows only (skips elsewhere).

Run with:  backend\\venv\\Scripts\\python.exe backend\\scripts\\prototype_ocr.py
"""

import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app.embeddings.model import EmbeddingModel, default_model_dir  # noqa: E402
from app.extraction import ocr  # noqa: E402
from app.extraction.extractor import extract_document  # noqa: E402
from app.files.identity import build_file_record  # noqa: E402
from app.indexing import Indexer  # noqa: E402
from app.indexing.indexer import CHUNKS_TABLE, OCR_MIN_WORDS  # noqa: E402
from app.search import SearchService  # noqa: E402
from app.storage import FileRecordStore, KeywordStore, LanceDBVectorStore  # noqa: E402


def text_image(lines: list[str], size=(1600, 900), font_size=44) -> Image.Image:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype("arial.ttf", font_size)
    for i, line in enumerate(lines):
        draw.text((70, 70 + i * int(font_size * 1.6)), line, fill="black", font=font)
    return img


def main() -> int:
    if sys.platform != "win32":
        print("Windows only: skipped.")
        return 0
    assert ocr.available(), "Windows OCR engine not available (no recognizer for the user's languages?)"
    work = Path(tempfile.mkdtemp(prefix="intellifile_ocr_"))
    try:
        files = work / "files"
        files.mkdir()
        # 1. word-for-word on a rendered image
        board = ["Sprint retro: move the database migration", "to Thursday 14 May. Owner: Priya.", "Budget approved: 2,400 euros"]
        text_image(board).save(files / "whiteboard.png")
        t0 = time.perf_counter()
        read = ocr.ocr_image(files / "whiteboard.png")
        image_seconds = time.perf_counter() - t0
        assert read.split() == " ".join(board).split(), read
        print(f"1. Windows OCR reads a rendered whiteboard word for word ({len(read.split())} words, {image_seconds:.2f} s): OK")

        # 2. scanned PDF: page images only
        pages = [
            text_image(["Lecture 7: Operating Systems", "Page replacement policies", "The clock algorithm approximates least recently used", "by giving each page a second chance."], size=(1240, 1754), font_size=36),
            text_image(["Exam logistics", "The final exam is on 3 June in room B204.", "Bring a scientific calculator."], size=(1240, 1754), font_size=36),
        ]
        pages[0].save(files / "scanned lecture 7.pdf", save_all=True, append_images=pages[1:], resolution=150)
        from pypdf import PdfReader
        assert all(not (p.extract_text() or "").strip() for p in PdfReader(str(files / "scanned lecture 7.pdf")).pages), "fixture must have no text layer"
        t0 = time.perf_counter()
        blocks = extract_document(files / "scanned lecture 7.pdf")
        pdf_seconds = (time.perf_counter() - t0) / 2
        assert [b.page_number for b in blocks] == [1, 2] and "second chance" in blocks[0].text and "B204" in blocks[1].text, blocks
        print(f"2. Scanned PDF with no text layer → {len(blocks)} OCR'd blocks with page numbers 1 and 2 ({pdf_seconds:.2f} s per page): OK")

        # fixtures for search
        text_image(["Error 0x80070005: Access is denied.", "Windows Update could not install KB5031356.", "Try again later or contact your administrator."], size=(1400, 500), font_size=36).save(files / "Screenshot 2026-09-20 101512.png")
        Image.new("RGB", (1200, 800), (40, 120, 200)).save(files / "blue sky.jpg")
        shapes = Image.new("RGB", (1200, 800), (240, 230, 210))
        d = ImageDraw.Draw(shapes)
        for i in range(12):
            d.ellipse((60 + i * 90, 200 + (i % 3) * 120, 150 + i * 90, 290 + (i % 3) * 120), fill=(200, 60 + i * 12, 40))
        shapes.save(files / "beach balls.jpg")
        rng = random.Random(7)
        noise = Image.new("RGB", (800, 600))
        noise.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(800 * 600)])
        noise.save(files / "gravel close-up.jpg")
        (files / "os notes.txt").write_text("Process scheduling: round robin gives each process a time slice. Deadlock needs four conditions.")
        (files / "shopping.txt").write_text("Milk, eggs, bread, a calculator battery and printer paper.")

        model = EmbeddingModel(default_model_dir())
        vectors, keywords, records = LanceDBVectorStore(str(work / "v")), KeywordStore(work / "k.db"), FileRecordStore(work / "f.db")
        indexer = Indexer(model, vectors, keywords, records)
        chunks: dict[str, int] = {}
        for path in sorted(files.iterdir()):
            record = build_file_record(path)
            records.upsert(record)
            if path.suffix.lower() in (".png", ".jpg"):
                chunks[path.name] = indexer.index_image_text(path, record.file_id)
            else:
                chunks[path.name] = indexer.index_file(path, record.file_id, record.hash)
        search = SearchService(model, vectors, keywords, records)

        # 3. search by words that only exist as pixels
        checks = [
            ("clock algorithm second chance", "scanned lecture 7.pdf", 1),
            ("when is the final exam and which room", "scanned lecture 7.pdf", 2),
            ("windows update access denied error", "Screenshot 2026-09-20 101512.png", None),
            ("database migration owner", "whiteboard.png", None),
        ]
        for query, want, page in checks:
            results = search.search(query, top_k=5)
            top = results[0] if results else None
            assert top is not None and top["filename"] == want, (query, [r["filename"] for r in results])
            if page is not None:
                assert top.get("page") == page or top.get("page_number") == page, (query, top.get("page"), top.get("page_number"))
        print(f"3. Search finds text that only exists as pixels: {len(checks)}/{len(checks)} queries return the scanned PDF (right page) or the screenshot first: OK")

        # 4. photos without text stay out of text search
        plain = {n: chunks[n] for n in ("blue sky.jpg", "beach balls.jpg", "gravel close-up.jpg")}
        words = {n: len(ocr.ocr_image(files / n).split()) for n in plain}
        assert all(v == 0 for v in plain.values()), plain
        stored = {r["file_id"] for r in vectors.get_by_file_id(CHUNKS_TABLE, records.get_by_path(str(files / "blue sky.jpg")).file_id)}
        assert not stored
        print(f"4. Images without text get no text chunks (OCR words found: {words}; threshold {OCR_MIN_WORDS}): OK")

        print(f"5. OCR cost on this PC: {image_seconds:.2f} s per image, {pdf_seconds:.2f} s per scanned page")
        print("\nImprovement 4 OK: scanned PDFs and screenshots are searchable offline through Windows' built-in OCR; photos without text are untouched.")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())  # a normal exit on purpose: it proves OCR leaves nothing that blocks process exit (see app/extraction/ocr.py)
