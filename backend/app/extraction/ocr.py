"""Offline text recognition with Windows' own OCR engine (next-round
improvement 4, 2026-09-26).

Windows 10/11 ship `Windows.Media.Ocr` with the recognizers for the
user's display languages — on-device, no model to download or bundle.
Python reaches it through the pywinrt projections (`winrt-*` packages, a
few MB).

Used for:
- scanned PDFs: a page with no text layer is a picture of the page; its
  embedded image is taken out with pypdf and read (pdf_extractor.py), so
  a scanned lecture handout is searchable;
- images: screenshots, photographed whiteboards, scanned receipts. Their
  text is indexed alongside the CLIP photo vectors when there is enough
  of it (Indexer.index_image_text).

Everything degrades to "no OCR" when the engine or the projections are
missing (another OS, a stripped-down Windows): extraction simply behaves
as before. OCR calls are serialized; the engine is created once.

Why not Windows.Data.Pdf to render pages? Found 2026-09-26 by bisection:
in a process that has imported onnxruntime-directml (DirectML/D3D12, the
app's ML runtime), rendering one PDF page with Windows.Data.Pdf leaves the
process unable to exit — every result is correct, then not even os._exit
returns. Image OCR alone with onnxruntime loaded exits cleanly, so pages
go through the image path. The WinRT operations are awaited with their
blocking `.get()`: no event loop needed on the worker thread.
"""

import logging
import shutil
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_SIDE = 4000             # images are scaled down to this before OCR (engine limit is 10000; speed)
MIN_SCAN_PIXELS = 300 * 300  # smaller images on a PDF page are decoration, not a scan
# A scanned page costs ~1-2 s of OCR on a laptop: a 900-page scanned archive
# would hold the indexer for half an hour. Beyond this many scanned pages
# per PDF the rest are left unread (2026-10-04).
MAX_OCR_PAGES = 100

_lock = threading.Lock()
_engine = None
_checked = False


def _load_engine():
    global _engine, _checked
    if _checked:
        return _engine
    _checked = True
    if sys.platform != "win32":
        return None
    try:
        from winrt.windows.media.ocr import OcrEngine

        _engine = OcrEngine.try_create_from_user_profile_languages()
        if _engine is None:
            logger.info("Windows OCR: no recognizer for the user's languages — OCR off")
        else:
            logger.info("Windows OCR ready (%s)", _engine.recognizer_language.language_tag)
    except Exception as e:  # projections not installed, or no WinRT
        logger.info("Windows OCR unavailable (%s) — OCR off", e)
        _engine = None
    return _engine


def available() -> bool:
    with _lock:
        return _load_engine() is not None


def language() -> str | None:
    """The recognizer's language tag (e.g. en-US), or None when OCR is off."""
    with _lock:
        engine = _load_engine()
        return engine.recognizer_language.language_tag if engine is not None else None


def _recognize(engine, bitmap) -> str:
    result = engine.recognize_async(bitmap).get()
    return "\n".join(line.text for line in result.lines)


def _bitmap_from_stream(stream, max_side: int):
    from winrt.windows.graphics.imaging import (
        BitmapAlphaMode, BitmapDecoder, BitmapInterpolationMode, BitmapPixelFormat, BitmapTransform,
        ColorManagementMode, ExifOrientationMode,
    )

    decoder = BitmapDecoder.create_async(stream).get()
    width, height = decoder.oriented_pixel_width, decoder.oriented_pixel_height
    transform = BitmapTransform()
    scale = min(1.0, max_side / max(width, height, 1))
    if scale < 1.0:
        transform.scaled_width = max(1, int(width * scale))
        transform.scaled_height = max(1, int(height * scale))
        transform.interpolation_mode = BitmapInterpolationMode.FANT
    return decoder.get_software_bitmap_transformed_async(
        BitmapPixelFormat.BGRA8, BitmapAlphaMode.PREMULTIPLIED, transform,
        ExifOrientationMode.RESPECT_EXIF_ORIENTATION, ColorManagementMode.DO_NOT_COLOR_MANAGE,
    ).get()


def _ocr_image(engine, path: Path) -> str:
    from winrt.windows.storage import FileAccessMode, StorageFile

    file = StorageFile.get_file_from_path_async(str(path.resolve())).get()
    stream = file.open_async(FileAccessMode.READ).get()
    try:
        bitmap = _bitmap_from_stream(stream, MAX_SIDE)
        text = _recognize(engine, bitmap)
        bitmap.close()
        return text
    finally:
        stream.close()


