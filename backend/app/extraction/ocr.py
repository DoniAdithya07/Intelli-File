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
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_SIDE = 4000             # images are scaled down to this before OCR (engine limit is 10000; speed)
MIN_SCAN_PIXELS = 300 * 300  # smaller images on a PDF page are decoration, not a scan

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


def _ocr_pdf(engine, path: Path, page_numbers: list[int]) -> dict[int, str]:
    """A scanned PDF is a picture of each page: pypdf pulls the page's
    embedded image(s) out, each is read like any image. Not rendered with
    Windows.Data.Pdf — see the module docstring for why."""
    import tempfile

    from pypdf import PdfReader

    reader = PdfReader(str(path))
    out: dict[int, str] = {}
    with tempfile.TemporaryDirectory(prefix="intellifile_ocr_") as tmp:
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
                picture = image.image
                if picture is None or picture.width * picture.height < MIN_SCAN_PIXELS:
                    continue  # logos, bullets, rules — not a scanned page
                target = Path(tmp) / f"p{number}_{i}.png"
                picture.convert("RGB").save(target)
                texts.append(_ocr_image(engine, target))
            if texts:
                out[number] = "\n".join(t for t in texts if t)
    return out


def ocr_image(path: Path) -> str:
    """Text Windows' OCR finds in an image file; "" when OCR is off."""
    with _lock:
        engine = _load_engine()
        if engine is None:
            return ""
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
