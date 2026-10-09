"""Image size limit for every Pillow decode in the app (2026-10-04).

Set here because every path that opens a user's image (the photo indexer,
thumbnails, the PDF page images for OCR) imports this package first. A
"decompression bomb" — a small PNG that claims 50,000 x 50,000 pixels —
would otherwise be decoded in full and run the laptop out of memory.
Pillow warns above MAX_IMAGE_PIXELS and refuses above twice that; the
warning is turned into an error too, so such a file fails with a reason
on the Status screen instead of being decoded. 210 MP takes every phone
photo up to 200 MP (16320 x 12240); the photo indexer and thumbnails decode
JPEGs at reduced scale (draft), so those cost a few MB, not 600 MB.
"""

import warnings

from PIL import Image

Image.MAX_IMAGE_PIXELS = 210_000_000
warnings.simplefilter("error", Image.DecompressionBombWarning)

# HEIC/HEIF, the iPhone's photo format (2026-10-05), decoded by pi-heif: the
# decode-only build of pillow-heif (libheif + libde265, LGPL). pillow-heif's
# own Windows wheel also links the x265 encoder, which is GPL. Registered
# here so it is in place before any image is opened; on a machine where its
# native library cannot load, only .heic files fail (with a reason).
try:
    from pi_heif import register_heif_opener

    register_heif_opener()
except (ImportError, OSError) as e:
    import logging

    logging.getLogger(__name__).warning("HEIC support unavailable: %s", e)