def temp_dir() -> Path:
    """Where page images wait for OCR: inside the app's own data folder.
    Until 2026-10-04 they went to %TEMP% on drive C, where a crash mid-PDF
    left full-page PNGs behind for good. Cleared at every startup."""
    from ..paths import get_app_data_dir

    return get_app_data_dir() / "cache" / "ocr"


def clear_temp_dir() -> None:
    shutil.rmtree(temp_dir(), ignore_errors=True)


def _ocr_pdf(engine, path: Path, page_numbers: list[int]) -> dict[int, str]:
    """A scanned PDF is a picture of each page: pypdf pulls the page's
    embedded image(s) out, each is read like any image. Not rendered with
    Windows.Data.Pdf — see the module docstring for why."""
    import tempfile

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    out: dict[int, str] = {}
    if len(page_numbers) > MAX_OCR_PAGES:
        logger.warning("%s has %d scanned pages; only the first %d are read with OCR", path, len(page_numbers), MAX_OCR_PAGES)
        page_numbers = page_numbers[:MAX_OCR_PAGES]
    temp_dir().mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="intellifile_ocr_", dir=temp_dir()) as tmp:
        for number in page_numbers:
            if not 1 <= number <= len(reader.pages):
                continue
            try:
                images = list(reader.pages[number - 1].images)
            except Exception:  # an image filter pypdf cannot decode
                logger.debug("No readable images on page %d of %s", number, path, exc_info=True)
                continue
            texts = []
            for i, image in enumerate(images):
                try:
                    picture = image.image
                except Exception:  # undecodable, or over Image.MAX_IMAGE_PIXELS (extraction/__init__.py): skip that picture, keep the PDF's text
                    logger.debug("Skipped an image on page %d of %s", number, path, exc_info=True)
                    continue
                if picture is None or picture.width * picture.height < MIN_SCAN_PIXELS:
                    continue  # logos, bullets, rules — not a scanned page
                target = Path(tmp) / f"p{number}_{i}.png"
                picture.convert("RGB").save(target)
                texts.append(_ocr_image(engine, target))
            if texts:
                out[number] = "\n".join(t for t in texts if t)
    return out


# Decoded by Pillow, page by page, instead of by Windows (2026-10-05): a
# multi-page TIFF (a scanner's or fax's whole document) of which Windows
# reads only page 1, and HEIC, which Windows decodes only with an optional
# Store codec. Pages up to MAX_OCR_PAGES, as for scanned PDFs.
_PILLOW_DECODED = {".tif", ".tiff", ".heic", ".heif"}


def _ocr_pages_with_pillow(engine, path: Path) -> str:
    import tempfile

    from PIL import Image, ImageOps, ImageSequence

    texts = []
    temp_dir().mkdir(parents=True, exist_ok=True)
    with Image.open(path) as image, tempfile.TemporaryDirectory(prefix="intellifile_ocr_", dir=temp_dir()) as tmp:
        for number, page in enumerate(ImageSequence.Iterator(image)):
            if number >= MAX_OCR_PAGES:
                logger.warning("%s has more than %d pages; only the first %d are read with OCR", path, MAX_OCR_PAGES, MAX_OCR_PAGES)
                break
            # (2026-10-05) Pillow checks the pixel cap for page 1 only, and
            # convert("RGB") came first: a 1-bit fax page of 20,000 px became
            # 1.2 GB. Each page is checked, then shrunk in its own mode.
            if Image.MAX_IMAGE_PIXELS and page.width * page.height > Image.MAX_IMAGE_PIXELS:
                logger.warning("%s: page %d is %d x %d pixels, over the limit; not read with OCR", path, number + 1, page.width, page.height)
                continue
            scale = MAX_SIDE / max(page.size)
            small = page.resize((max(1, round(page.width * scale)), max(1, round(page.height * scale)))) if scale < 1 else page
            picture = ImageOps.exif_transpose(small.convert("RGB"))
            target = Path(tmp) / f"p{number}.png"
            picture.save(target)
            texts.append(_ocr_image(engine, target))
    return "\n".join(t for t in texts if t)


def ocr_image(path: Path) -> str:
    """Text Windows' OCR finds in an image file; "" when OCR is off."""
    with _lock:
        engine = _load_engine()
        if engine is None:
            return ""
        if path.suffix.lower() in _PILLOW_DECODED:
            return _ocr_pages_with_pillow(engine, path)
        return _ocr_image(engine, path)


def ocr_pdf_pages(path: Path, page_numbers: list[int]) -> dict[int, str]:
    """{page number: text} for the given 1-based pages of a scanned PDF —
    each page's embedded image read by Windows' OCR; {} when OCR is off."""
    if not page_numbers:
        return {}
    with _lock:
        engine = _load_engine()
        if engine is None:
            return {}
        return _ocr_pdf(engine, path, page_numbers)
